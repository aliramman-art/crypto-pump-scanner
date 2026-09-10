# ============================================================
# VOLUME-KHAT 100 v3.3
# ============================================================
# Kraken Futures TOP 100
#
# TIMEFRAMES:
#   1H  = Trend
#   15M = Setup
#   5M  = Confirmation / Entry
#
# FEATURES:
#   - CLOSED CANDLES ONLY
#   - RVOL
#   - Breakout
#   - Rejection
#   - Structural SL
#   - Minimum RR 1:2
#   - TOP CANDIDATE
#   - 5M confirmation slightly relaxed
#   - SQLite migration
#   - Paper trading
#   - Telegram report
# ============================================================

import os
import time
import sqlite3
import requests
import traceback
from datetime import datetime, timezone, timedelta

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

# ------------------------------------------------------------
# RVOL
# ------------------------------------------------------------

RVOL_PERIOD = 20

RVOL_ABNORMAL = 1.70
RVOL_STRONG = 2.50
RVOL_VERY_STRONG = 3.00

# ------------------------------------------------------------
# PRICE ACTION
# ------------------------------------------------------------

BREAKOUT_LOOKBACK = 5
SUPPORT_RESISTANCE_LOOKBACK = 20

REJECTION_DISTANCE = 0.008

WICK_BODY_RATIO = 1.15

# ------------------------------------------------------------
# 5M CONFIRMATION
# ------------------------------------------------------------

# v3.3:
# Relaxed from 0.30 to 0.20
MIN_BODY_RATIO = 0.20

# ------------------------------------------------------------
# ATR / SL / TP
# ------------------------------------------------------------

ATR_PERIOD = 14
ATR_SL_BUFFER = 0.15

MAX_SL_PCT = 0.0080

MIN_RR = 2.0

# ------------------------------------------------------------
# SCORE
# ------------------------------------------------------------

MIN_SCORE = 9

# ------------------------------------------------------------
# TRADE LIMITS
# ------------------------------------------------------------

MAX_OPEN_TRADES = 3
MAX_NEW_SIGNALS = 3

COOLDOWN_CANDLES = 3

# ------------------------------------------------------------
# PAPER TRADING
# ------------------------------------------------------------

PAPER_TRADING = True

DB_FILE = "volume_khat_100.db"

# ------------------------------------------------------------
# TELEGRAM
# ------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Iran timezone
IRAN_TZ = timezone(timedelta(hours=3, minutes=30))


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "VOLUME-KHAT-100/3.3"
})


# ============================================================
# HTTP
# ============================================================

def http_get(url, params=None):
    r = session.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    r.raise_for_status()

    return r.json()


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    return conn


def table_columns(conn, table_name):
    rows = conn.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    return {row[1] for row in rows}


def init_db():

    conn = db_connect()

    conn.execute("""
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

    required = {
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
        "closed_reported": "INTEGER DEFAULT 0",
        "created_at": "TEXT"
    }

    existing = table_columns(conn, "trades")

    for column, definition in required.items():

        if column not in existing:

            try:
                conn.execute(
                    f"ALTER TABLE trades ADD COLUMN {column} {definition}"
                )

                print(f"DB MIGRATION: added column {column}")

            except Exception as e:
                print(
                    f"DB MIGRATION ERROR {column}: {e}"
                )

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_trades_status
        ON trades(status)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_trades_symbol
        ON trades(symbol)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_trades_created
        ON trades(created_at)
    """)

    conn.execute("""
        UPDATE trades
        SET closed_reported = 0
        WHERE closed_reported IS NULL
    """)

    conn.commit()
    conn.close()


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().isoformat()


def iran_time_string():

    now = utc_now().astimezone(IRAN_TZ)

    return now.strftime(
        "%Y/%m/%d %H:%M:%S"
    )


def to_persian_digits(text):

    table = str.maketrans(
        "0123456789",
        "۰۱۲۳۴۵۶۷۸۹"
    )

    return str(text).translate(table)


# ============================================================
# MARKET DISCOVERY
# ============================================================

def discover_markets():

    print("Discovering TOP 100 markets...")

    url = (
        BASE +
        "/derivatives/api/v3/instruments"
    )

    data = http_get(url)

    instruments = (
        data.get("instruments")
        or data.get("data")
        or []
    )

    symbols = []

    for item in instruments:

        symbol = item.get("symbol")

        if not symbol:
            continue

        if not symbol.startswith("PF_"):
            continue

        if not symbol.endswith("USD"):
            continue

        symbols.append(symbol)

    symbols = list(dict.fromkeys(symbols))

    # --------------------------------------------------------
    # 24H VOLUME RANK
    # --------------------------------------------------------

    try:

        ticker_data = http_get(
            BASE + "/derivatives/api/v3/tickers"
        )

        tickers = (
            ticker_data.get("tickers")
            or ticker_data.get("data")
            or []
        )

        volume_map = {}

        for t in tickers:

            symbol = t.get("symbol")

            if not symbol:
                continue

            try:

                volume = float(
                    t.get("vol24h")
                    or t.get("volume24h")
                    or t.get("volume")
                    or 0
                )

                volume_map[symbol] = volume

            except Exception:
                continue

        symbols.sort(
            key=lambda x: volume_map.get(x, 0),
            reverse=True
        )

    except Exception as e:

        print(
            f"Ticker ranking error: {e}"
        )

    symbols = symbols[:TOP_N]

    print(
        f"Markets discovered: {len(symbols)}"
    )

    return symbols


