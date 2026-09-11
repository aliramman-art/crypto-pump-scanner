# ============================================================
# KRAKEN FUTURES VOLUME-KHAT 100 v4.1
# ============================================================
#
# 1H  = TREND
# 15M = SETUP
# 5M  = STRUCTURE BREAK + PULLBACK + CONFIRMATION + ENTRY
#
# CLOSED CANDLES ONLY
#
# v4.1 CHANGES:
#   - SL is now based on 5M pullback structure
#   - TP is now based on 5M structural risk
#   - 15M is used only for Trend/Setup context
#   - SL < 0.50% => REJECT
#   - SL > 1.50% => REJECT
#   - RR minimum 2.0
#   - 5M entry logic preserved
#   - Duration tracking
#   - Time-profit exit after 2h +1.50%
#   - v4.0 performance reset preserved
#   - PAPER TRADING ONLY
#
# ============================================================

import os
import time
import math
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

# Used only as structural SL buffer
ATR_SL_BUFFER = 0.15

# ------------------------------------------------------------
# RISK
# ------------------------------------------------------------

MIN_SL_PCT = 0.0050       # 0.50%
MAX_SL_PCT = 0.0150       # 1.50%

MIN_RR = 2.0

# ------------------------------------------------------------
# SCORING
# ------------------------------------------------------------

MIN_SCORE = 9

# ------------------------------------------------------------
# TRADES
# ------------------------------------------------------------

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

RESET_DATABASE_ON_V40_START = True
RESET_KEY = "VOLUME_KHAT_V40_RESET_DONE"

# ------------------------------------------------------------
# TIMEZONE
# ------------------------------------------------------------

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))


# ============================================================
# KRAKEN
# ============================================================

KRAKEN_OHLC_URL = "https://futures.kraken.com/api/charts/v1/"

KRAKEN_TICKER_URL = (
    "https://futures.kraken.com/derivatives/api/v3/tickers"
)


# ============================================================
# GLOBAL DIAGNOSTICS
# ============================================================

DIAGNOSTICS = {}


def reset_diagnostics():

    global DIAGNOSTICS

    DIAGNOSTICS = {
        "scanned": 0,

        "trend_neutral": 0,
        "trend_ok": 0,

        "setup_failed": 0,
        "setup_passed": 0,

        "structure_break_failed": 0,
        "pullback_failed": 0,
        "confirmation_failed": 0,
        "full_5m_confirmation": 0,

        "score_low": 0,

        "cooldown": 0,
        "already_open": 0,

        "sl_low": 0,
        "sl_high": 0,

        "rr_low": 0,

        "other": 0,
        "errors": 0,
    }


# ============================================================
# DATABASE
# ============================================================

def db():

    return sqlite3.connect(DB_FILE)


