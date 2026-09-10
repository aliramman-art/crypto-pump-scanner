# ============================================================
# KRAKEN FUTURES VOLUME-KHAT 100 v3.8
# ============================================================
#
# 1H  = Trend
# 15M = Setup
# 5M  = Confirmation / Entry
#
# CLOSED CANDLES ONLY
#
# v3.8 CHANGES:
#   - Performance starts from ZERO
#   - Old database trades are cleared ONCE on first v3.8 run
#   - Duration shown for OPEN trades
#   - Duration shown when trade reaches TIME EXIT
#   - TIME EXIT:
#       >= 2 hours
#       AND PnL >= +1.50%
#       => close trade with TIME_PROFIT
#   - Existing strategy logic preserved
#   - Re-entry cooldown based on EXIT TIME
#   - SL 0.50% to 0.80%
#   - RR minimum 2.0
#   - Maximum 3 OPEN trades
#   - Maximum 3 NEW signals per run
#   - Better filter diagnostics
# ============================================================

import os
import sqlite3
import time
import requests
import pandas as pd

from datetime import datetime, timezone, timedelta


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
# STRUCTURE
# ============================================================

BREAKOUT_LOOKBACK = 5
SUPPORT_RESISTANCE_LOOKBACK = 20

REJECTION_DISTANCE = 0.008
WICK_BODY_RATIO = 1.15


# ============================================================
# ATR / SL / TP
# ============================================================

ATR_PERIOD = 14
ATR_SL_BUFFER = 0.15

MIN_SL_PCT = 0.0050
MAX_SL_PCT = 0.0080

MIN_RR = 2.0


# ============================================================
# CANDLE
# ============================================================

MIN_BODY_RATIO = 0.20


# ============================================================
# SCORE
# ============================================================

MIN_SCORE = 9

# Maximum score:
#
# Trend        +3
# Setup        +4
# RVOL         +4
# Confirmation +4
#
# Maximum = 15
# ============================================================


# ============================================================
# TRADE LIMITS
# ============================================================

MAX_OPEN_TRADES = 3
MAX_NEW_SIGNALS = 3

# 3 closed 5m candles = 15 minutes
COOLDOWN_CANDLES = 3


# ============================================================
# TIME EXIT
# ============================================================

TIME_EXIT_HOURS = 2.0

TIME_EXIT_MINUTES = int(
    TIME_EXIT_HOURS * 60
)

TIME_EXIT_MIN_PROFIT_PCT = 1.50


# ============================================================
# PAPER / LIVE
# ============================================================

PAPER_TRADING = True


# ============================================================
# DATABASE
# ============================================================

DB_FILE = "volume_khat_100.db"

# ------------------------------------------------------------
# IMPORTANT
#
# This flag performs the v3.8 reset ONLY ONCE.
#
# On the first run:
#   - old trades are deleted
#   - performance becomes zero
#   - open trades become zero
#
# After that, a database marker prevents another reset.
# ------------------------------------------------------------

RESET_DATABASE_ON_V38_START = True

RESET_KEY = "VOLUME_KHAT_V38_RESET_DONE"


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
# IRAN TIMEZONE
# ============================================================

IRAN_TZ = timezone(
    timedelta(hours=3, minutes=30)
)


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "VOLUME-KHAT-100/3.8"
    }
)


# ============================================================
# DATABASE
# ============================================================

def db_connect():

    conn = sqlite3.connect(DB_FILE)

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = db_connect()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT NOT NULL,

            side TEXT NOT NULL,

            setup TEXT,

            entry REAL NOT NULL,

            sl REAL NOT NULL,

            tp REAL NOT NULL,

            rr REAL,

            score REAL,

            status TEXT NOT NULL,

            result TEXT,

            pnl REAL,

            entry_time TEXT,

            exit_time TEXT,

            exit_reason TEXT,

            closed_reported INTEGER DEFAULT 0,

            created_at TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS system_meta (

            key TEXT PRIMARY KEY,

            value TEXT
        )
        """
    )

    conn.commit()

    # --------------------------------------------------------
    # Migration safety
    # --------------------------------------------------------

    columns = []

    try:

        rows = conn.execute(
            "PRAGMA table_info(trades)"
        ).fetchall()

        columns = [
            row["name"]
            for row in rows
        ]

    except Exception:

        pass

    required_columns = {

        "setup": "TEXT",

        "rr": "REAL",

        "score": "REAL",

        "result": "TEXT",

        "pnl": "REAL",

        "entry_time": "TEXT",

        "exit_time": "TEXT",

        "exit_reason": "TEXT",

        "closed_reported":
            "INTEGER DEFAULT 0",

        "created_at": "TEXT",
    }

    for column, definition in required_columns.items():

        if column not in columns:

            try:

                conn.execute(
                    f"""
                    ALTER TABLE trades
                    ADD COLUMN {column} {definition}
                    """
                )

            except Exception:

                pass

    conn.commit()

    # --------------------------------------------------------
    # Indexes
    # --------------------------------------------------------

    try:

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_trades_symbol
            ON trades(symbol)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_trades_status
            ON trades(status)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_trades_created
            ON trades(created_at)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_trades_exit
            ON trades(exit_time)
            """
        )

        conn.commit()

    except Exception:

        pass

    conn.close()


# ============================================================
# ONE-TIME V3.8 RESET
# ============================================================

def perform_v38_reset():

    if not RESET_DATABASE_ON_V38_START:

        return False

    conn = db_connect()

    try:

        row = conn.execute(
            """
            SELECT value
            FROM system_meta
            WHERE key=?
            """,
            (RESET_KEY,)
        ).fetchone()

        already_done = (
            row is not None
            and str(row["value"]) == "1"
        )

        if already_done:

            conn.close()

            return False

        # ----------------------------------------------------
        # Delete ALL old trades
        # ----------------------------------------------------

        conn.execute(
            "DELETE FROM trades"
        )

        # ----------------------------------------------------
        # Reset sqlite sequence when available
        # ----------------------------------------------------

        try:

            conn.execute(
                """
                DELETE FROM sqlite_sequence
                WHERE name='trades'
                """
            )

        except Exception:

            pass

        # ----------------------------------------------------
        # Mark reset completed
        # ----------------------------------------------------

        conn.execute(
            """
            INSERT OR REPLACE INTO system_meta
            (key, value)
            VALUES (?, ?)
            """,
            (
                RESET_KEY,
                "1",
            )
        )

        conn.commit()

        conn.close()

        return True

    except Exception:

        conn.close()

        raise


