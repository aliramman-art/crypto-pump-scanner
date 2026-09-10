# ============================================================
# VOLUME-KHAT 100 v3.2
# ============================================================
# Kraken Futures
# TOP 100 USD PERPETUAL MARKETS
#
# 1H  = Trend
# 15M = Setup
# 5M  = Confirmation / Entry
#
# CLOSED CANDLES ONLY
# MAX 3 OPEN TRADES
# NO ARTIFICIAL HOURLY SIGNAL LIMIT
# PAPER TRADING
# SQLITE MIGRATION
# ============================================================

import os
import sqlite3
import requests
import traceback
from datetime import datetime, timezone

import pandas as pd
import numpy as np


# ============================================================
# CONFIG
# ============================================================

BASE = "https://futures.kraken.com"

TOP_N = 100

TIMEFRAME_5M = "5m"
TIMEFRAME_15M = "15m"
TIMEFRAME_1H = "1h"

OHLCV_LIMIT = 150

REQUEST_TIMEOUT = 20

# ============================================================
# VOLUME
# ============================================================

RVOL_PERIOD = 20

RVOL_ABNORMAL = 1.70
RVOL_STRONG = 2.50
RVOL_VERY_STRONG = 3.00

# ============================================================
# SETUP
# ============================================================

BREAKOUT_LOOKBACK = 5

SUPPORT_RESISTANCE_LOOKBACK = 20

REJECTION_DISTANCE = 0.0080

WICK_BODY_RATIO = 1.15

# ============================================================
# RISK
# ============================================================

ATR_PERIOD = 14

ATR_SL_BUFFER = 0.15

MAX_SL_PCT = 0.0080

MIN_RR = 2.0

# ============================================================
# 5M CONFIRMATION
# ============================================================

MIN_BODY_RATIO = 0.30

# ============================================================
# SCORE
# ============================================================

MIN_SCORE = 9

# ============================================================
# TRADE MANAGEMENT
# ============================================================

MAX_OPEN_TRADES = 3

MAX_NEW_SIGNALS = 3

COOLDOWN_CANDLES = 3

PAPER_TRADING = True

# ============================================================
# DATABASE
# ============================================================

DB_FILE = "volume_khat_100.db"

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
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "VOLUME-KHAT-100/3.2",
    "Accept": "application/json",
})


# ============================================================
# DIAGNOSTICS
# ============================================================

DIAG = {
    "markets": 0,
    "data_ok": 0,
    "trend": 0,
    "setup": 0,
    "confirmation": 0,
    "valid_sl_tp": 0,
    "score_9": 0,
    "final": 0,
    "neutral": 0,
    "no_setup": 0,
    "no_confirmation": 0,
    "invalid_sltp": 0,
    "low_score": 0,
    "data_errors": 0,
}

DATA_ERROR_TYPES = {}


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


# ============================================================
# HTTP GET
# ============================================================

def http_get(
    url,
    params=None,
    timeout=REQUEST_TIMEOUT
):

    try:

        response = SESSION.get(
            url,
            params=params,
            timeout=timeout
        )

        status = response.status_code

        if status != 200:

            text = response.text[:200]

            raise RuntimeError(
                f"HTTP {status}: {text}"
            )

        try:

            return response.json()

        except Exception:

            raise RuntimeError(
                "INVALID JSON"
            )

    except requests.exceptions.Timeout:

        raise RuntimeError(
            "TIMEOUT"
        )

    except requests.exceptions.ConnectionError:

        raise RuntimeError(
            "CONNECTION ERROR"
        )

    except requests.exceptions.RequestException as e:

        raise RuntimeError(
            f"REQUEST ERROR: {str(e)[:100]}"
        )


# ============================================================
# DATABASE CONNECTION
# ============================================================

def db_connect():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.execute(
        "PRAGMA journal_mode=WAL"
    )

    conn.execute(
        "PRAGMA busy_timeout=30000"
    )

    return conn


# ============================================================
# DATABASE INITIALIZATION + MIGRATION
# ============================================================

