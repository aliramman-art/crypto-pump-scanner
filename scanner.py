# ============================================================
# KRAKEN FUTURES VOLUME-KHAT 100 v4.1
# ============================================================
#
# 1H  = TREND
# 15M = SETUP
# 5M  = STRUCTURE BREAK + PULLBACK + CONFIRMATION
#
# CLOSED CANDLES ONLY
#
# v4.1 FINAL
# ------------------------------------------------------------
# - Robust SQLite schema migration
# - Correct Kraken Futures candle endpoint
# - Correct millisecond timestamp handling
# - Detailed API diagnostics
# - 5M structural SL / TP
# - SL: 0.50% <= SL <= 1.50%
# - TP: minimum RR 2.0
# - 5M Structure Break
# - 5M Pullback
# - 5M Confirmation
# - Duration tracking
# - Time-profit exit:
#       >= 2 hours AND PnL >= +1.50%
# - Max 3 open trades
# - Max 3 new signals per run
# - Cooldown = 3 closed 5M candles
# - Performance reset once for v4.1
# - PAPER TRADING ONLY
# ============================================================

import os
import time
import sqlite3
import requests

from datetime import datetime, timezone, timedelta


# ============================================================
# CONFIG
# ============================================================

TOP_N = 100

TIMEFRAME_5M = "5m"
TIMEFRAME_15M = "15m"
TIMEFRAME_1H = "1h"

OHLCV_LIMIT = 150
REQUEST_TIMEOUT = 20

# ------------------------------------------------------------
# RVOL
# ------------------------------------------------------------

RVOL_PERIOD = 20

RVOL_ABNORMAL = 1.70
RVOL_STRONG = 2.50
RVOL_VERY_STRONG = 3.00

# ------------------------------------------------------------
# 15M SETUP
# ------------------------------------------------------------

BREAKOUT_LOOKBACK = 5

SUPPORT_RESISTANCE_LOOKBACK = 20

REJECTION_DISTANCE = 0.008

WICK_BODY_RATIO = 1.15

MIN_BODY_RATIO = 0.20

# ------------------------------------------------------------
# 5M STRUCTURE
# ------------------------------------------------------------

STRUCTURE_LOOKBACK_5M = 5

PULLBACK_TOLERANCE = 0.0030

MAX_PULLBACK_CANDLES = 6

MIN_CONFIRM_BODY_RATIO = 0.30

# ------------------------------------------------------------
# ATR
# ------------------------------------------------------------

ATR_PERIOD = 14

ATR_SL_BUFFER = 0.15

# ------------------------------------------------------------
# SL / TP
# ------------------------------------------------------------

MIN_SL_PCT = 0.0050

MAX_SL_PCT = 0.0150

MIN_RR = 2.0

# ------------------------------------------------------------
# TRADING LIMITS
# ------------------------------------------------------------

MIN_SCORE = 9

MAX_OPEN_TRADES = 3

MAX_NEW_SIGNALS = 3

COOLDOWN_CANDLES = 3

# ------------------------------------------------------------
# TIME EXIT
# ------------------------------------------------------------

TIME_EXIT_HOURS = 2.0

TIME_EXIT_MINUTES = 120

TIME_EXIT_MIN_PROFIT_PCT = 1.50

# ------------------------------------------------------------
# MODE
# ------------------------------------------------------------

PAPER_TRADING = True

# ------------------------------------------------------------
# DATABASE
# ------------------------------------------------------------

DB_FILE = "volume_khat_100.db"

RESET_DATABASE_ON_V41_START = True

RESET_KEY = "VOLUME_KHAT_V41_RESET_DONE"

SCHEMA_VERSION = "4.1"

# ------------------------------------------------------------
# TIMEZONE
# ------------------------------------------------------------

IRAN_TZ = timezone(
    timedelta(
        hours=3,
        minutes=30,
    )
)


# ============================================================
# KRAKEN FUTURES API
# ============================================================

KRAKEN_OHLC_URL = (
    "https://futures.kraken.com/api/charts/v1"
)

KRAKEN_OHLC_TICK_TYPE = "trade"

KRAKEN_TICKER_URL = (
    "https://futures.kraken.com/"
    "derivatives/api/v3/tickers"
)


# ============================================================
# GLOBAL DIAGNOSTICS
# ============================================================

diagnostics = {
    "scanned": 0,

    "trend_neutral": 0,
    "trend_ok": 0,

    "setup_failed": 0,
    "setup_passed": 0,

    "structure_failed": 0,
    "pullback_failed": 0,
    "confirmation_failed": 0,
    "full_5m_confirmation": 0,

    "score_failed": 0,

    "cooldown": 0,
    "already_open": 0,

    "sl_too_small": 0,
    "sl_too_large": 0,

    "rr_failed": 0,

    "other": 0,

    "errors": 0,

    # --------------------------------------------------------
    # API diagnostics
    # --------------------------------------------------------

    "api_errors": 0,
    "api_empty": 0,
    "api_parse_errors": 0,

    "ohlcv_1h_errors": 0,
    "ohlcv_15m_errors": 0,
    "ohlcv_5m_errors": 0,
}


# ============================================================
# DATABASE
# ============================================================

def get_connection():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.row_factory = sqlite3.Row

    return conn