# ============================================================
# TIME HELPERS
# ============================================================

def utc_now():

    return datetime.now(
        timezone.utc
    )


def utc_iso():

    return utc_now().isoformat()


def parse_datetime(value):

    if not value:

        return None

    try:

        value = str(value)

        if value.endswith("Z"):

            value = (
                value[:-1]
                + "+00:00"
            )

        dt = datetime.fromisoformat(
            value
        )

        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        )

    except Exception:

        return None


def iran_time_string():

    now = datetime.now(
        IRAN_TZ
    )

    return now.strftime(
        "%Y/%m/%d %H:%M:%S"
    )


def calculate_duration(
    entry_time,
    end_time=None
):

    start = parse_datetime(
        entry_time
    )

    if start is None:

        return "00h 00m"

    if end_time is None:

        end = utc_now()

    else:

        end = parse_datetime(
            end_time
        )

        if end is None:

            end = utc_now()

    seconds = (
        end - start
    ).total_seconds()

    if seconds < 0:

        seconds = 0

    total_minutes = int(
        seconds // 60
    )

    hours = total_minutes // 60

    minutes = total_minutes % 60

    return (
        f"{hours:02d}h "
        f"{minutes:02d}m"
    )


def duration_minutes(
    entry_time
):

    start = parse_datetime(
        entry_time
    )

    if start is None:

        return 0

    elapsed = (
        utc_now() - start
    ).total_seconds()

    return max(
        0,
        int(elapsed // 60)
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):

        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {

        "chat_id":
            TELEGRAM_CHAT_ID,

        "text":
            message,

        "disable_web_page_preview":
            True,
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        return response.ok

    except Exception:

        return False


# ============================================================
# KRAKEN MARKETS
# ============================================================

def get_top_markets():

    url = (
        BASE
        + "/derivatives/api/v3/tickers"
    )

    response = SESSION.get(
        url,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    data = response.json()

    tickers = data.get(
        "tickers",
        []
    )

    markets = []

    for item in tickers:

        symbol = item.get(
            "symbol",
            ""
        )

        if not symbol.startswith(
            "PF_"
        ):

            continue

        if not symbol.endswith(
            "USD"
        ):

            continue

        try:

            volume = float(
                item.get(
                    "vol24h",
                    0
                )
                or 0
            )

        except Exception:

            volume = 0

        markets.append(
            {
                "symbol":
                    symbol,

                "volume":
                    volume,
            }
        )

    markets.sort(
        key=lambda x:
            x["volume"],
        reverse=True,
    )

    return [
        x["symbol"]
        for x in markets[:TOP_N]
    ]


# ============================================================
# OHLCV
# ============================================================

def fetch_ohlcv(
    symbol,
    interval
):

    url = (
        BASE
        + f"/api/charts/v1/trade/"
        + f"{symbol}/{interval}"
    )

    response = SESSION.get(
        url,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    data = response.json()

    candles = data.get(
        "candles",
        []
    )

    if not candles:

        return pd.DataFrame()

    rows = []

    for c in candles:

        try:

            timestamp = (
                c.get("time")
                or c.get("timestamp")
            )

            if timestamp is None:

                continue

            timestamp = float(
                timestamp
            )

            if timestamp > 100000000000:

                timestamp /= 1000

            rows.append(
                {

                    "timestamp":
                        timestamp,

                    "open":
                        float(
                            c["open"]
                        ),

                    "high":
                        float(
                            c["high"]
                        ),

                    "low":
                        float(
                            c["low"]
                        ),

                    "close":
                        float(
                            c["close"]
                        ),

                    "volume":
                        float(
                            c.get(
                                "volume",
                                0
                            )
                            or 0
                        ),
                }
            )

        except Exception:

            continue

    if not rows:

        return pd.DataFrame()

    df = pd.DataFrame(
        rows
    )

    df = df.sort_values(
        "timestamp"
    )

    df = df.drop_duplicates(
        "timestamp"
    )

    # --------------------------------------------------------
    # Remove unfinished candle
    # --------------------------------------------------------

    interval_seconds = {

        "5m": 300,

        "15m": 900,

        "1h": 3600,

    }.get(
        interval,
        300
    )

    now_ts = time.time()

    if len(df) > 0:

        last_ts = float(
            df.iloc[-1]["timestamp"]
        )

        if (
            now_ts
            < last_ts + interval_seconds
        ):

            df = df.iloc[:-1]

    df = (
        df
        .tail(OHLCV_LIMIT)
        .reset_index(drop=True)
    )

    return df


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(df):

    if (
        df is None
        or len(df)
        < RVOL_PERIOD + 1
    ):

        return 0.0

    volume = df["volume"]

    previous_avg = (
        volume.iloc[
            -RVOL_PERIOD-1:-1
        ].mean()
    )

    current_volume = float(
        volume.iloc[-1]
    )

    if previous_avg <= 0:

        return 0.0

    return (
        current_volume
        / previous_avg
    )


# ============================================================
# ATR
# ============================================================

def calculate_atr(df):

    if (
        df is None
        or len(df)
        < ATR_PERIOD + 2
    ):

        return 0.0

    high = df["high"]

    low = df["low"]

    close = df["close"]

    previous_close = (
        close.shift(1)
    )

    tr1 = high - low

    tr2 = (
        high - previous_close
    ).abs()

    tr3 = (
        low - previous_close
    ).abs()

    tr = pd.concat(
        [
            tr1,
            tr2,
            tr3,
        ],
        axis=1
    ).max(axis=1)

    atr = tr.rolling(
        ATR_PERIOD
    ).mean()

    value = atr.iloc[-1]

    if pd.isna(value):

        return 0.0

    return float(value)


# ============================================================
# TREND
# ============================================================

def get_trend(df):

    if (
        df is None
        or len(df) < 55
    ):

        return "NEUTRAL"

    close = df["close"]

    sma20 = (
        close.rolling(20)
        .mean()
    )

    sma50 = (
        close.rolling(50)
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

    if c > s20 > s50:

        return "LONG"

    if c < s20 < s50:

        return "SHORT"

    return "NEUTRAL"


# ============================================================
# BREAKOUT
# ============================================================

def detect_breakout(
    df,
    trend
):

    if (
        df is None
        or len(df)
        < BREAKOUT_LOOKBACK + 2
    ):

        return None

    rvol = calculate_rvol(
        df
    )

    if rvol < RVOL_ABNORMAL:

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

    close = float(
        last["close"]
    )

    if trend == "LONG":

        if close > previous_high:

            return {

                "type":
                    "BREAKOUT",

                "direction":
                    "LONG",

                "level":
                    previous_high,

                "rvol":
                    rvol,
            }

    if trend == "SHORT":

        if close < previous_low:

            return {

                "type":
                    "BREAKOUT",

                "direction":
                    "SHORT",

                "level":
                    previous_low,

                "rvol":
                    rvol,
            }

    return None


# ============================================================
# REJECTION
# ============================================================

def detect_rejection(
    df,
    trend
):

    if (
        df is None
        or len(df)
        < SUPPORT_RESISTANCE_LOOKBACK + 2
    ):

        return None

    rvol = calculate_rvol(
        df
    )

    if rvol < RVOL_ABNORMAL:

        return None

    last = df.iloc[-1]

    o = float(last["open"])

    h = float(last["high"])

    l = float(last["low"])

    c = float(last["close"])

    body = abs(
        c - o
    )

    if body <= 0:

        return None

    upper_wick = (
        h - max(o, c)
    )

    lower_wick = (
        min(o, c) - l
    )

    previous = df.iloc[
        -SUPPORT_RESISTANCE_LOOKBACK-1:-1
    ]

    support = float(
        previous["low"].min()
    )

    resistance = float(
        previous["high"].max()
    )

    # --------------------------------------------------------
    # LONG rejection
    # --------------------------------------------------------

    if trend == "LONG":

        distance = (
            abs(c - support)
            / c
        )

        if distance <= REJECTION_DISTANCE:

            bullish = c > o

            if (
                bullish
                and lower_wick
                >= body * WICK_BODY_RATIO
            ):

                return {

                    "type":
                        "REJECTION",

                    "direction":
                        "LONG",

                    "level":
                        support,

                    "rvol":
                        rvol,
                }

    # --------------------------------------------------------
    # SHORT rejection
    # --------------------------------------------------------

    if trend == "SHORT":

        distance = (
            abs(c - resistance)
            / c
        )

        if distance <= REJECTION_DISTANCE:

            bearish = c < o

            if (
                bearish
                and upper_wick
                >= body * WICK_BODY_RATIO
            ):

                return {

                    "type":
                        "REJECTION",

                    "direction":
                        "SHORT",

                    "level":
                        resistance,

                    "rvol":
                        rvol,
                }

    return None


# ============================================================
# SETUP
# ============================================================

def detect_setup(
    df15,
    trend
):

    breakout = detect_breakout(
        df15,
        trend
    )

    if breakout:

        return breakout

    rejection = detect_rejection(
        df15,
        trend
    )

    if rejection:

        return rejection

    return None


# ============================================================
# 5M CONFIRMATION
# ============================================================

def confirm_5m(
    df5,
    trend
):

    if (
        df5 is None
        or len(df5) < 3
    ):

        return False

    last = df5.iloc[-1]

    o = float(last["open"])

    h = float(last["high"])

    l = float(last["low"])

    c = float(last["close"])

    candle_range = (
        h - l
    )

    if candle_range <= 0:

        return False

    body = abs(
        c - o
    )

    body_ratio = (
        body / candle_range
    )

    if (
        body_ratio
        < MIN_BODY_RATIO
    ):

        return False

    if (
        trend == "LONG"
        and c > o
    ):

        return True

    if (
        trend == "SHORT"
        and c < o
    ):

        return True

    return False


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    trend,
    setup,
    rvol,
    confirmed
):

    score = 0

    if trend in (
        "LONG",
        "SHORT"
    ):

        score += 3

    if setup:

        score += 4

    if rvol >= RVOL_STRONG:

        score += 4

    elif rvol >= RVOL_ABNORMAL:

        score += 3

    if confirmed:

        score += 4

    return score


# ============================================================
# DATABASE HELPERS
# ============================================================

def get_open_trades():

    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status='OPEN'
        ORDER BY id ASC
        """
    ).fetchall()

    conn.close()

    return rows


def get_last_trade_for_symbol(
    symbol
):

    conn = db_connect()

    row = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE symbol=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (symbol,)
    ).fetchone()

    conn.close()

    return row


# ============================================================
# COOLDOWN
# ============================================================

def cooldown_active(
    symbol
):

    row = get_last_trade_for_symbol(
        symbol
    )

    if row is None:

        return False

    status = row["status"]

    if status == "OPEN":

        return False

    exit_dt = parse_datetime(
        row["exit_time"]
    )

    if exit_dt is None:

        return True

    elapsed = (
        utc_now()
        - exit_dt
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


# ============================================================
# SL / TP
# ============================================================

def build_sl_tp(
    df5,
    df15,
    side,
    entry
):

    atr = calculate_atr(
        df5
    )

    if atr <= 0:

        return {

            "valid":
                False,

            "reason":
                "ATR unavailable",
        }

    previous = df15.iloc[
        -SUPPORT_RESISTANCE_LOOKBACK-1:-1
    ]

    support = float(
        previous["low"].min()
    )

    resistance = float(
        previous["high"].max()
    )

    # --------------------------------------------------------
    # Candidate SL
    # --------------------------------------------------------

    if side == "LONG":

        structural_sl = (
            support
            - atr * ATR_SL_BUFFER
        )

        atr_sl = (
            entry
            - atr * ATR_SL_BUFFER
        )

        candidates = [

            structural_sl,

            atr_sl,
        ]

        valid_candidates = [

            x
            for x in candidates
            if x < entry
        ]

        if not valid_candidates:

            return {

                "valid":
                    False,

                "reason":
                    "No valid LONG SL",
            }

        sl = max(
            valid_candidates
        )

    else:

        structural_sl = (
            resistance
            + atr * ATR_SL_BUFFER
        )

        atr_sl = (
            entry
            + atr * ATR_SL_BUFFER
        )

        candidates = [

            structural_sl,

            atr_sl,
        ]

        valid_candidates = [

            x
            for x in candidates
            if x > entry
        ]

        if not valid_candidates:

            return {

                "valid":
                    False,

                "reason":
                    "No valid SHORT SL",
            }

        sl = min(
            valid_candidates
        )

    # --------------------------------------------------------
    # SL percentage
    # --------------------------------------------------------

    sl_pct = (
        abs(entry - sl)
        / entry
    )

    # --------------------------------------------------------
    # MIN SL
    # --------------------------------------------------------

    if sl_pct < MIN_SL_PCT:

        return {

            "valid":
                False,

            "reason":
                (
                    f"SL {sl_pct * 100:.2f}% "
                    f"< MIN "
                    f"{MIN_SL_PCT * 100:.2f}%"
                ),
        }

    # --------------------------------------------------------
    # MAX SL
    # --------------------------------------------------------

    if sl_pct > MAX_SL_PCT:

        return {

            "valid":
                False,

            "reason":
                (
                    f"SL {sl_pct * 100:.2f}% "
                    f"> MAX "
                    f"{MAX_SL_PCT * 100:.2f}%"
                ),
        }

    # --------------------------------------------------------
    # TP
    # --------------------------------------------------------

    risk = abs(
        entry - sl
    )

    if side == "LONG":

        structural_tp = resistance

        minimum_tp = (
            entry
            + risk * MIN_RR
        )

        tp = max(
            structural_tp,
            minimum_tp,
        )

        reward = (
            tp - entry
        )

    else:

        structural_tp = support

        minimum_tp = (
            entry
            - risk * MIN_RR
        )

        tp = min(
            structural_tp,
            minimum_tp,
        )

        reward = (
            entry - tp
        )

    if risk <= 0:

        return {

            "valid":
                False,

            "reason":
                "Invalid risk",
        }

    rr = (
        reward / risk
    )

    # --------------------------------------------------------
    # RR
    # --------------------------------------------------------

    if rr < MIN_RR:

        return {

            "valid":
                False,

            "reason":
                (
                    f"RR {rr:.2f} "
                    f"< MIN "
                    f"{MIN_RR:.2f}"
                ),
        }

    return {

        "valid":
            True,

        "sl":
            sl,

        "tp":
            tp,

        "rr":
            rr,

        "sl_pct":
            sl_pct,
    }


# ============================================================
# INSERT TRADE
# ============================================================

def insert_trade(
    symbol,
    side,
    setup,
    entry,
    sl,
    tp,
    rr,
    score
):

    now = utc_iso()

    conn = db_connect()

    conn.execute(
        """
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

            'OPEN',

            NULL,
            NULL,

            ?,

            NULL,
            NULL,

            0,

            ?
        )
        """,
        (
            symbol,
            side,
            setup,
            entry,
            sl,
            tp,
            rr,
            score,
            now,
            now,
        )
    )

    conn.commit()

    conn.close()


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade_id,
    result,
    pnl,
    exit_reason
):

    conn = db_connect()

    conn.execute(
        """
        UPDATE trades
        SET

            status='CLOSED',

            result=?,

            pnl=?,

            exit_time=?,

            exit_reason=?,

            closed_reported=0

        WHERE id=?
        """,
        (
            result,
            pnl,
            utc_iso(),
            exit_reason,
            trade_id,
        )
    )

    conn.commit()

    conn.close()


# ============================================================
# PNL
# ============================================================

def calculate_trade_pnl(
    side,
    entry,
    current
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
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades():

    trades = get_open_trades()

    time_exit_events = []

    for trade in trades:

        symbol = trade["symbol"]

        try:

            df5 = fetch_ohlcv(
                symbol,
                TIMEFRAME_5M
            )

            if df5.empty:

                continue

            last = df5.iloc[-1]

            high = float(
                last["high"]
            )

            low = float(
                last["low"]
            )

            current = float(
                last["close"]
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

            side = trade["side"]

            # ------------------------------------------------
            # Current PnL
            # ------------------------------------------------

            current_pnl = calculate_trade_pnl(
                side,
                entry,
                current
            )

            # ------------------------------------------------
            # Duration
            # ------------------------------------------------

            duration_mins = duration_minutes(
                trade["entry_time"]
            )

            # ------------------------------------------------
            # TIME EXIT
            #
            # Priority:
            #   SL / TP first based on candle extremes
            #   then TIME EXIT based on closed price
            #
            # If current closed price is +1.50% or more
            # after 2 hours, close with TIME_PROFIT.
            # ------------------------------------------------

            if (
                duration_mins
                >= TIME_EXIT_MINUTES
                and current_pnl
                >= TIME_EXIT_MIN_PROFIT_PCT
            ):

                close_trade(
                    trade["id"],
                    "WIN",
                    current_pnl,
                    "TIME_PROFIT"
                )

                time_exit_events.append(
                    {
                        "symbol":
                            symbol,

                        "side":
                            side,

                        "entry":
                            entry,

                        "exit":
                            current,

                        "pnl":
                            current_pnl,

                        "duration":
                            calculate_duration(
                                trade["entry_time"]
                            ),
                    }
                )

                continue

            # ------------------------------------------------
            # LONG
            # ------------------------------------------------

            if side == "LONG":

                if low <= sl:

                    pnl = calculate_trade_pnl(
                        side,
                        entry,
                        sl
                    )

                    close_trade(
                        trade["id"],
                        "LOSS",
                        pnl,
                        "SL"
                    )

                    continue

                if high >= tp:

                    pnl = calculate_trade_pnl(
                        side,
                        entry,
                        tp
                    )

                    close_trade(
                        trade["id"],
                        "WIN",
                        pnl,
                        "TP"
                    )

                    continue

            # ------------------------------------------------
            # SHORT
            # ------------------------------------------------

            else:

                if high >= sl:

                    pnl = calculate_trade_pnl(
                        side,
                        entry,
                        sl
                    )

                    close_trade(
                        trade["id"],
                        "LOSS",
                        pnl,
                        "SL"
                    )

                    continue

                if low <= tp:

                    pnl = calculate_trade_pnl(
                        side,
                        entry,
                        tp
                    )

                    close_trade(
                        trade["id"],
                        "WIN",
                        pnl,
                        "TP"
                    )

                    continue

        except Exception:

            continue

    return time_exit_events


# ============================================================
# PERFORMANCE
# ============================================================

def get_performance():

    conn = db_connect()

    open_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status='OPEN'
        """
    ).fetchone()[0]

    closed_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status='CLOSED'
        """
    ).fetchone()[0]

    wins = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status='CLOSED'
        AND result='WIN'
        """
    ).fetchone()[0]

    losses = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status='CLOSED'
        AND result='LOSS'
        """
    ).fetchone()[0]

    realized = conn.execute(
        """
        SELECT COALESCE(
            SUM(pnl),
            0
        )
        FROM trades
        WHERE status='CLOSED'
        """
    ).fetchone()[0]

    time_profit_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status='CLOSED'
        AND exit_reason='TIME_PROFIT'
        """
    ).fetchone()[0]

    conn.close()

    if closed_count > 0:

        win_rate = (
            wins
            / closed_count
            * 100
        )

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

        "realized":
            float(
                realized or 0
            ),

        "time_profit":
            time_profit_count,
    }