# ============================================================
# OHLCV
# ============================================================

def fetch_ohlcv(symbol, interval):

    # IMPORTANT:
    # Kraken Futures Charts API requires:
    #
    # /api/charts/v1/trade/{symbol}/{resolution}
    #
    # NOT:
    # /api/charts/v1/trade?symbol=...&interval=...

    url = (
        f"{BASE}/"
        f"api/charts/v1/"
        f"trade/"
        f"{symbol}/"
        f"{interval}"
    )

    data = http_get(
        url,
        params={
            "count": OHLCV_LIMIT
        }
    )

    rows = (
        data.get("candles")
        or data.get("data")
        or data.get("results")
        or []
    )

    if not rows:
        raise ValueError(
            "INSUFFICIENT DATA (0)"
        )

    parsed = []

    for row in rows:

        try:

            if isinstance(row, dict):

                ts = (
                    row.get("time")
                    or row.get("timestamp")
                )

                o = row.get("open")
                h = row.get("high")
                l = row.get("low")
                c = row.get("close")
                v = row.get("volume")

            else:

                # fallback for list-based response
                ts = row[0]
                o = row[1]
                h = row[2]
                l = row[3]
                c = row[4]
                v = row[5]

            if ts is None:
                continue

            ts = float(ts)

            if ts > 10_000_000_000:
                ts = ts / 1000.0

            parsed.append({
                "timestamp": ts,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v or 0)
            })

        except Exception:
            continue

    if len(parsed) < 60:
        raise ValueError(
            f"INSUFFICIENT DATA ({len(parsed)})"
        )

    df = pd.DataFrame(parsed)

    df["datetime"] = pd.to_datetime(
        df["timestamp"],
        unit="s",
        utc=True
    )

    df = (
        df.drop_duplicates(
            subset=["timestamp"]
        )
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    # --------------------------------------------------------
    # REMOVE CURRENT / UNFINISHED CANDLE
    # --------------------------------------------------------

    if len(df) > 1:
        df = df.iloc[:-1].copy()

    if len(df) < 60:
        raise ValueError(
            f"INSUFFICIENT CLOSED DATA ({len(df)})"
        )

    return df.tail(150).reset_index(drop=True)


# ============================================================
# INDICATORS
# ============================================================

def calculate_atr(df, period=ATR_PERIOD):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ],
        axis=1
    ).max(axis=1)

    atr = tr.rolling(period).mean()

    return atr


def calculate_rvol(df):

    if len(df) < RVOL_PERIOD + 2:
        return np.nan

    current_volume = float(
        df["volume"].iloc[-1]
    )

    average_volume = float(
        df["volume"]
        .iloc[-RVOL_PERIOD-1:-1]
        .mean()
    )

    if average_volume <= 0:
        return np.nan

    return current_volume / average_volume


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def get_support(df, lookback=SUPPORT_RESISTANCE_LOOKBACK):

    x = df.tail(lookback)

    return float(
        x["low"].min()
    )


def get_resistance(df, lookback=SUPPORT_RESISTANCE_LOOKBACK):

    x = df.tail(lookback)

    return float(
        x["high"].max()
    )


# ============================================================
# TREND
# ============================================================

def get_trend(df):

    if len(df) < 55:
        return "NEUTRAL"

    close = float(
        df["close"].iloc[-1]
    )

    sma20 = float(
        df["close"].rolling(20).mean().iloc[-1]
    )

    sma50 = float(
        df["close"].rolling(50).mean().iloc[-1]
    )

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
# CANDLE ANALYSIS
# ============================================================

def candle_body_ratio(row):

    rng = float(
        row["high"] - row["low"]
    )

    if rng <= 0:
        return 0.0

    body = abs(
        float(row["close"] - row["open"])
    )

    return body / rng


def candle_direction(row):

    if row["close"] > row["open"]:
        return "LONG"

    if row["close"] < row["open"]:
        return "SHORT"

    return "NEUTRAL"


# ============================================================
# 15M BREAKOUT
# ============================================================