def create_trades_table(cur):

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT NOT NULL,
            side TEXT NOT NULL,

            entry REAL NOT NULL,
            sl REAL NOT NULL,
            tp REAL NOT NULL,

            entry_time TEXT NOT NULL,

            exit REAL,
            exit_time TEXT,

            pnl_pct REAL,

            result TEXT,
            exit_reason TEXT,

            duration_minutes REAL,

            closed_reported INTEGER DEFAULT 0
        )
        """
    )


def create_system_meta_table(cur):

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS system_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )


def init_db():

    conn = get_connection()

    cur = conn.cursor()

    # --------------------------------------------------------
    # Create tables
    # --------------------------------------------------------

    create_trades_table(cur)

    create_system_meta_table(cur)

    conn.commit()

    # --------------------------------------------------------
    # Existing columns
    # --------------------------------------------------------

    cur.execute(
        "PRAGMA table_info(trades)"
    )

    existing_columns = {
        row["name"]
        for row in cur.fetchall()
    }

    # --------------------------------------------------------
    # Optional / migration columns
    # --------------------------------------------------------

    migrations = {
        "entry_time": "TEXT",
        "exit": "REAL",
        "exit_time": "TEXT",
        "pnl_pct": "REAL",
        "result": "TEXT",
        "exit_reason": "TEXT",
        "duration_minutes": "REAL",
        "closed_reported":
            "INTEGER DEFAULT 0",
    }

    for column, data_type in migrations.items():

        if column not in existing_columns:

            try:

                cur.execute(
                    f"""
                    ALTER TABLE trades
                    ADD COLUMN {column}
                    {data_type}
                    """
                )

                print(
                    f"DB MIGRATION: "
                    f"added {column}"
                )

            except sqlite3.OperationalError as exc:

                print(
                    f"DB MIGRATION ERROR "
                    f"{column}: {exc}"
                )

                diagnostics["errors"] += 1

    # --------------------------------------------------------
    # Check required core columns
    # --------------------------------------------------------

    cur.execute(
        "PRAGMA table_info(trades)"
    )

    final_columns = {
        row["name"]
        for row in cur.fetchall()
    }

    required_core = {
        "symbol",
        "side",
        "entry",
        "sl",
        "tp",
    }

    missing_core = (
        required_core
        - final_columns
    )

    if missing_core:

        print(
            "CRITICAL DB ERROR: "
            f"missing core columns "
            f"{missing_core}"
        )

        conn.close()

        raise RuntimeError(
            "Trades database schema "
            "is incompatible. "
            f"Missing: {missing_core}"
        )

    # --------------------------------------------------------
    # Schema version
    # --------------------------------------------------------

    cur.execute(
        """
        INSERT OR REPLACE INTO system_meta
        (key, value)
        VALUES (?, ?)
        """,
        (
            "SCHEMA_VERSION",
            SCHEMA_VERSION,
        ),
    )

    conn.commit()

    conn.close()


def get_meta(key):

    conn = get_connection()

    cur = conn.cursor()

    cur.execute(
        """
        SELECT value
        FROM system_meta
        WHERE key = ?
        """,
        (key,),
    )

    row = cur.fetchone()

    conn.close()

    if row:

        return row["value"]

    return None


def set_meta(key, value):

    conn = get_connection()

    cur = conn.cursor()

    cur.execute(
        """
        INSERT OR REPLACE INTO system_meta
        (key, value)
        VALUES (?, ?)
        """,
        (
            key,
            str(value),
        ),
    )

    conn.commit()

    conn.close()


# ============================================================
# V4.1 RESET
# ============================================================

def perform_v41_reset():

    if not RESET_DATABASE_ON_V41_START:
        return

    done = get_meta(
        RESET_KEY
    )

    if done == "1":

        return

    conn = get_connection()

    cur = conn.cursor()

    cur.execute(
        "DELETE FROM trades"
    )

    conn.commit()

    conn.close()

    set_meta(
        RESET_KEY,
        "1",
    )

    print(
        "V4.1 performance reset completed."
    )


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent":
            "VOLUME-KHAT-100/4.1"
    }
)


# ============================================================
# OHLCV ERROR COUNTER
# ============================================================

def register_ohlcv_error(
    interval,
    error_type="generic",
):

    diagnostics["errors"] += 1

    diagnostics["api_errors"] += 1

    if error_type == "empty":

        diagnostics["api_empty"] += 1

    elif error_type == "parse":

        diagnostics["api_parse_errors"] += 1

    if interval == TIMEFRAME_1H:

        diagnostics[
            "ohlcv_1h_errors"
        ] += 1

    elif interval == TIMEFRAME_15M:

        diagnostics[
            "ohlcv_15m_errors"
        ] += 1

    elif interval == TIMEFRAME_5M:

        diagnostics[
            "ohlcv_5m_errors"
        ] += 1


# ============================================================
# FETCH OHLCV
# ============================================================

def fetch_ohlcv(
    symbol,
    interval,
    limit=OHLCV_LIMIT,
):

    try:

        # ----------------------------------------------------
        # Correct Kraken Futures endpoint
        #
        # /charts/v1/trade/SYMBOL/RESOLUTION
        # ----------------------------------------------------

        url = (
            f"{KRAKEN_OHLC_URL}/"
            f"{KRAKEN_OHLC_TICK_TYPE}/"
            f"{symbol}/"
            f"{interval}"
        )

        response = session.get(
            url,
            params={
                "count": limit + 2,
            },
            timeout=REQUEST_TIMEOUT,
        )

        # ----------------------------------------------------
        # HTTP ERROR
        # ----------------------------------------------------

        if not response.ok:

            register_ohlcv_error(
                interval
            )

            error_text = (
                response.text[:300]
                if response.text
                else "No response body"
            )

            print(
                f"OHLCV HTTP ERROR | "
                f"{symbol} | "
                f"{interval} | "
                f"HTTP "
                f"{response.status_code} | "
                f"{error_text}"
            )

            return []

        # ----------------------------------------------------
        # JSON
        # ----------------------------------------------------

        try:

            data = response.json()

        except ValueError as exc:

            register_ohlcv_error(
                interval,
                "parse",
            )

            print(
                f"OHLCV JSON ERROR | "
                f"{symbol} | "
                f"{interval} | "
                f"{exc}"
            )

            return []

        # ----------------------------------------------------
        # Validate object
        # ----------------------------------------------------

        if not isinstance(
            data,
            dict,
        ):

            register_ohlcv_error(
                interval,
                "parse",
            )

            print(
                f"OHLCV FORMAT ERROR | "
                f"{symbol} | "
                f"{interval} | "
                f"Expected dict, got "
                f"{type(data).__name__}"
            )

            return []

        # ----------------------------------------------------
        # Kraken candles
        # ----------------------------------------------------

        candles = data.get(
            "candles"
        )

        if candles is None:

            register_ohlcv_error(
                interval,
                "parse",
            )

            print(
                f"OHLCV DATA ERROR | "
                f"{symbol} | "
                f"{interval} | "
                f"'candles' missing | "
                f"keys={list(data.keys())}"
            )

            return []

        if not isinstance(
            candles,
            list,
        ):

            register_ohlcv_error(
                interval,
                "parse",
            )

            print(
                f"OHLCV DATA ERROR | "
                f"{symbol} | "
                f"{interval} | "
                f"candles is not list"
            )

            return []

        if not candles:

            register_ohlcv_error(
                interval,
                "empty",
            )

            print(
                f"OHLCV EMPTY | "
                f"{symbol} | "
                f"{interval}"
            )

            return []

        result = []

        # ----------------------------------------------------
        # Parse
        # ----------------------------------------------------

        for candle in candles:

            try:

                if isinstance(
                    candle,
                    dict,
                ):

                    ts = (
                        candle.get("time")
                        or candle.get(
                            "timestamp"
                        )
                    )

                    op = candle.get(
                        "open"
                    )

                    hi = candle.get(
                        "high"
                    )

                    lo = candle.get(
                        "low"
                    )

                    cl = candle.get(
                        "close"
                    )

                    vol = candle.get(
                        "volume"
                    )

                else:

                    if len(candle) < 6:
                        continue

                    ts = candle[0]
                    op = candle[1]
                    hi = candle[2]
                    lo = candle[3]
                    cl = candle[4]
                    vol = candle[5]

                if (
                    ts is None
                    or op is None
                    or hi is None
                    or lo is None
                    or cl is None
                    or vol is None
                ):

                    continue

                ts = float(ts)

                # ------------------------------------------------
                # Kraken Futures timestamps can be milliseconds.
                # Convert milliseconds -> seconds.
                # ------------------------------------------------

                if ts > 10_000_000_000:

                    ts /= 1000.0

                result.append(
                    {
                        "time": ts,
                        "open": float(op),
                        "high": float(hi),
                        "low": float(lo),
                        "close": float(cl),
                        "volume": float(vol),
                    }
                )

            except Exception as exc:

                print(
                    f"OHLCV CANDLE PARSE ERROR | "
                    f"{symbol} | "
                    f"{interval} | "
                    f"{exc}"
                )

                continue

        # ----------------------------------------------------
        # No valid candles
        # ----------------------------------------------------

        if not result:

            register_ohlcv_error(
                interval,
                "parse",
            )

            print(
                f"OHLCV PARSE EMPTY | "
                f"{symbol} | "
                f"{interval}"
            )

            return []

        # ----------------------------------------------------
        # Sort
        # ----------------------------------------------------

        result.sort(
            key=lambda x: x["time"]
        )

        # ----------------------------------------------------
        # Remove duplicates
        # ----------------------------------------------------

        unique = {}

        for candle in result:

            unique[
                candle["time"]
            ] = candle

        result = list(
            unique.values()
        )

        result.sort(
            key=lambda x: x["time"]
        )

        # ----------------------------------------------------
        # Closed candles only
        # ----------------------------------------------------

        interval_seconds = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "4h": 14400,
            "12h": 43200,
            "1d": 86400,
            "1w": 604800,
        }.get(
            interval
        )

        if interval_seconds is None:

            register_ohlcv_error(
                interval,
                "parse",
            )

            print(
                f"OHLCV INTERVAL ERROR | "
                f"{symbol} | "
                f"{interval}"
            )

            return []

        # ----------------------------------------------------
        # Remove current forming candle
        # ----------------------------------------------------

        now_ts = time.time()

        if result:

            last = result[-1]

            candle_close_time = (
                last["time"]
                + interval_seconds
            )

            if (
                now_ts
                < candle_close_time
            ):

                result = result[:-1]

        # ----------------------------------------------------
        # Minimum data
        # ----------------------------------------------------

        if len(result) < 20:

            register_ohlcv_error(
                interval,
                "empty",
            )

            print(
                f"OHLCV TOO FEW CANDLES | "
                f"{symbol} | "
                f"{interval} | "
                f"count={len(result)}"
            )

            return []

        return result[-limit:]

    except requests.RequestException as exc:

        register_ohlcv_error(
            interval
        )

        print(
            f"OHLCV REQUEST ERROR | "
            f"{symbol} | "
            f"{interval} | "
            f"{exc}"
        )

        return []

    except Exception as exc:

        register_ohlcv_error(
            interval
        )

        print(
            f"OHLCV ERROR | "
            f"{symbol} | "
            f"{interval} | "
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        return []


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(
    candles,
    period=RVOL_PERIOD,
):

    if len(candles) <= period:

        return 0.0

    current_volume = (
        candles[-1]["volume"]
    )

    previous = candles[
        -(period + 1):-1
    ]

    if not previous:

        return 0.0

    avg_volume = (
        sum(
            c["volume"]
            for c in previous
        )
        / len(previous)
    )

    if avg_volume <= 0:

        return 0.0

    return (
        current_volume
        / avg_volume
    )


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    candles,
    period=ATR_PERIOD,
):

    if len(candles) < (
        period + 1
    ):

        return 0.0

    trs = []

    for i in range(
        1,
        len(candles),
    ):

        current = candles[i]

        previous = candles[i - 1]

        tr = max(
            current["high"]
            - current["low"],

            abs(
                current["high"]
                - previous["close"]
            ),

            abs(
                current["low"]
                - previous["close"]
            ),
        )

        trs.append(tr)

    if len(trs) < period:

        return 0.0

    return (
        sum(trs[-period:])
        / period
    )


# ============================================================
# SMA
# ============================================================

def sma(
    values,
    period,
):

    if len(values) < period:

        return None

    return (
        sum(values[-period:])
        / period
    )


# ============================================================
# 1H TREND
# ============================================================

def get_trend(
    candles,
):

    if len(candles) < 50:

        return "NEUTRAL"

    closes = [
        c["close"]
        for c in candles
    ]

    fast = sma(
        closes,
        20,
    )

    slow = sma(
        closes,
        50,
    )

    if (
        fast is None
        or slow is None
    ):

        return "NEUTRAL"

    last_close = closes[-1]

    if (
        last_close > fast
        and fast > slow
    ):

        return "LONG"

    if (
        last_close < fast
        and fast < slow
    ):

        return "SHORT"

    return "NEUTRAL"


# ============================================================
# 15M BREAKOUT
# ============================================================

def detect_breakout(
    candles,
):

    if len(candles) < (
        BREAKOUT_LOOKBACK + 2
    ):

        return None

    current = candles[-1]

    previous = candles[
        -(BREAKOUT_LOOKBACK + 1):-1
    ]

    highest = max(
        c["high"]
        for c in previous
    )

    lowest = min(
        c["low"]
        for c in previous
    )

    rvol = calculate_rvol(
        candles
    )

    if (
        current["close"] > highest
        and rvol >= RVOL_ABNORMAL
    ):

        return {
            "type": "BREAKOUT",
            "side": "LONG",
            "rvol": rvol,
        }

    if (
        current["close"] < lowest
        and rvol >= RVOL_ABNORMAL
    ):

        return {
            "type": "BREAKOUT",
            "side": "SHORT",
            "rvol": rvol,
        }

    return None


# ============================================================
# 15M REJECTION
# ============================================================

def detect_rejection(
    candles,
    trend_side,
):

    if len(candles) < (
        SUPPORT_RESISTANCE_LOOKBACK
        + 2
    ):

        return None

    current = candles[-1]

    previous = candles[
        -(
            SUPPORT_RESISTANCE_LOOKBACK
            + 1
        ):-1
    ]

    resistance = max(
        c["high"]
        for c in previous
    )

    support = min(
        c["low"]
        for c in previous
    )

    body = abs(
        current["close"]
        - current["open"]
    )

    candle_range = (
        current["high"]
        - current["low"]
    )

    if candle_range <= 0:

        return None

    body_ratio = (
        body / candle_range
    )

    if body_ratio < MIN_BODY_RATIO:

        return None

    upper_wick = (
        current["high"]
        - max(
            current["open"],
            current["close"],
        )
    )

    lower_wick = (
        min(
            current["open"],
            current["close"],
        )
        - current["low"]
    )

    if body <= 0:

        return None

    rvol = calculate_rvol(
        candles
    )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if resistance <= 0:

        return None

    resistance_distance = (
        abs(
            current["high"]
            - resistance
        )
        / resistance
    )

    if (
        trend_side == "SHORT"
        and resistance_distance
        <= REJECTION_DISTANCE
        and upper_wick / body
        >= WICK_BODY_RATIO
        and current["close"]
        < current["open"]
        and rvol >= RVOL_ABNORMAL
    ):

        return {
            "type": "REJECTION",
            "side": "SHORT",
            "rvol": rvol,
        }

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if support <= 0:

        return None

    support_distance = (
        abs(
            current["low"]
            - support
        )
        / support
    )

    if (
        trend_side == "LONG"
        and support_distance
        <= REJECTION_DISTANCE
        and lower_wick / body
        >= WICK_BODY_RATIO
        and current["close"]
        > current["open"]
        and rvol >= RVOL_ABNORMAL
    ):

        return {
            "type": "REJECTION",
            "side": "LONG",
            "rvol": rvol,
        }

    return None


# ============================================================
# 15M SETUP
# ============================================================

def detect_setup(
    candles,
    trend_side,
):

    breakout = detect_breakout(
        candles
    )

    if breakout:

        if (
            breakout["side"]
            == trend_side
        ):

            return breakout

    rejection = detect_rejection(
        candles,
        trend_side,
    )

    if rejection:

        return rejection

    return None


# ============================================================
# 5M STRUCTURE CONFIRMATION
# ============================================================

def confirm_5m_structure(
    candles,
    side,
):

    if len(candles) < 20:

        diagnostics[
            "structure_failed"
        ] += 1

        return None

    break_index = None
    break_level = None

    start = max(
        STRUCTURE_LOOKBACK_5M,
        len(candles)
        - MAX_PULLBACK_CANDLES
        - 3,
    )

    end = len(candles) - 1

    for i in range(
        start,
        end,
    ):

        previous = candles[
            i
            - STRUCTURE_LOOKBACK_5M:i
        ]

        current = candles[i]

        previous_high = max(
            c["high"]
            for c in previous
        )

        previous_low = min(
            c["low"]
            for c in previous
        )

        if (
            side == "LONG"
            and current["close"]
            > previous_high
        ):

            break_index = i

            break_level = (
                previous_high
            )

        elif (
            side == "SHORT"
            and current["close"]
            < previous_low
        ):

            break_index = i

            break_level = (
                previous_low
            )

    if break_index is None:

        diagnostics[
            "structure_failed"
        ] += 1

        return None

    # --------------------------------------------------------
    # Pullback
    # --------------------------------------------------------

    pullback_index = None

    pullback_end = min(
        len(candles) - 1,
        break_index
        + MAX_PULLBACK_CANDLES,
    )

    for i in range(
        break_index + 1,
        pullback_end + 1,
    ):

        candle = candles[i]

        if side == "LONG":

            distance = (
                abs(
                    candle["low"]
                    - break_level
                )
                / break_level
            )

            if (
                distance
                <= PULLBACK_TOLERANCE
                and candle["low"]
                >= (
                    break_level
                    * (
                        1
                        - PULLBACK_TOLERANCE
                    )
                )
            ):

                pullback_index = i

                break

        else:

            distance = (
                abs(
                    candle["high"]
                    - break_level
                )
                / break_level
            )

            if (
                distance
                <= PULLBACK_TOLERANCE
                and candle["high"]
                <= (
                    break_level
                    * (
                        1
                        + PULLBACK_TOLERANCE
                    )
                )
            ):

                pullback_index = i

                break

    if pullback_index is None:

        diagnostics[
            "pullback_failed"
        ] += 1

        return None

    # --------------------------------------------------------
    # Confirmation candle
    # --------------------------------------------------------

    confirmation = candles[-1]

    if (
        pullback_index
        >= len(candles) - 1
    ):

        diagnostics[
            "confirmation_failed"
        ] += 1

        return None

    body = abs(
        confirmation["close"]
        - confirmation["open"]
    )

    candle_range = (
        confirmation["high"]
        - confirmation["low"]
    )

    if candle_range <= 0:

        diagnostics[
            "confirmation_failed"
        ] += 1

        return None

    body_ratio = (
        body / candle_range
    )

    if (
        body_ratio
        < MIN_CONFIRM_BODY_RATIO
    ):

        diagnostics[
            "confirmation_failed"
        ] += 1

        return None

    if side == "LONG":

        bullish = (
            confirmation["close"]
            > confirmation["open"]
        )

        valid_close = (
            confirmation["close"]
            > break_level
        )

        if not (
            bullish
            and valid_close
        ):

            diagnostics[
                "confirmation_failed"
            ] += 1

            return None

    else:

        bearish = (
            confirmation["close"]
            < confirmation["open"]
        )

        valid_close = (
            confirmation["close"]
            < break_level
        )

        if not (
            bearish
            and valid_close
        ):

            diagnostics[
                "confirmation_failed"
            ] += 1

            return None

    diagnostics[
        "full_5m_confirmation"
    ] += 1

    return {
        "break_index":
            break_index,

        "break_level":
            break_level,

        "pullback_index":
            pullback_index,

        "confirmation_index":
            len(candles) - 1,

        "confirmation":
            confirmation,
    }


# ============================================================
# 5M STRUCTURAL SL
# ============================================================

def calculate_5m_structural_sl(
    candles,
    structure,
    side,
):

    pullback_index = (
        structure[
            "pullback_index"
        ]
    )

    confirmation_index = (
        structure[
            "confirmation_index"
        ]
    )

    atr = calculate_atr(
        candles
    )

    if atr <= 0:

        return None

    section = candles[
        pullback_index:
        confirmation_index + 1
    ]

    if not section:

        return None

    if (
        structure["break_level"]
        <= 0
    ):

        return None

    if side == "LONG":

        structural_low = min(
            c["low"]
            for c in section
        )

        return (
            structural_low
            - atr * ATR_SL_BUFFER
        )

    if side == "SHORT":

        structural_high = max(
            c["high"]
            for c in section
        )

        return (
            structural_high
            + atr * ATR_SL_BUFFER
        )

    return None


# ============================================================
# SL / TP
# ============================================================

def build_sl_tp(
    entry,
    side,
    sl,
):

    if (
        entry <= 0
        or sl <= 0
    ):

        return None

    if side == "LONG":

        if sl >= entry:

            return None

        risk = (
            entry - sl
        )

        tp = (
            entry
            + risk * MIN_RR
        )

    elif side == "SHORT":

        if sl <= entry:

            return None

        risk = (
            sl - entry
        )

        tp = (
            entry
            - risk * MIN_RR
        )

    else:

        return None

    sl_pct = (
        abs(
            entry - sl
        )
        / entry
        * 100
    )

    if sl_pct < (
        MIN_SL_PCT * 100
    ):

        diagnostics[
            "sl_too_small"
        ] += 1

        return None

    if sl_pct > (
        MAX_SL_PCT * 100
    ):

        diagnostics[
            "sl_too_large"
        ] += 1

        return None

    risk_distance = abs(
        entry - sl
    )

    reward_distance = abs(
        tp - entry
    )

    if risk_distance <= 0:

        return None

    rr = (
        reward_distance
        / risk_distance
    )

    if rr < MIN_RR:

        diagnostics[
            "rr_failed"
        ] += 1

        return None

    return {
        "sl": sl,
        "tp": tp,
        "sl_pct": sl_pct,
        "rr": rr,
    }


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    trend_side,
    setup,
    rvol_5m,
    structure_ok,
):

    score = 0

    # 1H
    if trend_side in (
        "LONG",
        "SHORT",
    ):

        score += 3

    # 15M
    if setup is not None:

        score += 4

    # 5M RVOL
    if (
        rvol_5m
        >= RVOL_VERY_STRONG
    ):

        score += 4

    elif (
        rvol_5m
        >= RVOL_STRONG
    ):

        score += 4

    elif (
        rvol_5m
        >= RVOL_ABNORMAL
    ):

        score += 3

    # 5M confirmation
    if structure_ok:

        score += 4

    return min(
        score,
        15,
    )


# ============================================================
# TOP MARKETS
# ============================================================

def get_top_markets():

    try:

        response = session.get(
            KRAKEN_TICKER_URL,
            timeout=REQUEST_TIMEOUT,
        )

        if not response.ok:

            diagnostics[
                "errors"
            ] += 1

            print(
                f"MARKET HTTP ERROR | "
                f"HTTP "
                f"{response.status_code} | "
                f"{response.text[:300]}"
            )

            return []

        data = response.json()

        if not isinstance(
            data,
            dict,
        ):

            diagnostics[
                "errors"
            ] += 1

            print(
                "MARKET FORMAT ERROR"
            )

            return []

        tickers = data.get(
            "tickers"
        )

        if not tickers:

            diagnostics[
                "errors"
            ] += 1

            print(
                "MARKET DATA EMPTY"
            )

            return []

        markets = []

        for ticker in tickers:

            if not isinstance(
                ticker,
                dict,
            ):

                continue

            symbol = ticker.get(
                "symbol"
            )

            if not symbol:

                continue

            if not (
                symbol.startswith(
                    "PF_"
                )
                and symbol.endswith(
                    "USD"
                )
            ):

                continue

            volume = (
                ticker.get("vol24h")
                or ticker.get("volume")
                or 0
            )

            try:

                volume = float(
                    volume
                )

            except Exception:

                volume = 0.0

            markets.append(
                (
                    symbol,
                    volume,
                )
            )

        markets.sort(
            key=lambda x: x[1],
            reverse=True,
        )

        return [
            symbol
            for symbol, _
            in markets[:TOP_N]
        ]

    except Exception as exc:

        diagnostics[
            "errors"
        ] += 1

        print(
            f"MARKET ERROR: "
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        return []


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(
    symbol,
):

    try:

        response = session.get(
            KRAKEN_TICKER_URL,
            timeout=REQUEST_TIMEOUT,
        )

        if not response.ok:

            diagnostics[
                "errors"
            ] += 1

            print(
                f"PRICE HTTP ERROR | "
                f"{symbol} | "
                f"HTTP "
                f"{response.status_code}"
            )

            return None

        data = response.json()

        tickers = (
            data.get("tickers")
            if isinstance(
                data,
                dict,
            )
            else data
        )

        if not isinstance(
            tickers,
            list,
        ):

            return None

        for ticker in tickers:

            if not isinstance(
                ticker,
                dict,
            ):

                continue

            if (
                ticker.get("symbol")
                != symbol
            ):

                continue

            price = (
                ticker.get("last")
                or ticker.get(
                    "lastPrice"
                )
                or ticker.get(
                    "markPrice"
                )
            )

            if price is None:

                continue

            return float(price)

    except Exception as exc:

        diagnostics[
            "errors"
        ] += 1

        print(
            f"PRICE ERROR | "
            f"{symbol} | "
            f"{type(exc).__name__}: "
            f"{exc}"
        )

    return None


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():

    conn = get_connection()

    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM trades
        WHERE exit_time IS NULL
        ORDER BY id ASC
        """
    )

    trades = cur.fetchall()

    conn.close()

    return trades