# ============================================================
# OPEN TRADE FORMAT
# ============================================================

def format_open_trades():

    trades = get_open_trades()

    if not trades:

        return (
            f"📂 OPEN TRADES "
            f"(0/{MAX_OPEN_TRADES})\n"
            "None"
        )

    lines = []

    lines.append(
        f"📂 OPEN TRADES "
        f"({len(trades)}/{MAX_OPEN_TRADES})"
    )

    for trade in trades:

        symbol = trade["symbol"]

        side = trade["side"]

        emoji = (
            "🟢"
            if side == "LONG"
            else "🔴"
        )

        try:

            df5 = fetch_ohlcv(
                symbol,
                TIMEFRAME_5M
            )

            if df5.empty:

                current = float(
                    trade["entry"]
                )

            else:

                current = float(
                    df5.iloc[-1]["close"]
                )

        except Exception:

            current = float(
                trade["entry"]
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

        rr = float(
            trade["rr"] or 0
        )

        pnl = calculate_trade_pnl(
            side,
            entry,
            current
        )

        sl_pct = (
            abs(entry - sl)
            / entry
            * 100
        )

        duration = calculate_duration(
            trade["entry_time"]
        )

        lines.append("")

        lines.append(
            f"{emoji} "
            f"{symbol} "
            f"{side}"
        )

        lines.append(
            f"Duration: {duration}"
        )

        lines.append(
            f"PnL: {pnl:+.2f}%"
        )

        lines.append(
            f"Entry: {entry:.8f}"
        )

        lines.append(
            f"Now: {current:.8f}"
        )

        lines.append(
            f"SL: {sl:.8f} "
            f"({sl_pct:.2f}%)"
        )

        lines.append(
            f"TP: {tp:.8f}"
        )

        lines.append(
            f"RR: 1:{rr:.2f}"
        )

        # ----------------------------------------------------
        # Time Exit status
        # ----------------------------------------------------

        if (
            duration_minutes(
                trade["entry_time"]
            )
            >= TIME_EXIT_MINUTES
        ):

            if (
                pnl
                >= TIME_EXIT_MIN_PROFIT_PCT
            ):

                lines.append(
                    "⏱ TIME EXIT READY"
                )

            else:

                lines.append(
                    "⏱ TIME EXIT: "
                    f"WAIT "
                    f"(needs "
                    f"+{TIME_EXIT_MIN_PROFIT_PCT:.2f}%)"
                )

    return "\n".join(lines)


# ============================================================
# SIGNAL FORMAT
# ============================================================

def format_signal(
    signal
):

    side = signal["side"]

    emoji = (
        "🟢"
        if side == "LONG"
        else "🔴"
    )

    return (

        f"{emoji} "
        f"{signal['symbol']} "
        f"{side}\n"

        f"Setup: "
        f"{signal['setup']}\n"

        f"Score: "
        f"{signal['score']}/15\n"

        f"Entry: "
        f"{signal['entry']:.8f}\n"

        f"SL: "
        f"{signal['sl']:.8f} "
        f"({signal['sl_pct']:.2f}%)\n"

        f"TP: "
        f"{signal['tp']:.8f}\n"

        f"RR: "
        f"1:{signal['rr']:.2f}\n"

        f"RVOL: "
        f"{signal['rvol']:.2f}x"
    )


# ============================================================
# SCANNER
# ============================================================

def scan_markets(
    markets
):

    diagnostics = {

        "scanned":
            0,

        "trend":
            0,

        "trend_ok":
            0,

        "setup":
            0,

        "setup_ok":
            0,

        "confirmation":
            0,

        "confirmation_ok":
            0,

        "score":
            0,

        "cooldown":
            0,

        "open":
            0,

        "sl_low":
            0,

        "sl_high":
            0,

        "rr":
            0,

        "other":
            0,

        "errors":
            0,
    }

    candidates = []

    top_candidate = None

    top_rejection = None

    for symbol in markets:

        diagnostics["scanned"] += 1

        try:

            # ------------------------------------------------
            # 1H
            # ------------------------------------------------

            df1h = fetch_ohlcv(
                symbol,
                TIMEFRAME_1H
            )

            if df1h.empty:

                diagnostics[
                    "errors"
                ] += 1

                continue

            trend = get_trend(
                df1h
            )

            if trend == "NEUTRAL":

                diagnostics[
                    "trend"
                ] += 1

                continue

            diagnostics[
                "trend_ok"
            ] += 1

            # ------------------------------------------------
            # 15M
            # ------------------------------------------------

            df15 = fetch_ohlcv(
                symbol,
                TIMEFRAME_15M
            )

            if df15.empty:

                diagnostics[
                    "errors"
                ] += 1

                continue

            setup = detect_setup(
                df15,
                trend
            )

            if not setup:

                diagnostics[
                    "setup"
                ] += 1

                continue

            diagnostics[
                "setup_ok"
            ] += 1

            # ------------------------------------------------
            # 5M
            # ------------------------------------------------

            df5 = fetch_ohlcv(
                symbol,
                TIMEFRAME_5M
            )

            if df5.empty:

                diagnostics[
                    "errors"
                ] += 1

                continue

            rvol = calculate_rvol(
                df5
            )

            confirmed = confirm_5m(
                df5,
                trend
            )

            score = calculate_score(
                trend,
                setup,
                rvol,
                confirmed
            )

            candidate_preview = {

                "symbol":
                    symbol,

                "side":
                    trend,

                "setup":
                    setup["type"],

                "score":
                    score,

                "rvol":
                    rvol,
            }

            if (
                top_candidate is None
                or score
                > top_candidate["score"]
                or (
                    score
                    == top_candidate["score"]
                    and rvol
                    > top_candidate["rvol"]
                )
            ):

                top_candidate = (
                    candidate_preview
                )

            # ------------------------------------------------
            # Confirmation
            # ------------------------------------------------

            if not confirmed:

                diagnostics[
                    "confirmation"
                ] += 1

                if (
                    top_rejection is None
                    or score
                    > top_rejection["score"]
                ):

                    top_rejection = {

                        **candidate_preview,

                        "reason":
                            "5M Confirmation Failed",
                    }

                continue

            diagnostics[
                "confirmation_ok"
            ] += 1

            # ------------------------------------------------
            # Score
            # ------------------------------------------------

            if score < MIN_SCORE:

                diagnostics[
                    "score"
                ] += 1

                if (
                    top_rejection is None
                    or score
                    > top_rejection["score"]
                ):

                    top_rejection = {

                        **candidate_preview,

                        "reason":
                            (
                                f"Score {score} "
                                f"< MIN "
                                f"{MIN_SCORE}"
                            ),
                    }

                continue

            # ------------------------------------------------
            # Existing OPEN trade
            # ------------------------------------------------

            last_trade = (
                get_last_trade_for_symbol(
                    symbol
                )
            )

            if (
                last_trade is not None
                and last_trade["status"]
                == "OPEN"
            ):

                diagnostics[
                    "open"
                ] += 1

                if (
                    top_rejection is None
                    or score
                    > top_rejection["score"]
                ):

                    top_rejection = {

                        **candidate_preview,

                        "reason":
                            "Already Open",
                    }

                continue

            # ------------------------------------------------
            # Cooldown
            # ------------------------------------------------

            if cooldown_active(
                symbol
            ):

                diagnostics[
                    "cooldown"
                ] += 1

                if (
                    top_rejection is None
                    or score
                    > top_rejection["score"]
                ):

                    top_rejection = {

                        **candidate_preview,

                        "reason":
                            "Re-entry Cooldown Active",
                    }

                continue

            # ------------------------------------------------
            # Entry
            # ------------------------------------------------

            entry = float(
                df5.iloc[-1]["close"]
            )

            # ------------------------------------------------
            # SL / TP
            # ------------------------------------------------

            risk = build_sl_tp(
                df5,
                df15,
                trend,
                entry
            )

            if not risk["valid"]:

                reason = risk["reason"]

                if "MIN" in reason:

                    diagnostics[
                        "sl_low"
                    ] += 1

                elif "MAX" in reason:

                    diagnostics[
                        "sl_high"
                    ] += 1

                elif "RR" in reason:

                    diagnostics[
                        "rr"
                    ] += 1

                else:

                    diagnostics[
                        "other"
                    ] += 1

                if (
                    top_rejection is None
                    or score
                    > top_rejection["score"]
                ):

                    top_rejection = {

                        **candidate_preview,

                        "reason":
                            reason,
                    }

                continue

            # ------------------------------------------------
            # Valid candidate
            # ------------------------------------------------

            signal = {

                "symbol":
                    symbol,

                "side":
                    trend,

                "setup":
                    setup["type"],

                "entry":
                    entry,

                "sl":
                    risk["sl"],

                "tp":
                    risk["tp"],

                "rr":
                    risk["rr"],

                "sl_pct":
                    risk["sl_pct"] * 100,

                "score":
                    score,

                "rvol":
                    rvol,
            }

            candidates.append(
                signal
            )

        except Exception:

            diagnostics[
                "errors"
            ] += 1

            continue

    # --------------------------------------------------------
    # Sort strongest first
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x: (

            x["score"],

            x["rvol"],

            x["rr"],
        ),

        reverse=True,
    )

    return (

        candidates,

        top_candidate,

        top_rejection,

        diagnostics,
    )