def detect_breakout(df, trend):

    if len(df) < BREAKOUT_LOOKBACK + 2:
        return None

    last = df.iloc[-1]

    previous = df.iloc[
        -BREAKOUT_LOOKBACK-1:-1
    ]

    previous_high = float(
        previous["high"].max()
    )

    previous_low = float(
        previous["low"].min()
    )

    rvol = calculate_rvol(df)

    if not np.isfinite(rvol):
        return None

    if trend == "LONG":

        if (
            float(last["close"]) > previous_high
            and rvol >= RVOL_ABNORMAL
        ):
            return {
                "setup": "BREAKOUT",
                "side": "LONG",
                "rvol": rvol
            }

    if trend == "SHORT":

        if (
            float(last["close"]) < previous_low
            and rvol >= RVOL_ABNORMAL
        ):
            return {
                "setup": "BREAKOUT",
                "side": "SHORT",
                "rvol": rvol
            }

    return None


# ============================================================
# 15M REJECTION
# ============================================================

def detect_rejection(df, trend):

    if len(df) < SUPPORT_RESISTANCE_LOOKBACK + 2:
        return None

    last = df.iloc[-1]

    close = float(last["close"])
    open_price = float(last["open"])
    high = float(last["high"])
    low = float(last["low"])

    body = abs(
        close - open_price
    )

    if body <= 0:
        body = max(
            high - low,
            1e-12
        ) * 0.01

    lower_wick = min(
        open_price,
        close
    ) - low

    upper_wick = high - max(
        open_price,
        close
    )

    support = get_support(df)
    resistance = get_resistance(df)

    rvol = calculate_rvol(df)

    if not np.isfinite(rvol):
        return None

    # --------------------------------------------------------
    # LONG REJECTION
    # --------------------------------------------------------

    if trend == "LONG":

        distance = abs(
            close - support
        ) / max(close, 1e-12)

        if (
            distance <= REJECTION_DISTANCE
            and lower_wick >= body * WICK_BODY_RATIO
            and close > open_price
            and rvol >= RVOL_ABNORMAL
        ):

            return {
                "setup": "REJECTION",
                "side": "LONG",
                "rvol": rvol
            }

    # --------------------------------------------------------
    # SHORT REJECTION
    # --------------------------------------------------------

    if trend == "SHORT":

        distance = abs(
            close - resistance
        ) / max(close, 1e-12)

        if (
            distance <= REJECTION_DISTANCE
            and upper_wick >= body * WICK_BODY_RATIO
            and close < open_price
            and rvol >= RVOL_ABNORMAL
        ):

            return {
                "setup": "REJECTION",
                "side": "SHORT",
                "rvol": rvol
            }

    return None


# ============================================================
# 15M SETUP
# ============================================================

def detect_setup(df, trend):

    breakout = detect_breakout(
        df,
        trend
    )

    if breakout:
        return breakout

    rejection = detect_rejection(
        df,
        trend
    )

    if rejection:
        return rejection

    return None


# ============================================================
# 5M CONFIRMATION
# ============================================================

def confirm_5m(df, side):

    if len(df) < 3:
        return {
            "confirmed": False,
            "direction_ok": False,
            "body_ok": False,
            "body_ratio": 0.0
        }

    last = df.iloc[-1]

    direction = candle_direction(last)

    body_ratio = candle_body_ratio(last)

    direction_ok = (
        direction == side
    )

    body_ok = (
        body_ratio >= MIN_BODY_RATIO
    )

    confirmed = (
        direction_ok
        and body_ok
    )

    return {
        "confirmed": confirmed,
        "direction_ok": direction_ok,
        "body_ok": body_ok,
        "body_ratio": body_ratio
    }


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    trend,
    setup,
    rvol,
    confirmation
):

    score = 0

    # Trend
    if trend in ("LONG", "SHORT"):
        score += 3

    # Setup
    if setup:
        score += 4

    # RVOL
    if rvol >= RVOL_VERY_STRONG:
        score += 4

    elif rvol >= RVOL_STRONG:
        score += 4

    elif rvol >= RVOL_ABNORMAL:
        score += 3

    # 5M
    if confirmation:
        score += 4

    return score


# ============================================================
# SL / TP
# ============================================================

def build_sl_tp(
    side,
    entry,
    df15,
    df1h
):

    atr15 = calculate_atr(
        df15
    ).iloc[-1]

    atr1h = calculate_atr(
        df1h
    ).iloc[-1]

    atr_candidates = [
        x for x in [atr15, atr1h]
        if np.isfinite(x)
        and x > 0
    ]

    if not atr_candidates:
        return None

    atr = max(
        atr_candidates
    )

    support15 = get_support(
        df15
    )

    resistance15 = get_resistance(
        df15
    )

    support1h = get_support(
        df1h
    )

    resistance1h = get_resistance(
        df1h
    )

    if side == "LONG":

        structural_sl = min(
            support15,
            support1h
        )

        sl = (
            structural_sl
            - atr * ATR_SL_BUFFER
        )

        if sl >= entry:
            sl = (
                entry
                - atr * ATR_SL_BUFFER
            )

        risk = entry - sl

        if risk <= 0:
            return None

        sl_pct = risk / entry

        if sl_pct > MAX_SL_PCT:
            return None

        structural_tp = max(
            resistance15,
            resistance1h
        )

        min_tp = entry + risk * MIN_RR

        tp = max(
            structural_tp,
            min_tp
        )

        rr = (
            tp - entry
        ) / risk

    else:

        structural_sl = max(
            resistance15,
            resistance1h
        )

        sl = (
            structural_sl
            + atr * ATR_SL_BUFFER
        )

        if sl <= entry:
            sl = (
                entry
                + atr * ATR_SL_BUFFER
            )

        risk = sl - entry

        if risk <= 0:
            return None

        sl_pct = risk / entry

        if sl_pct > MAX_SL_PCT:
            return None

        structural_tp = min(
            support15,
            support1h
        )

        min_tp = entry - risk * MIN_RR

        tp = min(
            structural_tp,
            min_tp
        )

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
        "sl_pct": sl_pct
    }