def get_open_symbols():

    return {
        trade["symbol"]
        for trade in get_open_trades()
    }


# ============================================================
# DURATION
# ============================================================

def calculate_duration_minutes(
    entry_time,
):

    if not entry_time:

        return 0.0

    try:

        dt = datetime.fromisoformat(
            entry_time
        )

        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=timezone.utc
            )

        now = datetime.now(
            timezone.utc
        )

        return max(
            0.0,
            (
                now - dt
            ).total_seconds()
            / 60.0,
        )

    except Exception:

        return 0.0


def format_duration(
    minutes,
):

    if minutes is None:

        return "N/A"

    minutes = int(
        max(
            0,
            minutes,
        )
    )

    hours = (
        minutes // 60
    )

    mins = (
        minutes % 60
    )

    if hours > 0:

        return (
            f"{hours}h "
            f"{mins}m"
        )

    return f"{mins}m"


# ============================================================
# PNL
# ============================================================

def calculate_pnl_pct(
    side,
    entry,
    current,
):

    if entry <= 0:

        return 0.0

    if side == "LONG":

        return (
            (
                current - entry
            )
            / entry
            * 100
        )

    return (
        (
            entry - current
        )
        / entry
        * 100
    )


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade_id,
    exit_price,
    reason,
):

    conn = get_connection()

    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM trades
        WHERE id = ?
        """,
        (trade_id,),
    )

    trade = cur.fetchone()

    if trade is None:

        conn.close()

        return

    pnl_pct = calculate_pnl_pct(
        trade["side"],
        trade["entry"],
        exit_price,
    )

    result = (
        "WIN"
        if pnl_pct > 0
        else "LOSS"
    )

    duration = (
        calculate_duration_minutes(
            trade["entry_time"]
        )
    )

    exit_time = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    cur.execute(
        """
        UPDATE trades
        SET
            exit = ?,
            exit_time = ?,
            pnl_pct = ?,
            result = ?,
            exit_reason = ?,
            duration_minutes = ?
        WHERE id = ?
        """,
        (
            exit_price,
            exit_time,
            pnl_pct,
            result,
            reason,
            duration,
            trade_id,
        ),
    )

    conn.commit()

    conn.close()


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades():

    trades = get_open_trades()

    for trade in trades:

        symbol = trade["symbol"]

        side = trade["side"]

        entry = float(
            trade["entry"]
        )

        sl = float(
            trade["sl"]
        )

        tp = float(
            trade["tp"]
        )

        current = get_current_price(
            symbol
        )

        if current is None:

            continue

        pnl = calculate_pnl_pct(
            side,
            entry,
            current,
        )

        duration = (
            calculate_duration_minutes(
                trade["entry_time"]
            )
        )

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if side == "LONG":

            if current <= sl:

                close_trade(
                    trade["id"],
                    current,
                    "STOP_LOSS",
                )

                continue

            if current >= tp:

                close_trade(
                    trade["id"],
                    current,
                    "TAKE_PROFIT",
                )

                continue

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        else:

            if current >= sl:

                close_trade(
                    trade["id"],
                    current,
                    "STOP_LOSS",
                )

                continue

            if current <= tp:

                close_trade(
                    trade["id"],
                    current,
                    "TAKE_PROFIT",
                )

                continue

        # ----------------------------------------------------
        # TIME PROFIT
        # ----------------------------------------------------

        if (
            duration
            >= TIME_EXIT_MINUTES
            and pnl
            >= TIME_EXIT_MIN_PROFIT_PCT
        ):

            close_trade(
                trade["id"],
                current,
                "TIME_PROFIT",
            )


# ============================================================
# COOLDOWN
# ============================================================

def is_in_cooldown(
    symbol,
):

    conn = get_connection()

    cur = conn.cursor()

    cur.execute(
        """
        SELECT exit_time
        FROM trades
        WHERE symbol = ?
          AND exit_time IS NOT NULL
        ORDER BY exit_time DESC
        LIMIT 1
        """,
        (symbol,),
    )

    row = cur.fetchone()

    conn.close()

    if (
        not row
        or not row["exit_time"]
    ):

        return False

    try:

        exit_dt = (
            datetime.fromisoformat(
                row["exit_time"]
            )
        )

        if exit_dt.tzinfo is None:

            exit_dt = exit_dt.replace(
                tzinfo=timezone.utc
            )

        elapsed = (
            datetime.now(
                timezone.utc
            )
            - exit_dt
        ).total_seconds()

        candle_seconds = (
            COOLDOWN_CANDLES
            * 300
        )

        return (
            elapsed
            < candle_seconds
        )

    except Exception:

        return False


# ============================================================
# CREATE TRADE
# ============================================================

def create_trade(
    symbol,
    side,
    entry,
    sl,
    tp,
):

    conn = get_connection()

    cur = conn.cursor()

    entry_time = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    cur.execute(
        """
        INSERT INTO trades (
            symbol,
            side,
            entry,
            sl,
            tp,
            entry_time
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            symbol,
            side,
            entry,
            sl,
            tp,
            entry_time,
        ),
    )

    conn.commit()

    conn.close()