# ============================================================
# CREATE NEW TRADES
# ============================================================

def create_new_trades(
    candidates
):

    open_trades = get_open_trades()

    available_slots = (
        MAX_OPEN_TRADES
        - len(open_trades)
    )

    if available_slots <= 0:

        return []

    count = min(

        MAX_NEW_SIGNALS,

        available_slots,

        len(candidates),
    )

    new_signals = []

    for signal in candidates[:count]:

        symbol = signal["symbol"]

        # ----------------------------------------------------
        # Final safety check
        # ----------------------------------------------------

        last_trade = (
            get_last_trade_for_symbol(
                symbol
            )
        )

        if (
            last_trade is not None
            and last_trade["status"]
            == "OPEN"
        ):

            continue

        if cooldown_active(
            symbol
        ):

            continue

        insert_trade(

            symbol=
                symbol,

            side=
                signal["side"],

            setup=
                signal["setup"],

            entry=
                signal["entry"],

            sl=
                signal["sl"],

            tp=
                signal["tp"],

            rr=
                signal["rr"],

            score=
                signal["score"],
        )

        new_signals.append(
            signal
        )

    return new_signals


# ============================================================
# DIAGNOSTICS FORMAT
# ============================================================

def format_diagnostics(
    d
):

    lines = []

    lines.append(
        "🔎 FILTER DIAGNOSTICS"
    )

    lines.append(
        f"Scanned: "
        f"{d['scanned']}"
    )

    lines.append("")

    lines.append(
        "1H FILTER"
    )

    lines.append(
        f"❌ Neutral: "
        f"{d['trend']}"
    )

    lines.append(
        f"✅ Trend OK: "
        f"{d['trend_ok']}"
    )

    lines.append("")

    lines.append(
        "15M SETUP"
    )

    lines.append(
        f"❌ Setup Failed: "
        f"{d['setup']}"
    )

    lines.append(
        f"✅ Setup Passed: "
        f"{d['setup_ok']}"
    )

    lines.append("")

    lines.append(
        "5M CONFIRMATION"
    )

    lines.append(
        f"❌ Confirmation Failed: "
        f"{d['confirmation']}"
    )

    lines.append(
        f"✅ Confirmation Passed: "
        f"{d['confirmation_ok']}"
    )

    lines.append("")

    lines.append(
        f"❌ Score < {MIN_SCORE}: "
        f"{d['score']}"
    )

    lines.append(
        f"❌ Cooldown: "
        f"{d['cooldown']}"
    )

    lines.append(
        f"❌ Already Open: "
        f"{d['open']}"
    )

    lines.append(
        f"❌ SL < "
        f"{MIN_SL_PCT*100:.2f}%: "
        f"{d['sl_low']}"
    )

    lines.append(
        f"❌ SL > "
        f"{MAX_SL_PCT*100:.2f}%: "
        f"{d['sl_high']}"
    )

    lines.append(
        f"❌ RR < "
        f"{MIN_RR:.2f}: "
        f"{d['rr']}"
    )

    lines.append(
        f"❌ Other: "
        f"{d['other']}"
    )

    lines.append(
        f"⚠️ Errors: "
        f"{d['errors']}"
    )

    return "\n".join(
        lines
    )