def init_db():

    conn = db()
    cur = conn.cursor()

    cur.execute("""
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
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS system_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.commit()

    # --------------------------------------------------------
    # Migration
    # --------------------------------------------------------

    cur.execute("PRAGMA table_info(trades)")
    columns = [row[1] for row in cur.fetchall()]

    migrations = {
        "exit_reason": "TEXT",
        "duration_minutes": "REAL",
        "closed_reported": "INTEGER DEFAULT 0",
    }

    for col, dtype in migrations.items():

        if col not in columns:

            cur.execute(
                f"ALTER TABLE trades ADD COLUMN {col} {dtype}"
            )

    conn.commit()
    conn.close()


# ============================================================
# V4.0 RESET
# ============================================================

def perform_v40_reset():

    if not RESET_DATABASE_ON_V40_START:
        return

    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT value FROM system_meta WHERE key=?",
        (RESET_KEY,)
    )

    row = cur.fetchone()

    if row is None:

        cur.execute("DELETE FROM trades")

        cur.execute(
            """
            INSERT INTO system_meta(key,value)
            VALUES(?,?)
            """,
            (RESET_KEY, "1")
        )

        conn.commit()

        print("V4.0 performance reset completed.")

    conn.close()


# ============================================================
# TIME
# ============================================================

def now_utc():

    return datetime.now(timezone.utc)


def now_iran():

    return datetime.now(IRAN_TZ)


def iso_now():

    return now_utc().isoformat()


def parse_time(value):

    if not value:
        return None

    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


# ============================================================
# OHLCV
# ============================================================

def fetch_ohlcv(symbol, timeframe):

    try:

        url = f"{KRAKEN_OHLC_URL}{timeframe}/{symbol}"

        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        candles = data.get("candles", [])

        if not candles:
            return []

        result = []

        for c in candles:

            try:

                result.append({
                    "time": int(c["time"]),
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                    "volume": float(c["volume"]),
                })

            except Exception:
                continue

        result.sort(key=lambda x: x["time"])

        # remove duplicates
        unique = {}

        for candle in result:
            unique[candle["time"]] = candle

        result = list(unique.values())
        result.sort(key=lambda x: x["time"])

        # ----------------------------------------------------
        # Remove currently forming candle
        # ----------------------------------------------------

        interval_seconds = {
            "5m": 300,
            "15m": 900,
            "1h": 3600
        }[timeframe]

        current_ts = int(time.time())

        closed = []

        for candle in result:

            candle_end = candle["time"] + interval_seconds

            if candle_end <= current_ts:
                closed.append(candle)

        return closed[-OHLCV_LIMIT:]

    except Exception as e:

        print(f"OHLCV ERROR {symbol} {timeframe}: {e}")

        DIAGNOSTICS["errors"] += 1

        return []


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(candles, period=RVOL_PERIOD):

    if len(candles) < period + 1:
        return 0.0

    current_volume = candles[-1]["volume"]

    previous = candles[-period-1:-1]

    avg_volume = sum(
        c["volume"] for c in previous
    ) / len(previous)

    if avg_volume <= 0:
        return 0.0

    return current_volume / avg_volume


# ============================================================
# ATR
# ============================================================

def calculate_atr(candles, period=ATR_PERIOD):

    if len(candles) < period + 1:
        return 0.0

    trs = []

    for i in range(1, len(candles)):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(
            current["high"] - current["low"],
            abs(current["high"] - previous["close"]),
            abs(current["low"] - previous["close"])
        )

        trs.append(tr)

    if len(trs) < period:
        return 0.0

    return sum(trs[-period:]) / period


# ============================================================
# SMA
# ============================================================

def sma(values, period):

    if len(values) < period:
        return None

    return sum(values[-period:]) / period


# ============================================================
# 1H TREND
# ============================================================

def get_trend(candles):

    if len(candles) < 60:
        return "NEUTRAL"

    closes = [c["close"] for c in candles]

    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)

    close = closes[-1]

    if close > sma20 > sma50:
        return "LONG"

    if close < sma20 < sma50:
        return "SHORT"

    return "NEUTRAL"


# ============================================================
# 15M BREAKOUT
# ============================================================

def detect_breakout(candles, trend):

    if len(candles) < BREAKOUT_LOOKBACK + RVOL_PERIOD + 2:
        return None

    current = candles[-1]

    previous = candles[
        -BREAKOUT_LOOKBACK-1:-1
    ]

    resistance = max(
        c["high"] for c in previous
    )

    support = min(
        c["low"] for c in previous
    )

    rvol = calculate_rvol(candles)

    if trend == "LONG":

        if (
            current["close"] > resistance
            and rvol >= RVOL_ABNORMAL
        ):

            return {
                "type": "BREAKOUT",
                "direction": "LONG",
                "level": resistance,
                "rvol": rvol,
            }

    if trend == "SHORT":

        if (
            current["close"] < support
            and rvol >= RVOL_ABNORMAL
        ):

            return {
                "type": "BREAKOUT",
                "direction": "SHORT",
                "level": support,
                "rvol": rvol,
            }

    return None


# ============================================================
# 15M REJECTION
# ============================================================

def detect_rejection(candles, trend):

    if len(candles) < SUPPORT_RESISTANCE_LOOKBACK + 2:
        return None

    current = candles[-1]

    previous = candles[
        -SUPPORT_RESISTANCE_LOOKBACK-1:-1
    ]

    resistance = max(
        c["high"] for c in previous
    )

    support = min(
        c["low"] for c in previous
    )

    body = abs(
        current["close"] - current["open"]
    )

    candle_range = current["high"] - current["low"]

    if candle_range <= 0:
        return None

    if body <= 0:
        return None

    rvol = calculate_rvol(candles)

    # --------------------------------------------------------
    # SHORT rejection at resistance
    # --------------------------------------------------------

    if trend == "SHORT":

        distance = abs(
            current["high"] - resistance
        ) / resistance

        upper_wick = (
            current["high"]
            - max(current["open"], current["close"])
        )

        if (
            distance <= REJECTION_DISTANCE
            and upper_wick / body >= WICK_BODY_RATIO
            and body / candle_range >= MIN_BODY_RATIO
            and rvol >= RVOL_ABNORMAL
        ):

            return {
                "type": "REJECTION",
                "direction": "SHORT",
                "level": resistance,
                "rvol": rvol,
            }

    # --------------------------------------------------------
    # LONG rejection at support
    # --------------------------------------------------------

    if trend == "LONG":

        distance = abs(
            current["low"] - support
        ) / support

        lower_wick = (
            min(current["open"], current["close"])
            - current["low"]
        )

        if (
            distance <= REJECTION_DISTANCE
            and lower_wick / body >= WICK_BODY_RATIO
            and body / candle_range >= MIN_BODY_RATIO
            and rvol >= RVOL_ABNORMAL
        ):

            return {
                "type": "REJECTION",
                "direction": "LONG",
                "level": support,
                "rvol": rvol,
            }

    return None


# ============================================================
# 15M SETUP
# ============================================================

def detect_setup(candles, trend):

    breakout = detect_breakout(
        candles,
        trend
    )

    if breakout:
        return breakout

    rejection = detect_rejection(
        candles,
        trend
    )

    if rejection:
        return rejection

    return None


# ============================================================
# 5M STRUCTURE
# ============================================================

def confirm_5m_structure(candles, trend):

    if len(candles) < 30:
        return {
            "confirmed": False,
            "stage": "STRUCTURE"
        }

    latest_index = len(candles) - 1

    break_data = None

    # --------------------------------------------------------
    # Find latest structure break
    # --------------------------------------------------------

    start = max(
        STRUCTURE_LOOKBACK_5M,
        len(candles) - 20
    )

    for i in range(
        start,
        latest_index
    ):

        current = candles[i]

        previous = candles[
            i - STRUCTURE_LOOKBACK_5M:i
        ]

        if len(previous) < STRUCTURE_LOOKBACK_5M:
            continue

        previous_high = max(
            c["high"] for c in previous
        )

        previous_low = min(
            c["low"] for c in previous
        )

        if trend == "LONG":

            if current["close"] > previous_high:

                break_data = {
                    "index": i,
                    "level": previous_high,
                    "direction": "LONG"
                }

        elif trend == "SHORT":

            if current["close"] < previous_low:

                break_data = {
                    "index": i,
                    "level": previous_low,
                    "direction": "SHORT"
                }

    if not break_data:

        return {
            "confirmed": False,
            "stage": "STRUCTURE"
        }

    break_index = break_data["index"]
    break_level = break_data["level"]

    # Break must be recent
    if latest_index - break_index > MAX_PULLBACK_CANDLES:

        return {
            "confirmed": False,
            "stage": "STRUCTURE"
        }

    # Need candles after break
    if latest_index - break_index < 2:

        return {
            "confirmed": False,
            "stage": "PULLBACK"
        }

    # --------------------------------------------------------
    # Find pullback
    # --------------------------------------------------------

    pullback_index = None

    for i in range(
        break_index + 1,
        latest_index
    ):

        candle = candles[i]

        if trend == "LONG":

            distance = abs(
                candle["low"] - break_level
            ) / break_level

            touched = distance <= PULLBACK_TOLERANCE

            invalidated = (
                candle["close"]
                < break_level * (1 - PULLBACK_TOLERANCE)
            )

            if touched and not invalidated:

                pullback_index = i

        elif trend == "SHORT":

            distance = abs(
                candle["high"] - break_level
            ) / break_level

            touched = distance <= PULLBACK_TOLERANCE

            invalidated = (
                candle["close"]
                > break_level * (1 + PULLBACK_TOLERANCE)
            )

            if touched and not invalidated:

                pullback_index = i

    if pullback_index is None:

        return {
            "confirmed": False,
            "stage": "PULLBACK",
            "break_level": break_level,
            "break_index": break_index
        }

    # --------------------------------------------------------
    # Confirmation candle = latest closed candle
    # --------------------------------------------------------

    confirmation = candles[-1]

    candle_range = (
        confirmation["high"]
        - confirmation["low"]
    )

    if candle_range <= 0:

        return {
            "confirmed": False,
            "stage": "CONFIRMATION",
            "break_level": break_level,
            "break_index": break_index,
            "pullback_index": pullback_index
        }

    body = abs(
        confirmation["close"]
        - confirmation["open"]
    )

    body_ratio = body / candle_range

    if body_ratio < MIN_CONFIRM_BODY_RATIO:

        return {
            "confirmed": False,
            "stage": "CONFIRMATION",
            "break_level": break_level,
            "break_index": break_index,
            "pullback_index": pullback_index
        }

    # --------------------------------------------------------
    # LONG confirmation
    # --------------------------------------------------------

    if trend == "LONG":

        bullish = (
            confirmation["close"]
            > confirmation["open"]
        )

        above_level = (
            confirmation["close"]
            > break_level
        )

        if bullish and above_level:

            return {
                "confirmed": True,
                "stage": "CONFIRMED",

                "break_level": break_level,
                "break_index": break_index,

                "pullback_index": pullback_index,
                "confirmation_index": latest_index,

                "pullback_low": min(
                    c["low"]
                    for c in candles[
                        pullback_index:latest_index
                    ]
                ),
            }

    # --------------------------------------------------------
    # SHORT confirmation
    # --------------------------------------------------------

    if trend == "SHORT":

        bearish = (
            confirmation["close"]
            < confirmation["open"]
        )

        below_level = (
            confirmation["close"]
            < break_level
        )

        if bearish and below_level:

            return {
                "confirmed": True,
                "stage": "CONFIRMED",

                "break_level": break_level,
                "break_index": break_index,

                "pullback_index": pullback_index,
                "confirmation_index": latest_index,

                "pullback_high": max(
                    c["high"]
                    for c in candles[
                        pullback_index:latest_index
                    ]
                ),
            }

    return {
        "confirmed": False,
        "stage": "CONFIRMATION",

        "break_level": break_level,
        "break_index": break_index,

        "pullback_index": pullback_index
    }


# ============================================================
# 5M STRUCTURAL SL
# ============================================================

def calculate_5m_structural_sl(
    candles,
    confirmation_data,
    trend
):

    if not confirmation_data.get("confirmed"):
        return None

    atr = calculate_atr(candles)

    if atr <= 0:
        return None

    pullback_index = confirmation_data["pullback_index"]
    confirmation_index = confirmation_data["confirmation_index"]

    # --------------------------------------------------------
    # Use pullback structure only
    # --------------------------------------------------------

    structure_start = max(
        0,
        pullback_index
    )

    structure_end = confirmation_index

    structure_candles = candles[
        structure_start:structure_end
    ]

    if not structure_candles:
        return None

    if trend == "LONG":

        structural_low = min(
            c["low"]
            for c in structure_candles
        )

        sl = (
            structural_low
            - atr * ATR_SL_BUFFER
        )

        return sl

    if trend == "SHORT":

        structural_high = max(
            c["high"]
            for c in structure_candles
        )

        sl = (
            structural_high
            + atr * ATR_SL_BUFFER
        )

        return sl

    return None


# ============================================================
# TP / SL BUILDER
# ============================================================

def build_sl_tp(
    entry,
    candles_5m,
    confirmation_data,
    trend
):

    if entry <= 0:
        return None

    sl = calculate_5m_structural_sl(
        candles_5m,
        confirmation_data,
        trend
    )

    if sl is None:
        return None

    # --------------------------------------------------------
    # Validate direction
    # --------------------------------------------------------

    if trend == "LONG":

        if sl >= entry:
            return None

        risk = entry - sl

    elif trend == "SHORT":

        if sl <= entry:
            return None

        risk = sl - entry

    else:

        return None

    if risk <= 0:
        return None

    # --------------------------------------------------------
    # SL percentage
    # --------------------------------------------------------

    sl_pct = (
        risk / entry
    ) * 100.0

    # --------------------------------------------------------
    # SL must be between 0.50% and 1.50%
    # --------------------------------------------------------

    if sl_pct < MIN_SL_PCT * 100:

        return {
            "valid": False,
            "reason": "SL < 0.50%",
            "sl": sl,
            "sl_pct": sl_pct
        }

    if sl_pct > MAX_SL_PCT * 100:

        return {
            "valid": False,
            "reason": "SL > 1.50%",
            "sl": sl,
            "sl_pct": sl_pct
        }

    # --------------------------------------------------------
    # TP = minimum 2R
    # --------------------------------------------------------

    if trend == "LONG":

        tp = entry + (
            risk * MIN_RR
        )

    else:

        tp = entry - (
            risk * MIN_RR
        )

    rr = (
        abs(tp - entry)
        / risk
    )

    if rr < MIN_RR:

        return {
            "valid": False,
            "reason": "RR < 2.00",
            "sl": sl,
            "tp": tp,
            "sl_pct": sl_pct,
            "rr": rr
        }

    return {
        "valid": True,

        "entry": entry,
        "sl": sl,
        "tp": tp,

        "sl_pct": sl_pct,
        "rr": rr
    }


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    trend,
    setup,
    rvol_5m,
    confirmation
):

    score = 0

    # Trend
    if trend in ("LONG", "SHORT"):
        score += 3

    # Setup
    if setup:
        score += 4

    # 5M RVOL
    if rvol_5m >= RVOL_VERY_STRONG:
        score += 4

    elif rvol_5m >= RVOL_STRONG:
        score += 4

    elif rvol_5m >= RVOL_ABNORMAL:
        score += 3

    # 5M confirmation
    if confirmation:
        score += 4

    return score


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            symbol,
            side,
            entry,
            sl,
            tp,
            entry_time
        FROM trades
        WHERE exit_time IS NULL
        ORDER BY entry_time ASC
    """)

    rows = cur.fetchall()

    conn.close()

    result = []

    for row in rows:

        result.append({
            "id": row[0],
            "symbol": row[1],
            "side": row[2],
            "entry": float(row[3]),
            "sl": float(row[4]),
            "tp": float(row[5]),
            "entry_time": row[6]
        })

    return result