# ============================================================
# PERFORMANCE
# ============================================================

def get_performance():

    conn = get_connection()

    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            COUNT(*) AS total,

            SUM(
                CASE
                    WHEN result = 'WIN'
                    THEN 1
                    ELSE 0
                END
            ) AS wins,

            SUM(
                CASE
                    WHEN result = 'LOSS'
                    THEN 1
                    ELSE 0
                END
            ) AS losses,

            COALESCE(
                SUM(pnl_pct),
                0
            ) AS pnl

        FROM trades

        WHERE exit_time IS NOT NULL
        """
    )

    row = cur.fetchone()

    cur.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE exit_time IS NULL
        """
    )

    open_count = (
        cur.fetchone()[0]
    )

    cur.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE exit_reason = ?
        """,
        ("TIME_PROFIT",),
    )

    time_exits = (
        cur.fetchone()[0]
    )

    conn.close()

    total = row["total"] or 0

    wins = row["wins"] or 0

    losses = row["losses"] or 0

    pnl = row["pnl"] or 0.0

    if total > 0:

        win_rate = (
            wins
            / total
            * 100
        )

    else:

        win_rate = 0.0

    return {
        "open":
            open_count,

        "closed":
            total,

        "wins":
            wins,

        "losses":
            losses,

        "win_rate":
            win_rate,

        "pnl":
            pnl,

        "time_exits":
            time_exits,
    }


# ============================================================
# FORMAT PRICE
# ============================================================

def fmt_price(
    value,
):

    if value is None:

        return "N/A"

    if value >= 1000:

        return f"{value:.2f}"

    if value >= 1:

        return f"{value:.5f}"

    if value >= 0.01:

        return f"{value:.6f}"

    return f"{value:.8f}"


# ============================================================
# TOP CANDIDATE
# ============================================================

def format_candidate(
    candidate,
):

    if not candidate:

        return "None"

    side_icon = (
        "🟢"
        if candidate["side"]
        == "LONG"
        else "🔴"
    )

    text = []

    text.append(
        f"{side_icon} "
        f"{candidate['symbol']} "
        f"{candidate['side']}"
    )

    text.append(
        f"Setup: "
        f"{candidate['setup']}"
    )

    text.append(
        f"Score: "
        f"{candidate['score']}/15"
    )

    text.append(
        f"RVOL: "
        f"{candidate['rvol']:.2f}x"
    )

    if candidate.get(
        "entry"
    ) is not None:

        text.append(
            f"Entry: "
            f"{fmt_price(candidate['entry'])}"
        )

    if candidate.get(
        "sl"
    ) is not None:

        text.append(
            f"SL: "
            f"{fmt_price(candidate['sl'])}"
        )

    if candidate.get(
        "tp"
    ) is not None:

        text.append(
            f"TP: "
            f"{fmt_price(candidate['tp'])}"
        )

    if candidate.get(
        "sl_pct"
    ) is not None:

        text.append(
            f"SL Distance: "
            f"{candidate['sl_pct']:.2f}%"
        )

    if candidate.get(
        "rr"
    ) is not None:

        text.append(
            f"RR: "
            f"{candidate['rr']:.2f}"
        )

    if candidate.get(
        "reason"
    ):

        text.append(
            "❌ REJECTED"
        )

        text.append(
            f"Reason: "
            f"{candidate['reason']}"
        )

    return "\n".join(text)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(
    message,
):

    bot_token = os.getenv(
        "TELEGRAM_BOT_TOKEN"
    )

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID"
    )

    if (
        not bot_token
        or not chat_id
    ):

        print(
            "Telegram credentials "
            "not found."
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{bot_token}/sendMessage"
    )

    try:

        response = session.post(
            url,
            data={
                "chat_id":
                    chat_id,

                "text":
                    message,
            },
            timeout=REQUEST_TIMEOUT,
        )

        return response.ok

    except Exception as exc:

        print(
            f"TELEGRAM ERROR: "
            f"{exc}"
        )

        return False


# ============================================================
# REPORT
# ============================================================

def build_report(
    signals,
    candidate,
):

    now = datetime.now(
        IRAN_TZ
    ).strftime(
        "%Y/%m/%d %H:%M:%S"
    )

    performance = (
        get_performance()
    )

    open_trades = (
        get_open_trades()
    )

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
        "🧠 5M: BREAKOUT + "
        "PULLBACK + CONFIRMATION"
    )

    lines.append("")

    # --------------------------------------------------------
    # SIGNALS
    # --------------------------------------------------------

    lines.append(
        "🎯 NEW SIGNALS"
    )

    if not signals:

        lines.append(
            "None"
        )

    else:

        for signal in signals:

            side_icon = (
                "🟢"
                if signal["side"]
                == "LONG"
                else "🔴"
            )

            lines.append(
                f"{side_icon} "
                f"{signal['symbol']} "
                f"{signal['side']}"
            )

            lines.append(
                f"Entry: "
                f"{fmt_price(signal['entry'])}"
            )

            lines.append(
                f"SL: "
                f"{fmt_price(signal['sl'])} "
                f"({signal['sl_pct']:.2f}%)"
            )

            lines.append(
                f"TP: "
                f"{fmt_price(signal['tp'])}"
            )

            lines.append(
                f"RR: "
                f"{signal['rr']:.2f}"
            )

            lines.append(
                f"Score: "
                f"{signal['score']}/15"
            )

            lines.append("")

    # --------------------------------------------------------
    # TOP CANDIDATE
    # --------------------------------------------------------

    lines.append(
        "🏆 TOP CANDIDATE"
    )

    if candidate:

        lines.append(
            format_candidate(
                candidate
            )
        )

    else:

        lines.append(
            "None"
        )

    lines.append("")

    # --------------------------------------------------------
    # DIAGNOSTICS
    # --------------------------------------------------------

    lines.append(
        "🔎 FILTER DIAGNOSTICS"
    )

    lines.append(
        f"Scanned: "
        f"{diagnostics['scanned']}"
    )

    lines.append(
        "1H FILTER"
    )

    lines.append(
        f"❌ Neutral: "
        f"{diagnostics['trend_neutral']}"
    )

    lines.append(
        f"✅ Trend OK: "
        f"{diagnostics['trend_ok']}"
    )

    lines.append(
        "15M SETUP"
    )

    lines.append(
        f"❌ Setup Failed: "
        f"{diagnostics['setup_failed']}"
    )

    lines.append(
        f"✅ Setup Passed: "
        f"{diagnostics['setup_passed']}"
    )

    lines.append(
        "5M STRUCTURE"
    )

    lines.append(
        f"❌ Structure Break Failed: "
        f"{diagnostics['structure_failed']}"
    )

    lines.append(
        f"❌ Pullback Failed: "
        f"{diagnostics['pullback_failed']}"
    )

    lines.append(
        f"❌ Confirmation Failed: "
        f"{diagnostics['confirmation_failed']}"
    )

    lines.append(
        f"✅ Full 5M Confirmation: "
        f"{diagnostics['full_5m_confirmation']}"
    )

    lines.append(
        f"❌ Score < {MIN_SCORE}: "
        f"{diagnostics['score_failed']}"
    )

    lines.append(
        f"❌ Cooldown: "
        f"{diagnostics['cooldown']}"
    )

    lines.append(
        f"❌ Already Open: "
        f"{diagnostics['already_open']}"
    )

    lines.append(
        f"❌ SL < "
        f"{MIN_SL_PCT * 100:.2f}%: "
        f"{diagnostics['sl_too_small']}"
    )

    lines.append(
        f"❌ SL > "
        f"{MAX_SL_PCT * 100:.2f}%: "
        f"{diagnostics['sl_too_large']}"
    )

    lines.append(
        f"❌ RR < "
        f"{MIN_RR:.2f}: "
        f"{diagnostics['rr_failed']}"
    )

    lines.append(
        f"❌ Other: "
        f"{diagnostics['other']}"
    )

    lines.append(
        f"⚠️ Errors: "
        f"{diagnostics['errors']}"
    )

    lines.append(
        f"   API Errors: "
        f"{diagnostics['api_errors']}"
    )

    lines.append(
        f"   API Empty: "
        f"{diagnostics['api_empty']}"
    )

    lines.append(
        f"   API Parse Errors: "
        f"{diagnostics['api_parse_errors']}"
    )

    lines.append(
        f"   1H Data Errors: "
        f"{diagnostics['ohlcv_1h_errors']}"
    )

    lines.append(
        f"   15M Data Errors: "
        f"{diagnostics['ohlcv_15m_errors']}"
    )

    lines.append(
        f"   5M Data Errors: "
        f"{diagnostics['ohlcv_5m_errors']}"
    )

    lines.append("")

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

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

            current = (
                get_current_price(
                    trade["symbol"]
                )
            )

            if current is not None:

                pnl = calculate_pnl_pct(
                    trade["side"],
                    trade["entry"],
                    current,
                )

            else:

                pnl = 0.0

            duration = (
                calculate_duration_minutes(
                    trade["entry_time"]
                )
            )

            icon = (
                "🟢"
                if pnl >= 0
                else "🔴"
            )

            lines.append(
                f"{icon} "
                f"{trade['symbol']} "
                f"{trade['side']} "
                f"{pnl:+.2f}%"
            )

            lines.append(
                f"Entry: "
                f"{fmt_price(trade['entry'])}"
            )

            lines.append(
                f"SL: "
                f"{fmt_price(trade['sl'])}"
            )

            lines.append(
                f"TP: "
                f"{fmt_price(trade['tp'])}"
            )

            lines.append(
                f"Duration: "
                f"{format_duration(duration)}"
            )

    lines.append("")

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    lines.append(
        "📈 PERFORMANCE"
    )

    lines.append(
        f"Open: "
        f"{performance['open']}/"
        f"{MAX_OPEN_TRADES}"
    )

    lines.append(
        f"Closed: "
        f"{performance['closed']}"
    )

    lines.append(
        f"Wins: "
        f"{performance['wins']}"
    )

    lines.append(
        f"Losses: "
        f"{performance['losses']}"
    )

    lines.append(
        f"Win Rate: "
        f"{performance['win_rate']:.1f}%"
    )

    lines.append(
        f"Realized PnL: "
        f"{performance['pnl']:+.3f}%"
    )

    lines.append(
        f"Time-Profit Exits: "
        f"{performance['time_exits']}"
    )

    lines.append("")

    # --------------------------------------------------------
    # TIME EXIT
    # --------------------------------------------------------

    lines.append(
        "⏱ TIME EXIT RULE"
    )

    lines.append(
        "After: 2 hours"
    )

    lines.append(
        "Minimum Profit: +1.50%"
    )

    lines.append(
        "Only profitable trades "
        "are closed by this rule."
    )

    lines.append("")

    # --------------------------------------------------------
    # SL / TP
    # --------------------------------------------------------

    lines.append(
        "🛡 SL / TP RULE"
    )

    lines.append(
        "SL source: "
        "5M Pullback Structure"
    )

    lines.append(
        f"Minimum SL: "
        f"{MIN_SL_PCT * 100:.2f}%"
    )

    lines.append(
        f"Maximum SL: "
        f"{MAX_SL_PCT * 100:.2f}%"
    )

    lines.append(
        f"Minimum RR: "
        f"{MIN_RR:.2f}"
    )

    lines.append(
        "SL outside range = REJECT"
    )

    lines.append("")

    # --------------------------------------------------------
    # ENTRY RULE
    # --------------------------------------------------------

    lines.append(
        "🎯 5M ENTRY RULE"
    )

    lines.append(
        "Structure Break"
    )

    lines.append(
        "↓"
    )

    lines.append(
        "Pullback to Break Level"
    )

    lines.append(
        "↓"
    )

    lines.append(
        "Bullish/Bearish Confirmation"
    )

    lines.append(
        "↓"
    )

    lines.append(
        "ENTRY"
    )

    lines.append("")

    lines.append(
        "🧪 PAPER TRADING"
    )

    return "\n".join(
        lines
    )


# ============================================================
# MAIN SCANNER
# ============================================================

def main():

    print(
        "=" * 60
    )

    print(
        "VOLUME-KHAT 100 v4.1"
    )

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
        "MODE: PAPER TRADING"
    )

    print(
        "=" * 60
    )

    print(
        "STARTING SCANNER"
    )

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    init_db()

    # --------------------------------------------------------
    # RESET
    # --------------------------------------------------------

    perform_v41_reset()

    # --------------------------------------------------------
    # UPDATE OPEN TRADES
    # --------------------------------------------------------

    update_open_trades()

    # --------------------------------------------------------
    # TOP MARKETS
    # --------------------------------------------------------

    markets = get_top_markets()

    if not markets:

        print(
            "No markets found."
        )

        report = build_report(
            [],
            None,
        )

        print(report)

        send_telegram(
            report
        )

        return

    markets = markets[:TOP_N]

    diagnostics[
        "scanned"
    ] = len(markets)

    # --------------------------------------------------------
    # Open trades
    # --------------------------------------------------------

    open_symbols = (
        get_open_symbols()
    )

    # --------------------------------------------------------
    # Candidates
    # --------------------------------------------------------

    candidates = []

    signals = []

    # ========================================================
    # SCAN
    # ========================================================

    for symbol in markets:

        try:

            # ------------------------------------------------
            # Already open
            # ------------------------------------------------

            if symbol in open_symbols:

                diagnostics[
                    "already_open"
                ] += 1

                continue

            # ------------------------------------------------
            # Cooldown
            # ------------------------------------------------

            if is_in_cooldown(
                symbol
            ):

                diagnostics[
                    "cooldown"
                ] += 1

                continue

            # ------------------------------------------------
            # 1H TREND
            # ------------------------------------------------

            candles_1h = (
                fetch_ohlcv(
                    symbol,
                    TIMEFRAME_1H,
                )
            )

            if not candles_1h:

                continue

            trend = get_trend(
                candles_1h
            )

            if trend == "NEUTRAL":

                diagnostics[
                    "trend_neutral"
                ] += 1

                continue

            diagnostics[
                "trend_ok"
            ] += 1

            # ------------------------------------------------
            # 15M SETUP
            # ------------------------------------------------

            candles_15m = (
                fetch_ohlcv(
                    symbol,
                    TIMEFRAME_15M,
                )
            )

            if not candles_15m:

                continue

            setup = detect_setup(
                candles_15m,
                trend,
            )

            if not setup:

                diagnostics[
                    "setup_failed"
                ] += 1

                continue

            if (
                setup["side"]
                != trend
            ):

                diagnostics[
                    "setup_failed"
                ] += 1

                continue

            diagnostics[
                "setup_passed"
            ] += 1

            # ------------------------------------------------
            # 5M
            # ------------------------------------------------

            candles_5m = (
                fetch_ohlcv(
                    symbol,
                    TIMEFRAME_5M,
                )
            )

            if not candles_5m:

                continue

            structure = (
                confirm_5m_structure(
                    candles_5m,
                    trend,
                )
            )

            if not structure:

                continue

            # ------------------------------------------------
            # ENTRY
            # ------------------------------------------------

            entry = (
                candles_5m[-1]["close"]
            )

            # ------------------------------------------------
            # 5M STRUCTURAL SL
            # ------------------------------------------------

            sl = (
                calculate_5m_structural_sl(
                    candles_5m,
                    structure,
                    trend,
                )
            )

            if sl is None:

                diagnostics[
                    "other"
                ] += 1

                continue

            # ------------------------------------------------
            # SL / TP
            # ------------------------------------------------

            sltp = build_sl_tp(
                entry,
                trend,
                sl,
            )

            # ------------------------------------------------
            # 5M RVOL
            # ------------------------------------------------

            rvol_5m = (
                calculate_rvol(
                    candles_5m
                )
            )

            # ------------------------------------------------
            # SCORE
            # ------------------------------------------------

            score = calculate_score(
                trend,
                setup,
                rvol_5m,
                True,
            )

            # ------------------------------------------------
            # Candidate
            # ------------------------------------------------

            candidate = {
                "symbol":
                    symbol,

                "side":
                    trend,

                "setup":
                    setup["type"],

                "score":
                    score,

                "rvol":
                    rvol_5m,

                "entry":
                    entry,

                "sl":
                    sl,

                "tp":
                    None,

                "sl_pct":
                    None,

                "rr":
                    None,

                "reason":
                    None,
            }

            # ------------------------------------------------
            # Invalid SL / TP
            # ------------------------------------------------

            if sltp is None:

                sl_pct = (
                    abs(
                        entry - sl
                    )
                    / entry
                    * 100
                )

                candidate[
                    "sl_pct"
                ] = sl_pct

                if sl_pct < (
                    MIN_SL_PCT * 100
                ):

                    candidate[
                        "reason"
                    ] = (
                        f"SL "
                        f"{sl_pct:.2f}% "
                        f"< MIN "
                        f"{MIN_SL_PCT * 100:.2f}%"
                    )

                elif sl_pct > (
                    MAX_SL_PCT * 100
                ):

                    candidate[
                        "reason"
                    ] = (
                        f"SL "
                        f"{sl_pct:.2f}% "
                        f"> MAX "
                        f"{MAX_SL_PCT * 100:.2f}%"
                    )

                else:

                    candidate[
                        "reason"
                    ] = (
                        "SL/TP calculation "
                        "failed"
                    )

                candidates.append(
                    candidate
                )

                continue

            # ------------------------------------------------
            # Valid SL / TP
            # ------------------------------------------------

            candidate[
                "tp"
            ] = sltp["tp"]

            candidate[
                "sl_pct"
            ] = sltp["sl_pct"]

            candidate[
                "rr"
            ] = sltp["rr"]

            # ------------------------------------------------
            # Score
            # ------------------------------------------------

            if score < MIN_SCORE:

                diagnostics[
                    "score_failed"
                ] += 1

                candidate[
                    "reason"
                ] = (
                    f"Score "
                    f"{score} "
                    f"< {MIN_SCORE}"
                )

                candidates.append(
                    candidate
                )

                continue

            # ------------------------------------------------
            # Valid candidate
            # ------------------------------------------------

            candidates.append(
                candidate
            )

        except Exception as exc:

            diagnostics[
                "errors"
            ] += 1

            print(
                f"SCAN ERROR | "
                f"{symbol} | "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    # ========================================================
    # SORT
    # ========================================================

    candidates.sort(
        key=lambda x: (
            x["score"],
            x["rvol"],
        ),
        reverse=True,
    )

    top_candidate = (
        candidates[0]
        if candidates
        else None
    )

    # ========================================================
    # CREATE SIGNALS
    # ========================================================

    available_slots = max(
        0,
        MAX_OPEN_TRADES
        - len(
            get_open_trades()
        ),
    )

    signal_limit = min(
        MAX_NEW_SIGNALS,
        available_slots,
    )

    for candidate in candidates:

        if len(signals) >= (
            signal_limit
        ):

            break

        if candidate["reason"] is not None:

            continue

        if (
            candidate["entry"]
            is None
            or candidate["sl"]
            is None
            or candidate["tp"]
            is None
        ):

            continue

        create_trade(
            candidate["symbol"],
            candidate["side"],
            candidate["entry"],
            candidate["sl"],
            candidate["tp"],
        )

        signals.append(
            candidate
        )

        open_symbols.add(
            candidate["symbol"]
        )

    # ========================================================
    # REPORT
    # ========================================================

    report = build_report(
        signals,
        top_candidate,
    )

    print("")

    print(report)

    print("")

    send_telegram(
        report
    )

    print(
        "=" * 60
    )

    print(
        "SCAN COMPLETE"
    )

    print(
        "=" * 60
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