# ============================================================
# TOP CANDIDATE
# ============================================================

def format_top_candidate(
    top_candidate,
    top_rejection
):

    lines = []

    lines.append(
        "🏆 TOP CANDIDATE"
    )

    if top_rejection:

        side = (
            top_rejection["side"]
        )

        emoji = (
            "🟢"
            if side == "LONG"
            else "🔴"
        )

        lines.append(
            f"{emoji} "
            f"{top_rejection['symbol']} "
            f"{side}"
        )

        lines.append(
            f"Setup: "
            f"{top_rejection['setup']}"
        )

        lines.append(
            f"Score: "
            f"{top_rejection['score']}/15"
        )

        lines.append(
            f"RVOL: "
            f"{top_rejection['rvol']:.2f}x"
        )

        lines.append(
            "❌ REJECTED"
        )

        lines.append(
            f"Reason: "
            f"{top_rejection['reason']}"
        )

        return "\n".join(
            lines
        )

    if top_candidate:

        side = (
            top_candidate["side"]
        )

        emoji = (
            "🟢"
            if side == "LONG"
            else "🔴"
        )

        lines.append(
            f"{emoji} "
            f"{top_candidate['symbol']} "
            f"{side}"
        )

        lines.append(
            f"Setup: "
            f"{top_candidate['setup']}"
        )

        lines.append(
            f"Score: "
            f"{top_candidate['score']}/15"
        )

        lines.append(
            f"RVOL: "
            f"{top_candidate['rvol']:.2f}x"
        )

        lines.append(
            "Status: "
            "No valid entry"
        )

        return "\n".join(
            lines
        )

    lines.append(
        "None"
    )

    return "\n".join(
        lines
    )