# ============================================================
# COOLDOWN
# ============================================================

def cooldown_active(symbol):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT exit_time
        FROM trades
        WHERE symbol=?
          AND exit_time IS NOT NULL
        ORDER BY exit_time DESC
        LIMIT 1
    """, (symbol,))

    row = cur.fetchone()

    conn.close()

    if not row:
        return False

    exit_time = parse_time(row[0])

    if not exit_time:
        return False

    cooldown_minutes = (
        COOLDOWN_CANDLES * 5
    )

    elapsed = (
        now_utc() - exit_time
    ).total_seconds() / 60

    return elapsed < cooldown_minutes


# ============================================================
# INSERT TRADE
# ============================================================

def insert_trade(
    symbol,
    side,
    entry,
    sl,
    tp
):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO trades(
            symbol,
            side,
            entry,
            sl,
            tp,
            entry_time
        )
        VALUES(?,?,?,?,?,?)
    """, (
        symbol,
        side,
        entry,
        sl,
        tp,
        iso_now()
    ))

    conn.commit()
    conn.close()


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(symbol):

    try:

        response = requests.get(
            KRAKEN_TICKER_URL,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        tickers = data.get("tickers", [])

        for ticker in tickers:

            if ticker.get("symbol") == symbol:

                for key in (
                    "last",
                    "lastPrice",
                    "markPrice"
                ):

                    value = ticker.get(key)

                    if value is not None:
                        return float(value)

        return None

    except Exception as e:

        print(
            f"TICKER ERROR {symbol}: {e}"
        )

        return None


# ============================================================
# PNL
# ============================================================

def calculate_pnl(
    side,
    entry,
    price
):

    if entry <= 0:
        return 0.0

    if side == "LONG":

        return (
            (price - entry)
            / entry
        ) * 100.0

    return (
        (entry - price)
        / entry
    ) * 100.0


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade_id,
    exit_price,
    exit_reason
):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            side,
            entry,
            entry_time
        FROM trades
        WHERE id=?
          AND exit_time IS NULL
    """, (trade_id,))

    row = cur.fetchone()

    if not row:

        conn.close()
        return

    side = row[0]
    entry = float(row[1])
    entry_time = parse_time(row[2])

    pnl = calculate_pnl(
        side,
        entry,
        exit_price
    )

    exit_time = now_utc()

    duration_minutes = 0.0

    if entry_time:

        duration_minutes = (
            exit_time - entry_time
        ).total_seconds() / 60

    result = (
        "WIN"
        if pnl > 0
        else "LOSS"
    )

    cur.execute("""
        UPDATE trades
        SET
            exit=?,
            exit_time=?,
            pnl_pct=?,
            result=?,
            exit_reason=?,
            duration_minutes=?,
            closed_reported=0
        WHERE id=?
    """, (
        exit_price,
        exit_time.isoformat(),
        pnl,
        result,
        exit_reason,
        duration_minutes,
        trade_id
    ))

    conn.commit()
    conn.close()


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades():

    trades = get_open_trades()

    if not trades:
        return

    for trade in trades:

        symbol = trade["symbol"]
        side = trade["side"]

        candles = fetch_ohlcv(
            symbol,
            TIMEFRAME_5M
        )

        if not candles:
            continue

        latest = candles[-1]

        # ----------------------------------------------------
        # SL / TP
        # ----------------------------------------------------

        if side == "LONG":

            # Conservative order:
            # if same candle hits both,
            # SL is assumed first.

            if latest["low"] <= trade["sl"]:

                close_trade(
                    trade["id"],
                    trade["sl"],
                    "SL"
                )

                continue

            if latest["high"] >= trade["tp"]:

                close_trade(
                    trade["id"],
                    trade["tp"],
                    "TP"
                )

                continue

        else:

            if latest["high"] >= trade["sl"]:

                close_trade(
                    trade["id"],
                    trade["sl"],
                    "SL"
                )

                continue

            if latest["low"] <= trade["tp"]:

                close_trade(
                    trade["id"],
                    trade["tp"],
                    "TP"
                )

                continue

        # ----------------------------------------------------
        # Time-profit exit
        # ----------------------------------------------------

        entry_time = parse_time(
            trade["entry_time"]
        )

        if not entry_time:
            continue

        elapsed_minutes = (
            now_utc() - entry_time
        ).total_seconds() / 60

        if elapsed_minutes < TIME_EXIT_MINUTES:
            continue

        current_price = get_current_price(
            symbol
        )

        if current_price is None:
            current_price = latest["close"]

        pnl = calculate_pnl(
            side,
            trade["entry"],
            current_price
        )

        if pnl >= TIME_EXIT_MIN_PROFIT_PCT:

            close_trade(
                trade["id"],
                current_price,
                "TIME_PROFIT"
            )


# ============================================================
# PERFORMANCE
# ============================================================

def get_performance():

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            COUNT(*),
            SUM(
                CASE
                    WHEN result='WIN' THEN 1
                    ELSE 0
                END
            ),
            SUM(
                CASE
                    WHEN result='LOSS' THEN 1
                    ELSE 0
                END
            ),
            COALESCE(SUM(pnl_pct),0),
            SUM(
                CASE
                    WHEN exit_reason='TIME_PROFIT'
                    THEN 1
                    ELSE 0
                END
            )
        FROM trades
        WHERE exit_time IS NOT NULL
    """)

    row = cur.fetchone()

    conn.close()

    closed = int(row[0] or 0)
    wins = int(row[1] or 0)
    losses = int(row[2] or 0)

    pnl = float(row[3] or 0)
    time_exits = int(row[4] or 0)

    win_rate = (
        wins / closed * 100
        if closed > 0
        else 0
    )

    return {
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "pnl": pnl,
        "win_rate": win_rate,
        "time_exits": time_exits
    }