# ============================================================
# DATABASE HELPERS
# ============================================================

def get_open_trades():

    conn = db_connect()

    rows = conn.execute("""
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
    """).fetchall()

    conn.close()

    return rows


def count_open_trades():

    conn = db_connect()

    count = conn.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'OPEN'
    """).fetchone()[0]

    conn.close()

    return int(count)


def get_recent_trade_for_symbol(symbol):

    conn = db_connect()

    row = conn.execute("""
        SELECT
            id,
            entry_time,
            status
        FROM trades
        WHERE symbol = ?
        ORDER BY id DESC
        LIMIT 1
    """, (symbol,)).fetchone()

    conn.close()

    return row


def is_in_cooldown(symbol):

    row = get_recent_trade_for_symbol(
        symbol
    )

    if not row:
        return False

    entry_time = row[1]

    if not entry_time:
        return False

    try:

        dt = datetime.fromisoformat(
            entry_time
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        diff_seconds = (
            utc_now() - dt
        ).total_seconds()

        candle_seconds = 5 * 60

        return (
            diff_seconds
            < candle_seconds * COOLDOWN_CANDLES
        )

    except Exception:
        return False


def trade_exists_open(symbol, side):

    conn = db_connect()

    row = conn.execute("""
        SELECT id
        FROM trades
        WHERE symbol = ?
        AND side = ?
        AND status = 'OPEN'
        LIMIT 1
    """, (
        symbol,
        side
    )).fetchone()

    conn.close()

    return row is not None


def save_trade(signal):

    conn = db_connect()

    conn.execute("""
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
        signal["symbol"],
        signal["side"],
        signal["setup"],
        signal["entry"],
        signal["sl"],
        signal["tp"],
        signal["rr"],
        signal["score"],
        "OPEN",
        None,
        None,
        utc_iso(),
        None,
        None,
        0,
        utc_iso()
    ))

    conn.commit()
    conn.close()


# ============================================================
# LIVE PRICE
# ============================================================

def fetch_live_price(symbol):

    try:

        data = http_get(
            BASE +
            "/derivatives/api/v3/tickers"
        )

        tickers = (
            data.get("tickers")
            or data.get("data")
            or []
        )

        for t in tickers:

            if t.get("symbol") != symbol:
                continue

            for key in (
                "last",
                "lastPrice",
                "price",
                "markPrice"
            ):

                value = t.get(key)

                if value is not None:

                    return float(value)

    except Exception:
        pass

    return None


# ============================================================
# PNL
# ============================================================