# ============================================================
# TIME EXIT FORMAT
# ============================================================

def format_time_exit_events(
    events
):

    if not events:

        return ""

    lines = []

    lines.append(
        "⏱ TIME EXIT"
    )

    for event in events:

        emoji = (
            "🟢"
            if event["side"] == "LONG"
            else "🔴"
        )

        lines.append("")

        lines.append(
            f"{emoji} "
            f"{event['symbol']} "
            f"{event['side']}"
        )

        lines.append(
            f"Duration: "
            f"{event['duration']}"
        )

        lines.append(
            f"Exit: "
            f"{event['exit']:.8f}"
        )

        lines.append(
            f"PnL: "
            f"{event['pnl']:+.2f}%"
        )

        lines.append(
            "Reason: "
            f"{TIME_EXIT_HOURS:.0f}H "
            f"+ "
            f"{TIME_EXIT_MIN_PROFIT_PCT:.2f}% PROFIT"
        )

    return "\n".join(
        lines
    )


# ============================================================
# REPORT
# ============================================================

def build_report(
    new_signals,
    top_candidate,
    top_rejection,
    diagnostics,
    time_exit_events
):

    performance = get_performance()

    lines = []

    lines.append(
        "📊 VOLUME-KHAT 100"
    )

    lines.append(
        f"🕐 "
        f"{iran_time_string()}"
    )

    lines.append(
        "⚡ Kraken Futures | "
        "5M CLOSED | TOP 100"
    )

    # --------------------------------------------------------
    # TIME EXIT EVENTS
    # --------------------------------------------------------

    if time_exit_events:

        lines.append("")

        lines.append(
            format_time_exit_events(
                time_exit_events
            )
        )

    # --------------------------------------------------------
    # NEW SIGNALS
    # --------------------------------------------------------

    lines.append("")

    lines.append(
        "🎯 NEW SIGNALS"
    )

    if not new_signals:

        lines.append(
            "None"
        )

    else:

        for signal in new_signals:

            lines.append("")

            lines.append(
                format_signal(
                    signal
                )
            )

    # --------------------------------------------------------
    # TOP CANDIDATE
    # --------------------------------------------------------

    lines.append("")

    lines.append(
        format_top_candidate(
            top_candidate,
            top_rejection
        )
    )

    # --------------------------------------------------------
    # DIAGNOSTICS
    # --------------------------------------------------------

    lines.append("")

    lines.append(
        format_diagnostics(
            diagnostics
        )
    )

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    lines.append("")

    lines.append(
        format_open_trades()
    )

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    lines.append("")

    lines.append(
        "📈 PERFORMANCE"
    )

    lines.append(
        f"Closed: "
        f"{performance['closed']}"
    )

    lines.append(
        f"✅ Wins: "
        f"{performance['wins']}"
    )

    lines.append(
        f"❌ Losses: "
        f"{performance['losses']}"
    )

    lines.append(
        f"🎯 Win Rate: "
        f"{performance['win_rate']:.1f}%"
    )

    lines.append(
        f"💰 Realized PnL: "
        f"{performance['realized']:+.3f}%"
    )

    lines.append(
        f"⏱ Time-Profit Exits: "
        f"{performance['time_profit']}"
    )

    # --------------------------------------------------------
    # TIME EXIT RULE
    # --------------------------------------------------------

    lines.append("")

    lines.append(
        "⏱ TIME EXIT RULE"
    )

    lines.append(
        f"After: "
        f"{TIME_EXIT_HOURS:.0f} hours"
    )

    lines.append(
        f"Minimum Profit: "
        f"+{TIME_EXIT_MIN_PROFIT_PCT:.2f}%"
    )

    lines.append(
        "Only profitable trades are "
        "closed by this rule."
    )

    # --------------------------------------------------------
    # MODE
    # --------------------------------------------------------

    lines.append("")

    if PAPER_TRADING:

        lines.append(
            "🧪 PAPER TRADING"
        )

    else:

        lines.append(
            "⚠️ LIVE TRADING"
        )

    return "\n".join(
        lines
    )