def init_db():

    conn = db_connect()
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

    conn.commit()

    # --------------------------------------------------------
    # MIGRATION
    # --------------------------------------------------------

    cur.execute(
        "PRAGMA table_info(trades)"
    )

    existing_columns = {
        row[1]
        for row in cur.fetchall()
    }

    migrations = {

        "symbol": "TEXT",

        "side": "TEXT",

        "setup": "TEXT",

        "entry": "REAL",

        "sl": "REAL",

        "tp": "REAL",

        "rr": "REAL",

        "score": "REAL",

        "status": "TEXT",

        "result": "TEXT",

        "pnl": "REAL",

        "entry_time": "TEXT",

        "exit_time": "TEXT",

        "exit_reason": "TEXT",

        "closed_reported":
            "INTEGER DEFAULT 0",

        "created_at": "TEXT",
    }

    for column, definition in migrations.items():

        if column not in existing_columns:

            try:

                cur.execute(
                    f"""
                    ALTER TABLE trades
                    ADD COLUMN {column}
                    {definition}
                    """
                )

                print(
                    f"DB MIGRATION: "
                    f"added column {column}"
                )

            except Exception as e:

                print(
                    f"DB MIGRATION ERROR "
                    f"{column}: {e}"
                )

    # --------------------------------------------------------
    # Repair old NULL values
    # --------------------------------------------------------

    cur.execute("""
        UPDATE trades
        SET closed_reported = 0
        WHERE closed_reported IS NULL
    """)

    cur.execute("""
        UPDATE trades
        SET status = 'OPEN'
        WHERE status IS NULL
    """)

    cur.execute("""
        UPDATE trades
        SET created_at =
            COALESCE(
                created_at,
                entry_time,
                ?
            )
        WHERE created_at IS NULL
    """, (
        utc_iso(),
    ))

    # --------------------------------------------------------
    # INDEXES
    # --------------------------------------------------------

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

    cur.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_trades_entry_time
        ON trades(entry_time)
    """)

    conn.commit()
    conn.close()


# ============================================================
# MARKET DISCOVERY
# ============================================================

def discover_markets():

    url = (
        f"{BASE}/"
        "derivatives/api/v3/instruments"
    )

    try:

        data = http_get(url)

        instruments = data.get(
            "instruments",
            []
        )

        markets = []

        for item in instruments:

            symbol = (
                item.get("symbol")
                or item.get("instrument")
                or item.get("ticker")
            )

            if not symbol:
                continue

            symbol = str(symbol)

            if not symbol.startswith("PF_"):
                continue

            if not symbol.endswith("USD"):
                continue

            markets.append(symbol)

        return sorted(
            set(markets)
        )

    except Exception as e:

        print(
            "MARKET DISCOVERY ERROR:",
            e
        )

        return []


# ============================================================
# TICKER VOLUMES
# ============================================================

def get_ticker_volumes():

    url = (
        f"{BASE}/"
        "derivatives/api/v3/tickers"
    )

    result = {}

    try:

        data = http_get(url)

        tickers = data.get(
            "tickers",
            []
        )

        for item in tickers:

            symbol = (
                item.get("symbol")
                or item.get("tag")
                or item.get("instrument")
            )

            if not symbol:
                continue

            symbol = str(symbol)

            if not symbol.startswith("PF_"):
                continue

            if not symbol.endswith("USD"):
                continue

            volume = (
                item.get("vol24h")
                or item.get("volume24h")
                or item.get("volume")
                or 0
            )

            last = (
                item.get("last")
                or item.get("lastPrice")
                or item.get("price")
            )

            try:

                volume = float(volume)

            except Exception:

                volume = 0.0

            try:

                last = (
                    float(last)
                    if last is not None
                    else None
                )

            except Exception:

                last = None

            result[symbol] = {
                "volume": volume,
                "last": last,
            }

        return result

    except Exception as e:

        print(
            "TICKER ERROR:",
            e
        )

        return {}


# ============================================================
# TOP MARKETS
# ============================================================

def get_top_markets():

    markets = discover_markets()

    if not markets:
        return []

    ticker_data = get_ticker_volumes()

    ranked = []

    for symbol in markets:

        info = ticker_data.get(
            symbol
        )

        if not info:
            continue

        ranked.append(
            (
                symbol,
                float(
                    info.get(
                        "volume",
                        0
                    )
                )
            )
        )

    ranked.sort(
        key=lambda x: x[1],
        reverse=True
    )

    return [
        item[0]
        for item in ranked[:TOP_N]
    ]


# ============================================================
# OHLCV
# ============================================================
#
# IMPORTANT:
# Kraken Futures official endpoint:
#
# GET
# /api/charts/v1/:tick_type/:symbol/:resolution
#
# Example:
# /api/charts/v1/trade/PF_XBTUSD/5m
#
# The previous version incorrectly sent symbol and interval
# as query parameters. This version fixes that.
#
# ============================================================

def fetch_ohlcv(
    symbol,
    interval
):

    url = (
        f"{BASE}/"
        f"api/charts/v1/"
        f"trade/"
        f"{symbol}/"
        f"{interval}"
    )

    params = {
        "count": OHLCV_LIMIT
    }

    try:

        data = http_get(
            url,
            params=params
        )

        # ----------------------------------------------------
        # Kraken response
        # ----------------------------------------------------

        if not isinstance(data, dict):

            raise RuntimeError(
                "INVALID RESPONSE TYPE"
            )

        rows = data.get(
            "candles"
        )

        if rows is None:

            # Fallback for alternate response shape
            rows = data.get(
                "data"
            )

        if rows is None:

            rows = data.get(
                "results"
            )

        if not rows:

            raise RuntimeError(
                "EMPTY DATA"
            )

        parsed = []

        for row in rows:

            try:

                if not isinstance(
                    row,
                    dict
                ):

                    continue

                ts = row.get(
                    "time"
                )

                op = row.get(
                    "open"
                )

                hi = row.get(
                    "high"
                )

                lo = row.get(
                    "low"
                )

                cl = row.get(
                    "close"
                )

                vol = row.get(
                    "volume",
                    0
                )

                if (
                    ts is None
                    or op is None
                    or hi is None
                    or lo is None
                    or cl is None
                ):

                    continue

                ts = float(ts)

                # Kraken documentation returns
                # candle time in milliseconds.
                if ts > 10_000_000_000:

                    ts /= 1000.0

                parsed.append({

                    "timestamp":
                        datetime.fromtimestamp(
                            ts,
                            tz=timezone.utc
                        ),

                    "open":
                        float(op),

                    "high":
                        float(hi),

                    "low":
                        float(lo),

                    "close":
                        float(cl),

                    "volume":
                        float(vol),
                })

            except Exception:

                continue

        if not parsed:

            raise RuntimeError(
                "PARSE ERROR: "
                "NO VALID CANDLES"
            )

        df = pd.DataFrame(
            parsed
        )

        df = df.drop_duplicates(
            subset=["timestamp"]
        )

        df = df.sort_values(
            "timestamp"
        ).reset_index(
            drop=True
        )

        # ----------------------------------------------------
        # Remove latest candle.
        # Strategy uses CLOSED candles only.
        # ----------------------------------------------------

        if len(df) > 1:

            df = df.iloc[:-1].copy()

        # ----------------------------------------------------
        # Minimum data
        # ----------------------------------------------------

        if len(df) < 60:

            raise RuntimeError(
                f"INSUFFICIENT DATA "
                f"({len(df)})"
            )

        return (
            df
            .tail(OHLCV_LIMIT)
            .reset_index(drop=True)
        )

    except Exception as e:

        error_type = str(e)

        DATA_ERROR_TYPES[
            error_type
        ] = (
            DATA_ERROR_TYPES.get(
                error_type,
                0
            ) + 1
        )

        raise


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    df,
    period=ATR_PERIOD
):

    high = df["high"]

    low = df["low"]

    close = df["close"]

    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,

            (
                high - prev_close
            ).abs(),

            (
                low - prev_close
            ).abs(),
        ],
        axis=1
    ).max(
        axis=1
    )

    return tr.rolling(
        period
    ).mean()


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(df):

    if len(df) < (
        RVOL_PERIOD + 2
    ):

        return 0.0

    volume = df["volume"]

    baseline = (
        volume
        .shift(1)
        .rolling(
            RVOL_PERIOD
        )
        .mean()
    )

    current = float(
        volume.iloc[-1]
    )

    base = baseline.iloc[-1]

    if (
        pd.isna(base)
        or base <= 0
    ):

        return 0.0

    return float(
        current / base
    )


# ============================================================
# SUPPORT
# ============================================================

def recent_support(df):

    if len(df) < 10:
        return None

    start = max(
        0,
        len(df)
        - SUPPORT_RESISTANCE_LOOKBACK
        - 1
    )

    values = df[
        "low"
    ].iloc[
        start:-1
    ]

    if len(values) == 0:
        return None

    return float(
        values.min()
    )


# ============================================================
# RESISTANCE
# ============================================================

def recent_resistance(df):

    if len(df) < 10:
        return None

    start = max(
        0,
        len(df)
        - SUPPORT_RESISTANCE_LOOKBACK
        - 1
    )

    values = df[
        "high"
    ].iloc[
        start:-1
    ]

    if len(values) == 0:
        return None

    return float(
        values.max()
    )


# ============================================================
# TREND
# ============================================================

def get_trend(df1h):

    if len(df1h) < 55:
        return "NEUTRAL"

    close = df1h[
        "close"
    ]

    sma20 = (
        close
        .rolling(20)
        .mean()
    )

    sma50 = (
        close
        .rolling(50)
        .mean()
    )

    c = float(
        close.iloc[-1]
    )

    s20 = float(
        sma20.iloc[-1]
    )

    s50 = float(
        sma50.iloc[-1]
    )

    if (
        c > s20
        and s20 > s50
    ):

        return "LONG"

    if (
        c < s20
        and s20 < s50
    ):

        return "SHORT"

    return "NEUTRAL"


# ============================================================
# CANDLE METRICS
# ============================================================

def candle_metrics(row):

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

    total = max(
        h - l,
        1e-12
    )

    body = abs(
        c - o
    )

    upper_wick = (
        h - max(o, c)
    )

    lower_wick = (
        min(o, c) - l
    )

    return {

        "open": o,

        "high": h,

        "low": l,

        "close": c,

        "body": body,

        "upper_wick":
            max(
                0.0,
                upper_wick
            ),

        "lower_wick":
            max(
                0.0,
                lower_wick
            ),

        "body_ratio":
            body / total,
    }


# ============================================================
# BREAKOUT SETUP
# ============================================================

def detect_breakout_setup(
    df15,
    trend
):

    if len(df15) < (
        BREAKOUT_LOOKBACK + 5
    ):

        return None

    last = candle_metrics(
        df15.iloc[-1]
    )

    previous = df15.iloc[
        -(BREAKOUT_LOOKBACK + 1):-1
    ]

    previous_high = float(
        previous["high"].max()
    )

    previous_low = float(
        previous["low"].min()
    )

    rvol = calculate_rvol(
        df15
    )

    # LONG
    if (
        trend == "LONG"
        and last["close"]
            > previous_high
        and rvol >= RVOL_ABNORMAL
    ):

        return {
            "side": "LONG",
            "setup": "BREAKOUT",
            "rvol": rvol,
        }

    # SHORT
    if (
        trend == "SHORT"
        and last["close"]
            < previous_low
        and rvol >= RVOL_ABNORMAL
    ):

        return {
            "side": "SHORT",
            "setup": "BREAKOUT",
            "rvol": rvol,
        }

    return None


# ============================================================
# REJECTION SETUP
# ============================================================

def detect_rejection_setup(
    df15,
    trend
):

    if len(df15) < 25:
        return None

    last = candle_metrics(
        df15.iloc[-1]
    )

    close = last["close"]

    support = recent_support(
        df15
    )

    resistance = recent_resistance(
        df15
    )

    rvol = calculate_rvol(
        df15
    )

    # LONG rejection
    if (
        trend == "LONG"

        and support is not None

        and abs(
            close - support
        ) / close
        <= REJECTION_DISTANCE

        and last["lower_wick"]
        >= (
            max(
                last["body"],
                1e-12
            )
            * WICK_BODY_RATIO
        )

        and close > last["open"]

        and rvol >= RVOL_ABNORMAL
    ):

        return {
            "side": "LONG",
            "setup": "REJECTION",
            "rvol": rvol,
        }

    # SHORT rejection
    if (
        trend == "SHORT"

        and resistance is not None

        and abs(
            close - resistance
        ) / close
        <= REJECTION_DISTANCE

        and last["upper_wick"]
        >= (
            max(
                last["body"],
                1e-12
            )
            * WICK_BODY_RATIO
        )

        and close < last["open"]

        and rvol >= RVOL_ABNORMAL
    ):

        return {
            "side": "SHORT",
            "setup": "REJECTION",
            "rvol": rvol,
        }

    return None


# ============================================================
# DETECT SETUP
# ============================================================

def detect_setup(
    df15,
    trend
):

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

    if len(df5) < 5:
        return False

    last = candle_metrics(
        df5.iloc[-1]
    )

    if (
        last["body_ratio"]
        < MIN_BODY_RATIO
    ):

        return False

    if side == "LONG":

        return (
            last["close"]
            > last["open"]
        )

    if side == "SHORT":

        return (
            last["close"]
            < last["open"]
        )

    return False


# ============================================================
# STRUCTURAL SL
# ============================================================

def calculate_structural_sl(
    df15,
    df1h,
    side,
    entry
):

    atr_series = calculate_atr(
        df15
    )

    atr15 = atr_series.iloc[-1]

    if (
        pd.isna(atr15)
        or atr15 <= 0
    ):

        atr15 = (
            entry * 0.003
        )

    support15 = recent_support(
        df15
    )

    resistance15 = recent_resistance(
        df15
    )

    support1h = recent_support(
        df1h
    )

    resistance1h = recent_resistance(
        df1h
    )

    if side == "LONG":

        candidates = []

        for value in [
            support15,
            support1h
        ]:

            if (
                value is not None
                and value < entry
            ):

                candidates.append(
                    value
                )

        if candidates:

            base = max(
                candidates
            )

            sl = (
                base
                - atr15
                * ATR_SL_BUFFER
            )

        else:

            sl = (
                entry
                - atr15
            )

        return float(sl)

    if side == "SHORT":

        candidates = []

        for value in [
            resistance15,
            resistance1h
        ]:

            if (
                value is not None
                and value > entry
            ):

                candidates.append(
                    value
                )

        if candidates:

            base = min(
                candidates
            )

            sl = (
                base
                + atr15
                * ATR_SL_BUFFER
            )

        else:

            sl = (
                entry
                + atr15
            )

        return float(sl)

    return None


# ============================================================
# TAKE PROFIT
# ============================================================

def calculate_tp(
    df15,
    side,
    entry,
    sl
):

    risk = abs(
        entry - sl
    )

    if risk <= 0:
        return None, None

    resistance = recent_resistance(
        df15
    )

    support = recent_support(
        df15
    )

    if side == "LONG":

        structural_tp = None

        if (
            resistance is not None
            and resistance > entry
        ):

            structural_tp = resistance

        min_tp = (
            entry
            + risk * MIN_RR
        )

        if (
            structural_tp is not None
            and structural_tp >= min_tp
        ):

            tp = structural_tp

        else:

            tp = min_tp

    else:

        structural_tp = None

        if (
            support is not None
            and support < entry
        ):

            structural_tp = support

        min_tp = (
            entry
            - risk * MIN_RR
        )

        if (
            structural_tp is not None
            and structural_tp <= min_tp
        ):

            tp = structural_tp

        else:

            tp = min_tp

    rr = (
        abs(tp - entry)
        / risk
    )

    return (
        float(tp),
        float(rr)
    )


# ============================================================
# BUILD TRADE
# ============================================================

def build_trade(
    df5,
    df15,
    df1h,
    side,
    setup,
    rvol
):

    entry = float(
        df5["close"].iloc[-1]
    )

    sl = calculate_structural_sl(
        df15,
        df1h,
        side,
        entry
    )

    if sl is None:
        return None

    # SL direction
    if (
        side == "LONG"
        and sl >= entry
    ):

        return None

    if (
        side == "SHORT"
        and sl <= entry
    ):

        return None

    sl_pct = (
        abs(
            entry - sl
        )
        / entry
    )

    if sl_pct > MAX_SL_PCT:

        return None

    tp, rr = calculate_tp(
        df15,
        side,
        entry,
        sl
    )

    if tp is None:
        return None

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

    if rr < MIN_RR:

        return None

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = 0

    # Trend
    score += 3

    # Setup
    score += 4

    # Volume
    if rvol >= RVOL_VERY_STRONG:

        score += 4

    elif rvol >= RVOL_STRONG:

        score += 4

    elif rvol >= RVOL_ABNORMAL:

        score += 3

    # 5M confirmation
    score += 4

    return {

        "side": side,

        "setup": setup,

        "entry": entry,

        "sl": float(sl),

        "tp": float(tp),

        "rr": float(rr),

        "score": float(score),

        "rvol": float(rvol),

        "sl_pct": float(sl_pct),
    }


# ============================================================
# GET OPEN TRADES
# ============================================================

def get_open_trades():

    conn = db_connect()
    cur = conn.cursor()

    try:

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
                exit_reason,
                closed_reported,
                created_at
            FROM trades
            WHERE status = 'OPEN'
            ORDER BY id ASC
        """)

        rows = cur.fetchall()

    finally:

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
        "exit_reason",
        "closed_reported",
        "created_at",
    ]

    return [
        dict(
            zip(
                columns,
                row
            )
        )
        for row in rows
    ]