def calculate_pnl(
    side,
    entry,
    current
):

    if side == "LONG":

        return (
            current - entry
        ) / entry * 100

    return (
        entry - current
    ) / entry * 100


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades():

    rows = get_open_trades()

    for row in rows:

        trade_id = row[0]
        symbol = row[1]
        side = row[2]
        entry = float(row[4])
        sl = float(row[5])
        tp = float(row[6])

        try:

            df5 = fetch_ohlcv(
                symbol,
                TIMEFRAME_5M
            )

            last = df5.iloc[-1]

            high = float(
                last["high"]
            )

            low = float(
                last["low"]
            )

            exit_reason = None
            exit_price = None

            # ------------------------------------------------
            # LONG
            # ------------------------------------------------

            if side == "LONG":

                # Conservative:
                # if both are touched in same candle,
                # SL is assumed first.

                if low <= sl:

                    exit_reason = "SL"
                    exit_price = sl

                elif high >= tp:

                    exit_reason = "TP"
                    exit_price = tp

            # ------------------------------------------------
            # SHORT
            # ------------------------------------------------

            else:

                if high >= sl:

                    exit_reason = "SL"
                    exit_price = sl

                elif low <= tp:

                    exit_reason = "TP"
                    exit_price = tp

            # ------------------------------------------------
            # CLOSE
            # ------------------------------------------------

            if exit_reason:

                pnl = calculate_pnl(
                    side,
                    entry,
                    exit_price
                )

                result = (
                    "WIN"
                    if pnl > 0
                    else "LOSS"
                )

                conn = db_connect()

                conn.execute("""
                    UPDATE trades
                    SET
                        status = 'CLOSED',
                        result = ?,
                        pnl = ?,
                        exit_time = ?,
                        exit_reason = ?
                    WHERE id = ?
                """, (
                    result,
                    pnl,
                    utc_iso(),
                    exit_reason,
                    trade_id
                ))

                conn.commit()
                conn.close()

                print(
                    f"CLOSED {symbol} "
                    f"{side} "
                    f"{result} "
                    f"{pnl:.2f}%"
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

    rows = get_open_trades()

    if len(rows) <= MAX_OPEN_TRADES:
        return

    # Oldest remain, newest excess removed
    rows = sorted(
        rows,
        key=lambda x: x[0]
    )

    excess = rows[
        MAX_OPEN_TRADES:
    ]

    for row in excess:

        trade_id = row[0]
        symbol = row[1]

        try:

            conn = db_connect()

            conn.execute("""
                UPDATE trades
                SET
                    status = 'CLOSED',
                    result = 'REMOVED',
                    pnl = 0,
                    exit_time = ?,
                    exit_reason = 'MAX_OPEN_CLEANUP',
                    closed_reported = 1
                WHERE id = ?
            """, (
                utc_iso(),
                trade_id
            ))

            conn.commit()
            conn.close()

            print(
                f"Removed excess open trade: "
                f"{symbol}"
            )

        except Exception as e:

            print(
                f"Max open cleanup error "
                f"{symbol}: {e}"
            )


# ============================================================
# STATS
# ============================================================

def get_stats():

    conn = db_connect()

    closed = conn.execute("""
        SELECT
            COUNT(*),
            SUM(
                CASE
                    WHEN result = 'WIN'
                    THEN 1 ELSE 0
                END
            ),
            SUM(
                CASE
                    WHEN result = 'LOSS'
                    THEN 1 ELSE 0
                END
            ),
            COALESCE(SUM(pnl), 0)
        FROM trades
        WHERE status = 'CLOSED'
        AND result IN ('WIN', 'LOSS')
    """).fetchone()

    conn.close()

    total = int(closed[0] or 0)
    wins = int(closed[1] or 0)
    losses = int(closed[2] or 0)
    pnl = float(closed[3] or 0)

    wr = (
        wins / total * 100
        if total > 0
        else 0
    )

    return {
        "closed": total,
        "wins": wins,
        "losses": losses,
        "wr": wr,
        "pnl": pnl
    }


# ============================================================
# CLOSED REPORT
# ============================================================

def get_unreported_closed():

    conn = db_connect()

    rows = conn.execute("""
        SELECT
            id,
            symbol,
            side,
            entry,
            exit_time,
            exit_reason,
            pnl,
            result
        FROM trades
        WHERE status = 'CLOSED'
        AND result IN ('WIN', 'LOSS')
        AND closed_reported = 0
        ORDER BY id ASC
    """).fetchall()

    conn.close()

    return rows


def mark_closed_reported(ids):

    if not ids:
        return

    conn = db_connect()

    for trade_id in ids:

        conn.execute("""
            UPDATE trades
            SET closed_reported = 1
            WHERE id = ?
        """, (trade_id,))

    conn.commit()
    conn.close()


# ============================================================
# TOP CANDIDATE
# ============================================================

def candidate_rank_key(candidate):

    score = float(
        candidate.get("score", 0)
    )

    rvol = float(
        candidate.get("rvol", 0)
    )

    confirmation = (
        1
        if candidate.get("confirmation")
        else 0
    )

    return (
        score,
        confirmation,
        rvol
    )


def select_top_candidate(candidates):

    if not candidates:
        return None

    return max(
        candidates,
        key=candidate_rank_key
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:
        print("Telegram token missing")
        return False

    if not TELEGRAM_CHAT_ID:
        print("Telegram chat id missing")
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:

        r = session.post(
            url,
            json=payload,
            timeout=20
        )

        r.raise_for_status()

        return True

    except Exception as e:

        print(
            f"Telegram error: {e}"
        )

        return False


# ============================================================
# FORMAT
# ============================================================

def fmt_price(x):

    if x is None:
        return "-"

    x = float(x)

    if abs(x) >= 100:
        return f"{x:.2f}"

    if abs(x) >= 1:
        return f"{x:.5f}"

    if abs(x) >= 0.01:
        return f"{x:.6f}"

    return f"{x:.8f}"


def format_top_candidate(candidate):

    if not candidate:
        return (
            "🏆 TOP CANDIDATE\n"
            "None"
        )

    symbol = candidate["symbol"]
    side = candidate["side"]
    score = candidate["score"]
    rvol = candidate["rvol"]

    setup = candidate["setup"]

    body = candidate.get(
        "body_ratio",
        0
    )

    direction_ok = candidate.get(
        "direction_ok",
        False
    )

    confirmation = candidate.get(
        "confirmation",
        False
    )

    if confirmation:
        status = "✅ CONFIRMED"

    elif direction_ok:
        status = "🟡 5M BODY"

    else:
        status = "🟠 5M DIRECTION"

    return (
        "🏆 TOP CANDIDATE\n"
        f"{symbol} {side}\n"
        f"Score {score}/15 | "
        f"RVOL {rvol:.2f}x\n"
        f"Setup {setup}\n"
        f"5M Body {body:.2f} | "
        f"{status}"
    )


# ============================================================
# MAIN SCAN
# ============================================================

def scan_markets(symbols):

    diagnostics = {
        "markets": len(symbols),
        "data_ok": 0,
        "trend": 0,
        "setup": 0,
        "confirmation": 0,
        "valid_sltp": 0,
        "score9": 0,
        "final": 0,
        "neutral": 0,
        "no_setup": 0,
        "no_5m_confirm": 0,
        "invalid_sltp": 0,
        "low_score": 0,
        "data_errors": 0
    }

    candidates = []
    final_signals = []

    available_slots = max(
        0,
        MAX_OPEN_TRADES
        - count_open_trades()
    )

    print(
        f"Open trades: "
        f"{count_open_trades()}"
    )

    print(
        f"Available slots: "
        f"{available_slots}"
    )

    for index, symbol in enumerate(
        symbols,
        start=1
    ):

        print(
            f"[{index}/{len(symbols)}] "
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

            diagnostics["data_ok"] += 1

            # ------------------------------------------------
            # TREND
            # ------------------------------------------------

            trend = get_trend(
                df1h
            )

            if trend == "NEUTRAL":

                diagnostics["neutral"] += 1

                print(
                    f"  NEUTRAL"
                )

                continue

            diagnostics["trend"] += 1

            # ------------------------------------------------
            # SETUP
            # ------------------------------------------------

            setup = detect_setup(
                df15,
                trend
            )

            if not setup:

                diagnostics["no_setup"] += 1

                print(
                    f"  NO SETUP | "
                    f"Trend {trend}"
                )

                continue

            diagnostics["setup"] += 1

            side = setup["side"]
            setup_name = setup["setup"]
            rvol = float(
                setup["rvol"]
            )

            # ------------------------------------------------
            # 5M
            # ------------------------------------------------

            confirmation_data = confirm_5m(
                df5,
                side
            )

            confirmation = (
                confirmation_data["confirmed"]
            )

            body_ratio = (
                confirmation_data["body_ratio"]
            )

            direction_ok = (
                confirmation_data["direction_ok"]
            )

            if confirmation:
                diagnostics["confirmation"] += 1
            else:
                diagnostics["no_5m_confirm"] += 1

            # ------------------------------------------------
            # ENTRY
            # ------------------------------------------------

            entry = float(
                df5["close"].iloc[-1]
            )

            # ------------------------------------------------
            # SCORE
            # ------------------------------------------------

            score = calculate_score(
                trend=trend,
                setup=setup_name,
                rvol=rvol,
                confirmation=confirmation
            )

            if score >= MIN_SCORE:
                diagnostics["score9"] += 1

            # ------------------------------------------------
            # CANDIDATE
            # ------------------------------------------------

            candidate = {
                "symbol": symbol,
                "side": side,
                "trend": trend,
                "setup": setup_name,
                "rvol": rvol,
                "entry": entry,
                "score": score,
                "confirmation": confirmation,
                "direction_ok": direction_ok,
                "body_ok": confirmation_data["body_ok"],
                "body_ratio": body_ratio,
                "sl": None,
                "tp": None,
                "rr": None,
                "valid_sltp": False
            }

            candidates.append(
                candidate
            )

            # ------------------------------------------------
            # REQUIRE 5M CONFIRMATION
            # ------------------------------------------------

            if not confirmation:

                print(
                    f"  SETUP {setup_name} | "
                    f"{side} | "
                    f"RVOL {rvol:.2f} | "
                    f"5M BODY {body_ratio:.2f} | "
                    f"NO CONFIRM"
                )

                continue

            # ------------------------------------------------
            # SL / TP
            # ------------------------------------------------

            sltp = build_sl_tp(
                side,
                entry,
                df15,
                df1h
            )

            if not sltp:

                diagnostics["invalid_sltp"] += 1

                print(
                    f"  INVALID SL/TP"
                )

                continue

            diagnostics["valid_sltp"] += 1

            candidate["sl"] = sltp["sl"]
            candidate["tp"] = sltp["tp"]
            candidate["rr"] = sltp["rr"]
            candidate["valid_sltp"] = True

            # ------------------------------------------------
            # SCORE
            # ------------------------------------------------

            if score < MIN_SCORE:

                diagnostics["low_score"] += 1

                print(
                    f"  LOW SCORE {score}"
                )

                continue

            # ------------------------------------------------
            # MAX SIGNALS
            # ------------------------------------------------

            if len(final_signals) >= MAX_NEW_SIGNALS:
                continue

            # ------------------------------------------------
            # DUPLICATE
            # ------------------------------------------------

            if trade_exists_open(
                symbol,
                side
            ):
                continue

            # ------------------------------------------------
            # COOLDOWN
            # ------------------------------------------------

            if is_in_cooldown(symbol):
                continue

            # ------------------------------------------------
            # FINAL
            # ------------------------------------------------

            signal = {
                "symbol": symbol,
                "side": side,
                "setup": setup_name,
                "entry": entry,
                "sl": sltp["sl"],
                "tp": sltp["tp"],
                "rr": sltp["rr"],
                "score": score,
                "rvol": rvol,
                "body_ratio": body_ratio
            }

            final_signals.append(
                signal
            )

            diagnostics["final"] += 1

            print(
                f"  FINAL SIGNAL "
                f"{symbol} {side} "
                f"Score {score} "
                f"RR {sltp['rr']:.2f}"
            )

        except Exception as e:

            diagnostics["data_errors"] += 1

            print(
                f"  DATA ERROR "
                f"{symbol}: {e}"
            )

    # --------------------------------------------------------
    # SORT FINAL SIGNALS
    # --------------------------------------------------------

    final_signals.sort(
        key=lambda x: (
            x["score"],
            x["rvol"],
            x["rr"]
        ),
        reverse=True
    )

    return (
        diagnostics,
        candidates,
        final_signals
    )


# ============================================================
# SAVE FINAL SIGNALS
# ============================================================

def save_final_signals(
    final_signals
):

    saved = []

    open_count = count_open_trades()

    available = max(
        0,
        MAX_OPEN_TRADES
        - open_count
    )

    for signal in final_signals:

        if len(saved) >= available:
            break

        try:

            if trade_exists_open(
                signal["symbol"],
                signal["side"]
            ):
                continue

            if is_in_cooldown(
                signal["symbol"]
            ):
                continue

            save_trade(
                signal
            )

            saved.append(
                signal
            )

        except Exception as e:

            print(
                f"Save signal error "
                f"{signal['symbol']}: {e}"
            )

    return saved


# ============================================================
# REPORT
# ============================================================

def build_report(
    diagnostics,
    candidates,
    saved_signals
):

    stats = get_stats()

    open_rows = get_open_trades()

    top = select_top_candidate(
        candidates
    )

    now = iran_time_string()

    lines = []

    lines.append(
        "📊 VOLUME-KHAT 100"
    )

    lines.append(
        f"🕐 {now} IR"
    )

    lines.append(
        "⚡ Kraken Futures | 5M CLOSED"
    )

    lines.append(
        f"🔎 Markets: {diagnostics['markets']} | "
        f"Signals: {len(saved_signals)}"
    )

    lines.append("")

    lines.append(
        "📈 PERFORMANCE"
    )

    lines.append(
        f"Open {len(open_rows)}/{MAX_OPEN_TRADES} | "
        f"Closed {stats['closed']} "
        f"W {stats['wins']} | "
        f"L {stats['losses']} | "
        f"WR {stats['wr']:.1f}%"
    )

    lines.append(
        f"Realized PnL "
        f"{stats['pnl']:+.3f}%"
    )

    lines.append("")

    # --------------------------------------------------------
    # TOP CANDIDATE
    # --------------------------------------------------------

    lines.append(
        format_top_candidate(top)
    )

    lines.append("")

    # --------------------------------------------------------
    # DIAGNOSTIC
    # --------------------------------------------------------

    lines.append(
        "🔬 DIAGNOSTIC"
    )

    lines.append(
        f"Markets {diagnostics['markets']} | "
        f"Data OK {diagnostics['data_ok']}"
    )

    lines.append(
        f"Trend {diagnostics['trend']} | "
        f"Setup {diagnostics['setup']}"
    )

    lines.append(
        f"Confirmation {diagnostics['confirmation']} | "
        f"Valid SL/TP {diagnostics['valid_sltp']}"
    )

    lines.append(
        f"Score ≥ {MIN_SCORE}: "
        f"{diagnostics['score9']} | "
        f"Final: {diagnostics['final']}"
    )

    lines.append(
        f"Neutral {diagnostics['neutral']} | "
        f"No Setup {diagnostics['no_setup']}"
    )

    lines.append(
        f"No 5M Confirm "
        f"{diagnostics['no_5m_confirm']} | "
        f"Invalid SL/TP "
        f"{diagnostics['invalid_sltp']}"
    )

    lines.append(
        f"Low Score "
        f"{diagnostics['low_score']} | "
        f"Data Errors "
        f"{diagnostics['data_errors']}"
    )

    # --------------------------------------------------------
    # NEW SIGNALS
    # --------------------------------------------------------

    if saved_signals:

        lines.append("")
        lines.append(
            "🚨 NEW SIGNALS"
        )

        for s in saved_signals:

            lines.append(
                f"🟢 {s['symbol']} "
                f"{s['side']}"
            )

            lines.append(
                f"Entry {fmt_price(s['entry'])} | "
                f"SL {fmt_price(s['sl'])}"
            )

            lines.append(
                f"TP {fmt_price(s['tp'])} | "
                f"RR 1:{s['rr']:.2f} | "
                f"Score {s['score']}/15"
            )

    # --------------------------------------------------------
    # CLOSED
    # --------------------------------------------------------

    closed_rows = get_unreported_closed()

    if closed_rows:

        lines.append("")
        lines.append(
            "🔔 CLOSED"
        )

        reported_ids = []

        for row in closed_rows:

            trade_id = row[0]
            symbol = row[1]
            side = row[2]
            entry = row[3]
            exit_reason = row[5]
            pnl = row[6]
            result = row[7]

            icon = (
                "✅"
                if result == "WIN"
                else "❌"
            )

            exit_price = None

            try:

                exit_price = (
                    entry
                    * (
                        1 + pnl / 100
                        if side == "LONG"
                        else 1 - pnl / 100
                    )
                )

            except Exception:
                pass

            lines.append(
                f"{icon} {symbol} "
                f"{side} {result} "
                f"{pnl:+.2f}%"
            )

            lines.append(
                f"Entry {fmt_price(entry)} "
                f"→ Exit "
                f"{fmt_price(exit_price)}"
            )

            lines.append(
                f"Reason {exit_reason}"
            )

            reported_ids.append(
                trade_id
            )

        # We only mark as reported after
        # the final Telegram send succeeds.
    else:

        reported_ids = []

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    lines.append("")

    lines.append(
        "📂 OPEN TRADES"
    )

    if not open_rows:

        lines.append(
            "None"
        )

    else:

        for row in open_rows:

            symbol = row[1]
            side = row[2]
            entry = float(row[4])
            sl = float(row[5])
            tp = float(row[6])
            rr = float(row[7])

            current = fetch_live_price(
                symbol
            )

            if current is not None:

                pnl = calculate_pnl(
                    side,
                    entry,
                    current
                )

                icon = (
                    "🟢"
                    if pnl >= 0
                    else "🔴"
                )

                lines.append(
                    f"{icon} {symbol} "
                    f"{side} "
                    f"PnL {pnl:+.2f}%"
                )

                lines.append(
                    f"Entry {fmt_price(entry)} | "
                    f"Now {fmt_price(current)}"
                )

            else:

                lines.append(
                    f"🟢 {symbol} {side}"
                )

                lines.append(
                    f"Entry {fmt_price(entry)}"
                )

            lines.append(
                f"SL {fmt_price(sl)} | "
                f"TP {fmt_price(tp)} | "
                f"RR 1:{rr:.2f}"
            )

    lines.append("")
    lines.append(
        "🧪 PAPER TRADING"
    )

    return "\n".join(lines), reported_ids


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print(
        "VOLUME-KHAT 100 v3.3"
    )
    print(
        "Kraken Futures | 5M CLOSED"
    )
    print("=" * 60)

    try:

        # ----------------------------------------------------
        # DB
        # ----------------------------------------------------

        init_db()

        # ----------------------------------------------------
        # UPDATE EXISTING
        # ----------------------------------------------------

        update_open_trades()

        # ----------------------------------------------------
        # CLEANUP
        # ----------------------------------------------------

        enforce_max_open_trades()

        # ----------------------------------------------------
        # MARKETS
        # ----------------------------------------------------

        symbols = discover_markets()

        if not symbols:

            print(
                "No markets discovered."
            )

            return

        # ----------------------------------------------------
        # SCAN
        # ----------------------------------------------------

        (
            diagnostics,
            candidates,
            final_signals
        ) = scan_markets(
            symbols
        )

        # ----------------------------------------------------
        # SAVE
        # ----------------------------------------------------

        saved_signals = save_final_signals(
            final_signals
        )

        # ----------------------------------------------------
        # REPORT
        # ----------------------------------------------------

        report, reported_ids = build_report(
            diagnostics,
            candidates,
            saved_signals
        )

        print("")
        print(report)
        print("")

        # ----------------------------------------------------
        # TELEGRAM
        # ----------------------------------------------------

        success = send_telegram(
            report
        )

        # ----------------------------------------------------
        # CLOSED REPORT MARKING
        # ----------------------------------------------------

        if success and reported_ids:

            mark_closed_reported(
                reported_ids
            )

        print(
            "Telegram sent:",
            success
        )

    except Exception as e:

        print(
            "FATAL ERROR:",
            e
        )

        traceback.print_exc()

        try:

            send_telegram(
                "❌ VOLUME-KHAT 100 ERROR\n"
                f"{type(e).__name__}: {e}"
            )

        except Exception:
            pass


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    main()