# ============================================================
# DURATION
# ============================================================

def duration_text(entry_time):

    dt = parse_time(entry_time)

    if not dt:
        return "N/A"

    minutes = (
        now_utc() - dt
    ).total_seconds() / 60

    if minutes < 0:
        minutes = 0

    hours = int(minutes // 60)
    mins = int(minutes % 60)

    return f"{hours}h {mins}m"


# ============================================================
# SCAN
# ============================================================

def scan_markets(markets):

    reset_diagnostics()

    candidates = []

    top_candidate = None

    top_rejection = None

    open_trades = get_open_trades()

    open_symbols = {
        t["symbol"]
        for t in open_trades
    }

    available_slots = max(
        0,
        MAX_OPEN_TRADES - len(open_trades)
    )

    for symbol in markets:

        DIAGNOSTICS["scanned"] += 1

        try:

            # ------------------------------------------------
            # 1H
            # ------------------------------------------------

            candles_1h = fetch_ohlcv(
                symbol,
                TIMEFRAME_1H
            )

            if len(candles_1h) < 60:

                DIAGNOSTICS["errors"] += 1
                continue

            trend = get_trend(
                candles_1h
            )

            if trend == "NEUTRAL":

                DIAGNOSTICS["trend_neutral"] += 1
                continue

            DIAGNOSTICS["trend_ok"] += 1

            # ------------------------------------------------
            # 15M
            # ------------------------------------------------

            candles_15m = fetch_ohlcv(
                symbol,
                TIMEFRAME_15M
            )

            if len(candles_15m) < 60:

                DIAGNOSTICS["errors"] += 1
                continue

            setup = detect_setup(
                candles_15m,
                trend
            )

            if not setup:

                DIAGNOSTICS["setup_failed"] += 1
                continue

            DIAGNOSTICS["setup_passed"] += 1

            # ------------------------------------------------
            # 5M
            # ------------------------------------------------

            candles_5m = fetch_ohlcv(
                symbol,
                TIMEFRAME_5M
            )

            if len(candles_5m) < 40:

                DIAGNOSTICS["errors"] += 1
                continue

            confirmation_data = (
                confirm_5m_structure(
                    candles_5m,
                    trend
                )
            )

            stage = confirmation_data.get(
                "stage"
            )

            if stage == "STRUCTURE":

                DIAGNOSTICS[
                    "structure_break_failed"
                ] += 1

                continue

            if stage == "PULLBACK":

                DIAGNOSTICS[
                    "pullback_failed"
                ] += 1

                continue

            if not confirmation_data.get(
                "confirmed"
            ):

                DIAGNOSTICS[
                    "confirmation_failed"
                ] += 1

                continue

            DIAGNOSTICS[
                "full_5m_confirmation"
            ] += 1

            # ------------------------------------------------
            # Entry
            # ------------------------------------------------

            entry = candles_5m[-1]["close"]

            rvol_5m = calculate_rvol(
                candles_5m
            )

            score = calculate_score(
                trend,
                setup,
                rvol_5m,
                True
            )

            # ------------------------------------------------
            # Candidate object
            # ------------------------------------------------

            candidate = {
                "symbol": symbol,
                "side": trend,

                "entry": entry,

                "setup": setup["type"],

                "score": score,

                "rvol_5m": rvol_5m,

                "confirmation": confirmation_data
            }

            # ------------------------------------------------
            # Top candidate
            # ------------------------------------------------

            if (
                top_candidate is None
                or (
                    score,
                    rvol_5m
                )
                >
                (
                    top_candidate["score"],
                    top_candidate["rvol_5m"]
                )
            ):

                top_candidate = candidate.copy()

            # ------------------------------------------------
            # Score
            # ------------------------------------------------

            if score < MIN_SCORE:

                DIAGNOSTICS["score_low"] += 1

                candidate["rejection"] = (
                    f"Score {score} < {MIN_SCORE}"
                )

                top_rejection = candidate

                continue

            # ------------------------------------------------
            # Cooldown
            # ------------------------------------------------

            if cooldown_active(symbol):

                DIAGNOSTICS["cooldown"] += 1

                candidate["rejection"] = (
                    "Cooldown Active"
                )

                top_rejection = candidate

                continue

            # ------------------------------------------------
            # Already open
            # ------------------------------------------------

            if symbol in open_symbols:

                DIAGNOSTICS["already_open"] += 1

                candidate["rejection"] = (
                    "Already Open"
                )

                top_rejection = candidate

                continue

            # ------------------------------------------------
            # Build 5M SL / TP
            # ------------------------------------------------

            risk = build_sl_tp(
                entry,
                candles_5m,
                confirmation_data,
                trend
            )

            if not risk:

                DIAGNOSTICS["other"] += 1

                candidate["rejection"] = (
                    "SL/TP Calculation Failed"
                )

                top_rejection = candidate

                continue

            candidate.update({
                "sl": risk.get("sl"),
                "tp": risk.get("tp"),
                "sl_pct": risk.get("sl_pct"),
                "rr": risk.get("rr")
            })

            # ------------------------------------------------
            # SL low
            # ------------------------------------------------

            if (
                not risk["valid"]
                and risk["reason"]
                == "SL < 0.50%"
            ):

                DIAGNOSTICS["sl_low"] += 1

                candidate["rejection"] = (
                    f"SL {risk['sl_pct']:.2f}% < MIN 0.50%"
                )

                top_rejection = candidate

                continue

            # ------------------------------------------------
            # SL high
            # ------------------------------------------------

            if (
                not risk["valid"]
                and risk["reason"]
                == "SL > 1.50%"
            ):

                DIAGNOSTICS["sl_high"] += 1

                candidate["rejection"] = (
                    f"SL {risk['sl_pct']:.2f}% > MAX 1.50%"
                )

                top_rejection = candidate

                continue

            # ------------------------------------------------
            # RR
            # ------------------------------------------------

            if (
                not risk["valid"]
                and risk["reason"]
                == "RR < 2.00"
            ):

                DIAGNOSTICS["rr_low"] += 1

                candidate["rejection"] = (
                    f"RR {risk.get('rr', 0):.2f} < 2.00"
                )

                top_rejection = candidate

                continue

            # ------------------------------------------------
            # Valid
            # ------------------------------------------------

            candidate["rejection"] = None

            candidates.append(candidate)

        except Exception as e:

            DIAGNOSTICS["errors"] += 1

            print(
                f"SCAN ERROR {symbol}: {e}"
            )

    # ========================================================
    # Rank valid candidates
    # ========================================================

    candidates.sort(
        key=lambda x: (
            x["score"],
            x["rvol_5m"]
        ),
        reverse=True
    )

    selected = candidates[
        :min(
            MAX_NEW_SIGNALS,
            available_slots
        )
    ]

    # ========================================================
    # Open selected trades
    # ========================================================

    new_signals = []

    for candidate in selected:

        insert_trade(
            candidate["symbol"],
            candidate["side"],
            candidate["entry"],
            candidate["sl"],
            candidate["tp"]
        )

        new_signals.append(candidate)

    # ========================================================
    # If no valid selected candidate,
    # show best rejection
    # ========================================================

    if top_rejection is None:

        top_rejection = top_candidate

    return {
        "new_signals": new_signals,
        "top_candidate": top_candidate,
        "top_rejection": top_rejection
    }


# ============================================================
# MARKETS
# ============================================================

def get_top_markets():

    try:

        response = requests.get(
            KRAKEN_TICKER_URL,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        tickers = data.get(
            "tickers",
            []
        )

        markets = []

        for ticker in tickers:

            symbol = ticker.get("symbol")

            if not symbol:
                continue

            # Only USD perpetual style symbols
            if not symbol.startswith("PF_"):
                continue

            if not symbol.endswith("USD"):
                continue

            try:

                volume = float(
                    ticker.get(
                        "vol24h",
                        0
                    )
                )

            except Exception:

                volume = 0

            markets.append(
                (
                    symbol,
                    volume
                )
            )

        markets.sort(
            key=lambda x: x[1],
            reverse=True
        )

        return [
            symbol
            for symbol, _ in markets[:TOP_N]
        ]

    except Exception as e:

        print(
            f"MARKETS ERROR: {e}"
        )

        DIAGNOSTICS["errors"] += 1

        return []


# ============================================================
# REPORT HELPERS
# ============================================================

def fmt_price(value):

    if value is None:
        return "N/A"

    if value >= 1000:
        return f"{value:.2f}"

    if value >= 1:
        return f"{value:.4f}"

    return f"{value:.8f}"


def build_report(scan_result):

    new_signals = scan_result[
        "new_signals"
    ]

    top_candidate = scan_result[
        "top_candidate"
    ]

    top_rejection = scan_result[
        "top_rejection"
    ]

    open_trades = get_open_trades()

    performance = get_performance()

    lines = []

    lines.append(
        "📊 VOLUME-KHAT 100"
    )

    lines.append(
        f"🕐 {now_iran().strftime('%Y/%m/%d %H:%M:%S')}"
    )

    lines.append(
        "⚡ Kraken Futures | 5M CLOSED | TOP 100"
    )

    lines.append(
        "🧠 5M: BREAKOUT + PULLBACK + CONFIRMATION"
    )

    # --------------------------------------------------------
    # NEW SIGNALS
    # --------------------------------------------------------

    lines.append("")
    lines.append("🎯 NEW SIGNALS")

    if not new_signals:

        lines.append("None")

    else:

        for s in new_signals:

            lines.append(
                f"🟢 {s['symbol']} {s['side']}"
            )

            lines.append(
                f"Entry: {fmt_price(s['entry'])}"
            )

            lines.append(
                f"SL: {fmt_price(s['sl'])} "
                f"({s['sl_pct']:.2f}%)"
            )

            lines.append(
                f"TP: {fmt_price(s['tp'])}"
            )

            lines.append(
                f"RR: {s['rr']:.2f}"
            )

            lines.append(
                f"Setup: {s['setup']}"
            )

            lines.append(
                f"Score: {s['score']}/15"
            )

            lines.append(
                f"5M RVOL: {s['rvol_5m']:.2f}x"
            )

            lines.append("")

    # --------------------------------------------------------
    # TOP CANDIDATE
    # --------------------------------------------------------

    lines.append("🏆 TOP CANDIDATE")

    if not top_candidate:

        lines.append("None")

    else:

        side_icon = (
            "🟢"
            if top_candidate["side"] == "LONG"
            else "🔴"
        )

        lines.append(
            f"{side_icon} "
            f"{top_candidate['symbol']} "
            f"{top_candidate['side']}"
        )

        lines.append(
            f"Setup: {top_candidate['setup']}"
        )

        lines.append(
            f"Score: {top_candidate['score']}/15"
        )

        lines.append(
            f"5M RVOL: "
            f"{top_candidate['rvol_5m']:.2f}x"
        )

        rejection = (
            top_rejection.get("rejection")
            if top_rejection
            else None
        )

        if rejection:

            lines.append("❌ REJECTED")
            lines.append(
                f"Reason: {rejection}"
            )

    # --------------------------------------------------------
    # DIAGNOSTICS
    # --------------------------------------------------------

    lines.append("")
    lines.append("🔎 FILTER DIAGNOSTICS")

    lines.append(
        f"Scanned: {DIAGNOSTICS['scanned']}"
    )

    lines.append("1H FILTER")

    lines.append(
        f"❌ Neutral: "
        f"{DIAGNOSTICS['trend_neutral']}"
    )

    lines.append(
        f"✅ Trend OK: "
        f"{DIAGNOSTICS['trend_ok']}"
    )

    lines.append("15M SETUP")

    lines.append(
        f"❌ Setup Failed: "
        f"{DIAGNOSTICS['setup_failed']}"
    )

    lines.append(
        f"✅ Setup Passed: "
        f"{DIAGNOSTICS['setup_passed']}"
    )

    lines.append("5M STRUCTURE")

    lines.append(
        f"❌ Structure Break Failed: "
        f"{DIAGNOSTICS['structure_break_failed']}"
    )

    lines.append(
        f"❌ Pullback Failed: "
        f"{DIAGNOSTICS['pullback_failed']}"
    )

    lines.append(
        f"❌ Confirmation Failed: "
        f"{DIAGNOSTICS['confirmation_failed']}"
    )

    lines.append(
        f"✅ Full 5M Confirmation: "
        f"{DIAGNOSTICS['full_5m_confirmation']}"
    )

    lines.append(
        f"❌ Score < 9: "
        f"{DIAGNOSTICS['score_low']}"
    )

    lines.append(
        f"❌ Cooldown: "
        f"{DIAGNOSTICS['cooldown']}"
    )

    lines.append(
        f"❌ Already Open: "
        f"{DIAGNOSTICS['already_open']}"
    )

    lines.append(
        f"❌ SL < 0.50%: "
        f"{DIAGNOSTICS['sl_low']}"
    )

    lines.append(
        f"❌ SL > 1.50%: "
        f"{DIAGNOSTICS['sl_high']}"
    )

    lines.append(
        f"❌ RR < 2.00: "
        f"{DIAGNOSTICS['rr_low']}"
    )

    lines.append(
        f"❌ Other: "
        f"{DIAGNOSTICS['other']}"
    )

    lines.append(
        f"⚠️ Errors: "
        f"{DIAGNOSTICS['errors']}"
    )

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    lines.append("")

    lines.append(
        f"📂 OPEN TRADES "
        f"({len(open_trades)}/{MAX_OPEN_TRADES})"
    )

    if not open_trades:

        lines.append("None")

    else:

        for trade in open_trades:

            current = get_current_price(
                trade["symbol"]
            )

            if current is None:
                current = trade["entry"]

            pnl = calculate_pnl(
                trade["side"],
                trade["entry"],
                current
            )

            icon = (
                "🟢"
                if pnl >= 0
                else "🔴"
            )

            lines.append(
                f"{icon} {trade['symbol']} "
                f"{trade['side']}"
            )

            lines.append(
                f"Entry: "
                f"{fmt_price(trade['entry'])}"
            )

            lines.append(
                f"Live: "
                f"{fmt_price(current)}"
            )

            lines.append(
                f"PnL: {pnl:+.2f}%"
            )

            lines.append(
                f"SL: {fmt_price(trade['sl'])}"
            )

            lines.append(
                f"TP: {fmt_price(trade['tp'])}"
            )

            lines.append(
                f"Duration: "
                f"{duration_text(trade['entry_time'])}"
            )

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    lines.append("")
    lines.append("📈 PERFORMANCE")

    lines.append(
        f"Open: "
        f"{len(open_trades)}/{MAX_OPEN_TRADES}"
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

    # --------------------------------------------------------
    # RULES
    # --------------------------------------------------------

    lines.append("")
    lines.append("⏱ TIME EXIT RULE")

    lines.append(
        "After: 2 hours"
    )

    lines.append(
        "Minimum Profit: +1.50%"
    )

    lines.append(
        "Only profitable trades are closed by this rule."
    )

    lines.append("")
    lines.append("🛡 SL / TP RULE")

    lines.append(
        "SL source: 5M Pullback Structure"
    )

    lines.append(
        "Minimum SL: 0.50%"
    )

    lines.append(
        "Maximum SL: 1.50%"
    )

    lines.append(
        "SL outside this range = REJECT"
    )

    lines.append(
        "TP: Minimum 2R from 5M structural SL"
    )

    lines.append("")
    lines.append("🎯 5M ENTRY RULE")

    lines.append(
        "Structure Break"
    )

    lines.append("↓")

    lines.append(
        "Pullback to Break Level"
    )

    lines.append("↓")

    lines.append(
        "Bullish/Bearish Confirmation"
    )

    lines.append("↓")

    lines.append(
        "ENTRY"
    )

    lines.append("↓")

    lines.append(
        "5M Structural SL"
    )

    lines.append("↓")

    lines.append(
        "TP = Minimum 2R"
    )

    lines.append("")
    lines.append("🧪 PAPER TRADING")

    return "\n".join(lines)


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID"
)


def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:
        print(message)
        return

    if not TELEGRAM_CHAT_ID:
        print(message)
        return

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    # Telegram max message size
    chunk_size = 3800

    chunks = [
        message[i:i + chunk_size]
        for i in range(
            0,
            len(message),
            chunk_size
        )
    ]

    for chunk in chunks:

        try:

            response = requests.post(
                url,
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": chunk
                },
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

        except Exception as e:

            print(
                f"TELEGRAM ERROR: {e}"
            )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=================================================="
    )

    print(
        "VOLUME-KHAT 100 v4.1"
    )

    print(
        "5M STRUCTURAL SL/TP"
    )

    print(
        "PAPER TRADING"
    )

    print(
        "=================================================="
    )

    init_db()

    perform_v40_reset()

    # --------------------------------------------------------
    # First manage existing positions
    # --------------------------------------------------------

    update_open_trades()

    # --------------------------------------------------------
    # Get top 100
    # --------------------------------------------------------

    markets = get_top_markets()

    if not markets:

        message = (
            "📊 VOLUME-KHAT 100\n"
            "⚠️ No markets received from Kraken."
        )

        send_telegram(message)

        return

    # --------------------------------------------------------
    # Scan
    # --------------------------------------------------------

    result = scan_markets(
        markets
    )

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    report = build_report(
        result
    )

    print(report)

    send_telegram(report)


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    main()