# ============================================================
# HAS OPEN TRADE
# ============================================================

def has_open_trade(
    symbol
):

    conn = db_connect()
    cur = conn.cursor()

    try:

        cur.execute("""
            SELECT COUNT(*)
            FROM trades
            WHERE symbol = ?
              AND status = 'OPEN'
        """, (
            symbol,
        ))

        count = cur.fetchone()[0]

    finally:

        conn.close()

    return count > 0


# ============================================================
# RECENTLY TRADED
# ============================================================

def recently_traded(
    symbol
):

    conn = db_connect()
    cur = conn.cursor()

    try:

        cur.execute("""
            SELECT entry_time
            FROM trades
            WHERE symbol = ?
            ORDER BY id DESC
            LIMIT 1
        """, (
            symbol,
        ))

        row = cur.fetchone()

    finally:

        conn.close()

    if not row:
        return False

    if not row[0]:
        return False

    try:

        entry_time = datetime.strptime(
            row[0],
            "%Y-%m-%d %H:%M:%S UTC"
        ).replace(
            tzinfo=timezone.utc
        )

        elapsed = (
            utc_now()
            - entry_time
        ).total_seconds()

        cooldown_seconds = (
            COOLDOWN_CANDLES
            * 5
            * 60
        )

        return (
            elapsed
            < cooldown_seconds
        )

    except Exception:

        return False