# ============================================================
# DATABASE STATE
# ============================================================

def get_db_state():

    conn = db_connect()

    total = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        """
    ).fetchone()[0]

    open_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status='OPEN'
        """
    ).fetchone()[0]

    closed_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status='CLOSED'
        """
    ).fetchone()[0]

    last_id_row = conn.execute(
        """
        SELECT id
        FROM trades
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()

    conn.close()

    last_id = (
        last_id_row["id"]
        if last_id_row
        else 0
    )

    return {

        "total":
            total,

        "open":
            open_count,

        "closed":
            closed_count,

        "last_id":
            last_id,
    }


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)

    print(
        "VOLUME-KHAT 100 v3.8"
    )

    print("=" * 60)

    try:

        # ----------------------------------------------------
        # Initialize DB
        # ----------------------------------------------------

        init_db()

        # ----------------------------------------------------
        # ONE-TIME RESET
        # ----------------------------------------------------

        reset_done = (
            perform_v38_reset()
        )

        if reset_done:

            print(
                "V3.8 DATABASE RESET "
                "COMPLETED"
            )

            print(
                "Performance starts "
                "from ZERO."
            )

        # ----------------------------------------------------
        # DB state BEFORE
        # ----------------------------------------------------

        db_state_before = (
            get_db_state()
        )

        print(
            "DB STATE BEFORE:",
            db_state_before
        )

        # ----------------------------------------------------
        # Update existing trades
        # ----------------------------------------------------

        time_exit_events = (
            update_open_trades()
        )

        # ----------------------------------------------------
        # Markets
        # ----------------------------------------------------

        markets = get_top_markets()

        print(
            f"Markets loaded: "
            f"{len(markets)}"
        )

        if not markets:

            report = (

                "📊 VOLUME-KHAT 100\n"

                f"🕐 "
                f"{iran_time_string()}\n\n"

                "⚠️ No markets available."
            )

            send_telegram(
                report
            )

            return

        # ----------------------------------------------------
        # Scan
        # ----------------------------------------------------

        (
            candidates,
            top_candidate,
            top_rejection,
            diagnostics,
        ) = scan_markets(
            markets
        )

        print(
            f"Valid candidates: "
            f"{len(candidates)}"
        )

        # ----------------------------------------------------
        # Create new trades
        # ----------------------------------------------------

        new_signals = (
            create_new_trades(
                candidates
            )
        )

        print(
            f"New signals: "
            f"{len(new_signals)}"
        )

        # ----------------------------------------------------
        # Build report
        # ----------------------------------------------------

        report = build_report(

            new_signals,

            top_candidate,

            top_rejection,

            diagnostics,

            time_exit_events,
        )

        # ----------------------------------------------------
        # DB state AFTER
        # ----------------------------------------------------

        db_state_after = (
            get_db_state()
        )

        print(
            "DB STATE AFTER:",
            db_state_after
        )

        # ----------------------------------------------------
        # Telegram
        # ----------------------------------------------------

        send_telegram(
            report
        )

        print("")

        print(
            report
        )

        print("")

    except Exception as e:

        error_message = (

            "⚠️ VOLUME-KHAT ERROR\n"

            f"Time: "
            f"{iran_time_string()}\n"

            f"Error: "
            f"{str(e)[:1000]}"
        )

        print(
            error_message
        )

        send_telegram(
            error_message
        )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