# ============================================================
# SAVE TRADE
# ============================================================

def save_trade(
    symbol,
    trade
):

    conn = db_connect()
    cur = conn.cursor()

    try:

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
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
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

            utc_iso(),

            None,

            None,

            0,

            utc_iso(),
        ))

        conn.commit()

        return cur.lastrowid

    finally:

        conn.close()


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade_id,
    exit_price,
    reason
):

    conn = db_connect()
    cur = conn.cursor()

    try:

        cur.execute("""
            SELECT
                entry,
                side
            FROM trades
            WHERE id = ?
        """, (
            trade_id,
        ))

        row = cur.fetchone()

        if not row:
            return

        entry = float(
            row[0]
        )

        side = row[1]

        if side == "LONG":

            pnl = (
                (
                    exit_price
                    - entry
                )
                / entry
            ) * 100

        else:

            pnl = (
                (
                    entry
                    - exit_price
                )
                / entry
            ) * 100

        result = (
            "WIN"
            if pnl > 0
            else "LOSS"
        )

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
        """, (

            result,

            float(pnl),

            utc_iso(),

            reason,

            trade_id,
        ))

        conn.commit()

    finally:

        conn.close()


# ============================================================
# LIVE PRICES
# ============================================================

def get_live_prices():

    url = (
        f"{BASE}/"
        "derivatives/api/v3/tickers"
    )

    prices = {}

    try:

        data = http_get(
            url
        )

        tickers = data.get(
            "tickers",
            []
        )

        for item in tickers:

            symbol = (
                item.get("symbol")
                or item.get("tag")
                or item.get("instrument")
            )

            if not symbol:
                continue

            last = (
                item.get("last")
                or item.get("lastPrice")
                or item.get("price")
            )

            try:

                prices[
                    str(symbol)
                ] = float(last)

            except Exception:

                continue

        return prices

    except Exception as e:

        print(
            "LIVE PRICE ERROR:",
            e
        )

        return {}


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades():

    open_trades = get_open_trades()

    if not open_trades:
        return

    for trade in open_trades:

        symbol = trade[
            "symbol"
        ]

        try:

            df5 = fetch_ohlcv(
                symbol,
                TIMEFRAME_5M
            )

            candle = df5.iloc[-1]

            candle_high = float(
                candle["high"]
            )

            candle_low = float(
                candle["low"]
            )

            sl = float(
                trade["sl"]
            )

            tp = float(
                trade["tp"]
            )

            # ------------------------------------------------
            # Conservative SL first
            # ------------------------------------------------

            if trade["side"] == "LONG":

                if candle_low <= sl:

                    close_trade(
                        trade["id"],
                        sl,
                        "SL"
                    )

                elif candle_high >= tp:

                    close_trade(
                        trade["id"],
                        tp,
                        "TP"
                    )

            else:

                if candle_high >= sl:

                    close_trade(
                        trade["id"],
                        sl,
                        "SL"
                    )

                elif candle_low <= tp:

                    close_trade(
                        trade["id"],
                        tp,
                        "TP"
                    )

        except Exception as e:

            print(
                f"Open trade update error "
                f"{symbol}: {e}"
            )


# ============================================================
# MAX OPEN CLEANUP
# ============================================================

def enforce_max_open_trades():

    try:

        open_trades = get_open_trades()

        if len(open_trades) <= MAX_OPEN_TRADES:

            return

        excess = open_trades[
            MAX_OPEN_TRADES:
        ]

        conn = db_connect()
        cur = conn.cursor()

        try:

            for trade in excess:

                cur.execute("""
                    UPDATE trades
                    SET
                        status = 'CLOSED',
                        result = 'REMOVED',
                        pnl = 0,
                        exit_time = ?,
                        exit_reason =
                            'MAX_OPEN_CLEANUP',
                        closed_reported = 1
                    WHERE id = ?
                      AND status = 'OPEN'
                """, (

                    utc_iso(),

                    trade["id"],
                ))

            conn.commit()

        finally:

            conn.close()

    except Exception as e:

        print(
            "Max open cleanup error:",
            e
        )


# ============================================================
# STATS
# ============================================================

def get_stats():

    conn = db_connect()
    cur = conn.cursor()

    try:

        cur.execute("""
            SELECT COUNT(*)
            FROM trades
            WHERE status = 'OPEN'
        """)

        open_count = int(
            cur.fetchone()[0]
        )

        cur.execute("""
            SELECT COUNT(*)
            FROM trades
            WHERE status = 'CLOSED'
              AND result IN ('WIN', 'LOSS')
        """)

        closed_count = int(
            cur.fetchone()[0]
        )

        cur.execute("""
            SELECT COUNT(*)
            FROM trades
            WHERE status = 'CLOSED'
              AND result = 'WIN'
        """)

        wins = int(
            cur.fetchone()[0]
        )

        cur.execute("""
            SELECT COUNT(*)
            FROM trades
            WHERE status = 'CLOSED'
              AND result = 'LOSS'
        """)

        losses = int(
            cur.fetchone()[0]
        )

        cur.execute("""
            SELECT COALESCE(
                SUM(pnl),
                0
            )
            FROM trades
            WHERE status = 'CLOSED'
              AND result IN ('WIN', 'LOSS')
        """)

        realized_pnl = float(
            cur.fetchone()[0] or 0
        )

    finally:

        conn.close()

    if closed_count:

        win_rate = (
            wins
            / closed_count
        ) * 100

    else:

        win_rate = 0.0

    return {

        "open":
            open_count,

        "closed":
            closed_count,

        "wins":
            wins,

        "losses":
            losses,

        "win_rate":
            win_rate,

        "realized_pnl":
            realized_pnl,
    }


# ============================================================
# UNREPORTED CLOSED
# ============================================================

def get_unreported_closed():

    conn = db_connect()
    cur = conn.cursor()

    try:

        cur.execute("""
            SELECT
                id,
                symbol,
                side,
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
              AND result IN ('WIN', 'LOSS')
              AND closed_reported = 0
            ORDER BY id ASC
        """)

        rows = cur.fetchall()

    finally:

        conn.close()

    return rows


# ============================================================
# MARK REPORTED
# ============================================================

def mark_closed_reported():

    conn = db_connect()
    cur = conn.cursor()

    try:

        cur.execute("""
            UPDATE trades
            SET closed_reported = 1
            WHERE status = 'CLOSED'
              AND result IN ('WIN', 'LOSS')
              AND closed_reported = 0
        """)

        conn.commit()

    finally:

        conn.close()


# ============================================================
# FORMAT PRICE
# ============================================================

def fmt_price(value):

    if value is None:
        return "-"

    value = float(value)

    if abs(value) >= 1000:

        return f"{value:.2f}"

    if abs(value) >= 1:

        return (
            f"{value:.6f}"
            .rstrip("0")
            .rstrip(".")
        )

    if abs(value) >= 0.01:

        return (
            f"{value:.6f}"
            .rstrip("0")
            .rstrip(".")
        )

    return (
        f"{value:.10f}"
        .rstrip("0")
        .rstrip(".")
    )


# ============================================================
# DATA ERROR SUMMARY
# ============================================================

def diagnostic_error_summary():

    if not DATA_ERROR_TYPES:

        return "None"

    items = sorted(
        DATA_ERROR_TYPES.items(),
        key=lambda x: x[1],
        reverse=True
    )

    return " | ".join(
        f"{error}: {count}"
        for error, count
        in items[:8]
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(
    text
):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "TELEGRAM_BOT_TOKEN missing"
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "TELEGRAM_CHAT_ID missing"
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/"
        "sendMessage"
    )

    payload = {

        "chat_id":
            TELEGRAM_CHAT_ID,

        "text":
            text,
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=20
        )

        if response.status_code != 200:

            print(
                "TELEGRAM ERROR:",
                response.status_code,
                response.text[:300]
            )

            return False

        return True

    except Exception as e:

        print(
            "TELEGRAM EXCEPTION:",
            e
        )

        return False


# ============================================================
# BUILD REPORT
# ============================================================

def build_report(
    markets_count,
    signal_list,
    closed_rows,
    open_trades,
    prices
):

    stats = get_stats()

    lines = []

    lines.append(
        "📊 VOLUME-KHAT 100"
    )

    lines.append(
        f"🕐 {utc_iso()}"
    )

    lines.append(
        "⚡ Kraken Futures | 5M CLOSED"
    )

    lines.append(
        f"🔎 Markets: {markets_count} | "
        f"Signals: {len(signal_list)}"
    )

    lines.append("")

    lines.append(
        "📈 PERFORMANCE"
    )

    lines.append(
        f"Open {stats['open']}/"
        f"{MAX_OPEN_TRADES} | "
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
        "🔬 DIAGNOSTIC"
    )

    lines.append(
        f"Markets {DIAG['markets']} | "
        f"Data OK {DIAG['data_ok']}"
    )

    lines.append(
        f"Trend {DIAG['trend']} | "
        f"Setup {DIAG['setup']}"
    )

    lines.append(
        f"Confirmation "
        f"{DIAG['confirmation']} | "
        f"Valid SL/TP "
        f"{DIAG['valid_sl_tp']}"
    )

    lines.append(
        f"Score ≥ 9: "
        f"{DIAG['score_9']} | "
        f"Final: "
        f"{DIAG['final']}"
    )

    lines.append(
        f"Neutral "
        f"{DIAG['neutral']} | "
        f"No Setup "
        f"{DIAG['no_setup']}"
    )

    lines.append(
        f"No 5M Confirm "
        f"{DIAG['no_confirmation']} | "
        f"Invalid SL/TP "
        f"{DIAG['invalid_sltp']}"
    )

    lines.append(
        f"Low Score "
        f"{DIAG['low_score']} | "
        f"Data Errors "
        f"{DIAG['data_errors']}"
    )

    # --------------------------------------------------------
    # Error summary
    # --------------------------------------------------------

    if DIAG["data_errors"] > 0:

        lines.append("")

        lines.append(
            "⚠️ DATA ERROR SUMMARY"
        )

        lines.append(
            diagnostic_error_summary()
        )

    # --------------------------------------------------------
    # New signals
    # --------------------------------------------------------

    if signal_list:

        lines.append("")

        lines.append(
            "🚨 NEW SIGNALS"
        )

        for item in signal_list:

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

            icon = (
                "🟢"
                if item["side"] == "LONG"
                else "🔴"
            )

            lines.append(
                f"{icon} "
                f"{item['symbol']} "
                f"{item['side']}"
            )

            lines.append(
                f"Setup {item['setup']} | "
                f"Score "
                f"{item['score']:.0f}/15"
            )

            lines.append(
                f"Entry "
                f"{fmt_price(item['entry'])}"
            )

            lines.append(
                f"SL "
                f"{fmt_price(item['sl'])} "
                f"({item['sl_pct']*100:.2f}%)"
            )

            lines.append(
                f"TP "
                f"{fmt_price(item['tp'])} "
                f"| RR 1:{item['rr']:.2f}"
            )

            lines.append(
                f"RVOL "
                f"{item['rvol']:.2f}x"
            )

    # --------------------------------------------------------
    # Closed
    # --------------------------------------------------------

    if closed_rows:

        lines.append("")

        lines.append(
            "🔔 CLOSED"
        )

        for row in closed_rows:

            (
                trade_id,
                symbol,
                side,
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
            ) = row

            icon = (
                "✅"
                if result == "WIN"
                else "❌"
            )

            if exit_reason == "SL":

                exit_price = sl

            else:

                exit_price = tp

            lines.append(
                f"{icon} "
                f"{symbol} "
                f"{side} "
                f"{result} "
                f"{pnl:+.2f}%"
            )

            lines.append(
                f"Entry "
                f"{fmt_price(entry)} "
                f"→ Exit "
                f"{fmt_price(exit_price)}"
            )

    # --------------------------------------------------------
    # Open trades
    # --------------------------------------------------------

    if open_trades:

        lines.append("")

        lines.append(
            "📂 OPEN TRADES"
        )

        for trade in open_trades:

            symbol = trade[
                "symbol"
            ]

            entry = float(
                trade["entry"]
            )

            price = prices.get(
                symbol,
                entry
            )

            if trade["side"] == "LONG":

                pnl = (
                    (
                        price
                        - entry
                    )
                    / entry
                ) * 100

            else:

                pnl = (
                    (
                        entry
                        - price
                    )
                    / entry
                ) * 100

            icon = (
                "🟢"
                if pnl >= 0
                else "🔴"
            )

            lines.append(
                f"{icon} "
                f"{symbol} "
                f"{trade['side']} "
                f"PnL {pnl:+.2f}%"
            )

            lines.append(
                f"Entry "
                f"{fmt_price(entry)} | "
                f"Now "
                f"{fmt_price(price)}"
            )

            lines.append(
                f"SL "
                f"{fmt_price(trade['sl'])} | "
                f"TP "
                f"{fmt_price(trade['tp'])} | "
                f"RR 1:"
                f"{float(trade['rr']):.2f}"
            )

    lines.append("")

    lines.append(
        "🧪 PAPER TRADING"
    )

    return "\n".join(
        lines
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)

    print(
        "VOLUME-KHAT 100 v3.2"
    )

    print(
        "Kraken Futures | 5M CLOSED"
    )

    print("=" * 60)

    # --------------------------------------------------------
    # DB
    # --------------------------------------------------------

    init_db()

    # --------------------------------------------------------
    # Update open trades
    # --------------------------------------------------------

    try:

        update_open_trades()

    except Exception as e:

        print(
            "Open trade update error:",
            e
        )

    # --------------------------------------------------------
    # Enforce max open
    # --------------------------------------------------------

    try:

        enforce_max_open_trades()

    except Exception as e:

        print(
            "Max open cleanup error:",
            e
        )

    # --------------------------------------------------------
    # Reset diagnostics
    # --------------------------------------------------------

    for key in DIAG:

        DIAG[key] = 0

    DATA_ERROR_TYPES.clear()

    # --------------------------------------------------------
    # Discover TOP 100
    # --------------------------------------------------------

    print(
        "Discovering TOP 100 markets..."
    )

    markets = get_top_markets()

    if not markets:

        print(
            "ERROR: "
            "No markets discovered"
        )

        report = build_report(
            0,
            [],
            get_unreported_closed(),
            get_open_trades(),
            get_live_prices()
        )

        send_telegram(
            report
        )

        return

    markets = markets[
        :TOP_N
    ]

    DIAG["markets"] = len(
        markets
    )

    print(
        f"Markets discovered: "
        f"{len(markets)}"
    )

    # --------------------------------------------------------
    # Existing open trades
    # --------------------------------------------------------

    open_trades = get_open_trades()

    available_slots = max(
        0,
        MAX_OPEN_TRADES
        - len(open_trades)
    )

    print(
        f"Open trades: "
        f"{len(open_trades)}"
    )

    print(
        f"Available slots: "
        f"{available_slots}"
    )

    # --------------------------------------------------------
    # SCAN
    # --------------------------------------------------------

    signal_list = []

    for index, symbol in enumerate(
        markets,
        1
    ):

        print(
            f"[{index}/{len(markets)}] "
            f"{symbol}"
        )

        try:

            # ------------------------------------------------
            # DATA
            # ------------------------------------------------

            df1h = fetch_ohlcv(
                symbol,
                TIMEFRAME_1H
            )

            df15 = fetch_ohlcv(
                symbol,
                TIMEFRAME_15M
            )

            df5 = fetch_ohlcv(
                symbol,
                TIMEFRAME_5M
            )

            DIAG["data_ok"] += 1

            # ------------------------------------------------
            # TREND
            # ------------------------------------------------

            trend = get_trend(
                df1h
            )

            if trend == "NEUTRAL":

                DIAG["neutral"] += 1

                continue

            DIAG["trend"] += 1

            # ------------------------------------------------
            # SETUP
            # ------------------------------------------------

            setup = detect_setup(
                df15,
                trend
            )

            if not setup:

                DIAG["no_setup"] += 1

                continue

            DIAG["setup"] += 1

            side = setup[
                "side"
            ]

            # ------------------------------------------------
            # 5M CONFIRMATION
            # ------------------------------------------------

            if not confirm_5m(
                df5,
                side
            ):

                DIAG[
                    "no_confirmation"
                ] += 1

                continue

            DIAG[
                "confirmation"
            ] += 1

            # ------------------------------------------------
            # SL / TP
            # ------------------------------------------------

            trade = build_trade(
                df5,
                df15,
                df1h,
                side,
                setup["setup"],
                setup["rvol"]
            )

            if not trade:

                DIAG[
                    "invalid_sltp"
                ] += 1

                continue

            DIAG[
                "valid_sl_tp"
            ] += 1

            # ------------------------------------------------
            # SCORE
            # ------------------------------------------------

            if (
                trade["score"]
                >= MIN_SCORE
            ):

                DIAG[
                    "score_9"
                ] += 1

            else:

                DIAG[
                    "low_score"
                ] += 1

                continue

            # ------------------------------------------------
            # SLOTS
            # ------------------------------------------------

            max_signals = min(
                available_slots,
                MAX_NEW_SIGNALS
            )

            if (
                len(signal_list)
                >= max_signals
            ):

                continue

            # ------------------------------------------------
            # DUPLICATE
            # ------------------------------------------------

            if has_open_trade(
                symbol
            ):

                continue

            # ------------------------------------------------
            # COOLDOWN
            # ------------------------------------------------

            if recently_traded(
                symbol
            ):

                continue

            # ------------------------------------------------
            # SAVE
            # ------------------------------------------------

            trade_id = save_trade(
                symbol,
                trade
            )

            trade["symbol"] = symbol

            trade["id"] = trade_id

            signal_list.append(
                trade
            )

            DIAG["final"] += 1

            print(
                f"  SIGNAL: "
                f"{symbol} "
                f"{side} "
                f"Score="
                f"{trade['score']:.0f} "
                f"RVOL="
                f"{trade['rvol']:.2f}"
            )

        except Exception as e:

            DIAG[
                "data_errors"
            ] += 1

            print(
                f"  DATA ERROR "
                f"{symbol}: {e}"
            )

    # --------------------------------------------------------
    # Final state
    # --------------------------------------------------------

    open_trades = get_open_trades()

    closed_rows = (
        get_unreported_closed()
    )

    prices = get_live_prices()

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    report = build_report(
        len(markets),
        signal_list,
        closed_rows,
        open_trades,
        prices
    )

    print("")
    print("=" * 60)
    print(report)
    print("=" * 60)

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    telegram_ok = send_telegram(
        report
    )

    # --------------------------------------------------------
    # Only mark closed reports
    # after successful Telegram
    # --------------------------------------------------------

    if telegram_ok:

        try:

            mark_closed_reported()

        except Exception as e:

            print(
                "Mark reported error:",
                e
            )

    else:

        print(
            "Telegram failed. "
            "Closed trades remain unreported."
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print("=" * 60)

        print(
            "FATAL ERROR"
        )

        print("=" * 60)

        print(
            str(e)
        )

        traceback.print_exc()

        raise
