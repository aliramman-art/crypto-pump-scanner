# ============================================================
# KRAKEN FUTURES VOLUME-KHAT 100 v4.3
# ============================================================
#
# 1H  = TREND
# 15M = SETUP
# 5M  = STRUCTURE BREAK + PULLBACK + CONFIRMATION
#
# CLOSED CANDLES ONLY
#
# v4.3 FINAL
# ------------------------------------------------------------
# TRADE / DATABASE HARDENING
#
# - Open trades are preserved correctly
# - Atomic trade closing
# - Prevent duplicate open trade per symbol
# - Aggregate statistics calculated from closed trades
# - Repair incomplete closed records when possible
# - Duration tracking
# - TIME_PROFIT exit:
#       after 120 min AND PnL >= +1.50%
# - Reset is OFF by default
# - Database migration for older schemas
# - Missing entry_time is automatically repaired
# - Strategy logic preserved
# - Kraken timeframe format fixed
# - Telegram reporting added
# - API diagnostics added
#
# REAL TRADING: DISABLED
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

# ------------------------------------------------------------
# IMPORTANT:
# Kraken Futures chart API uses:
# 5m / 15m / 1h
# ------------------------------------------------------------

TF_5M = "5m"
TF_15M = "15m"
TF_1H = "1h"

TF_SECONDS = {
    TF_5M: 300,
    TF_15M: 900,
    TF_1H: 3600,
}

OHLCV_LIMIT = 150
REQUEST_TIMEOUT = 20


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
).strip()

TELEGRAM_URL = (
    "https://api.telegram.org/bot"
)


def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "⚠️ TELEGRAM: "
            "TELEGRAM_BOT_TOKEN is missing"
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "⚠️ TELEGRAM: "
            "TELEGRAM_CHAT_ID is missing"
        )

        return False

    if not message:

        print(
            "⚠️ TELEGRAM: empty message"
        )

        return False

    url = (
        TELEGRAM_URL
        + TELEGRAM_BOT_TOKEN
        + "/sendMessage"
    )

    # Telegram message limit is 4096.
    # Keep a safety margin.
    chunks = [
        message[i:i + 3900]
        for i in range(
            0,
            len(message),
            3900
        )
    ]

    success = True

    for index, chunk in enumerate(
        chunks,
        1
    ):

        try:

            response = requests.post(
                url,
                json={
                    "chat_id":
                        TELEGRAM_CHAT_ID,

                    "text":
                        chunk,

                    "disable_web_page_preview":
                        True,
                },
                timeout=REQUEST_TIMEOUT
            )

            if response.status_code != 200:

                success = False

                print(
                    "❌ TELEGRAM ERROR "
                    f"HTTP {response.status_code}: "
                    f"{response.text[:1000]}"
                )

                continue

            try:

                data = response.json()

            except Exception:

                success = False

                print(
                    "❌ TELEGRAM INVALID JSON: "
                    f"{response.text[:1000]}"
                )

                continue

            if not data.get("ok"):

                success = False

                print(
                    "❌ TELEGRAM API ERROR: "
                    f"{data}"
                )

            else:

                print(
                    f"✅ TELEGRAM SENT "
                    f"({index}/{len(chunks)})"
                )

        except Exception as exc:

            success = False

            print(
                "❌ TELEGRAM SEND ERROR: "
                f"{exc}"
            )

    return success


# ============================================================
# RVOL
# ============================================================

RVOL_PERIOD = 20

RVOL_ABNORMAL = 1.70
RVOL_STRONG = 2.50
RVOL_VERY_STRONG = 3.00


# ============================================================
# 15M SETUP
# ============================================================

BREAKOUT_LOOKBACK = 5
SR_LOOKBACK = 20

REJECTION_DISTANCE = 0.008
WICK_BODY_RATIO = 1.15
MIN_BODY_RATIO = 0.20


# ============================================================
# 5M CONFIRMATION
# ============================================================

STRUCTURE_LOOKBACK = 5
PULLBACK_TOLERANCE = 0.0030
MAX_PULLBACK_CANDLES = 6
MIN_CONFIRM_BODY_RATIO = 0.20


# ============================================================
# ATR / SL
# ============================================================

ATR_PERIOD = 14
SL_BUFFER = 0.0015

MIN_SL_PCT = 0.0050
MAX_SL_PCT = 0.0150

MIN_RR = 2.0


# ============================================================
# SCORE
# ============================================================

MIN_SCORE = 9
MAX_SCORE = 15


# ============================================================
# TRADES
# ============================================================

MAX_OPEN_TRADES = 3
MAX_NEW_SIGNALS = 3

COOLDOWN_CANDLES = 3
COOLDOWN_SECONDS = (
    COOLDOWN_CANDLES * 300
)


# ============================================================
# TIME PROFIT EXIT
# ============================================================

TIME_EXIT_MINUTES = 120
TIME_EXIT_MIN_PROFIT_PCT = 1.50


# ============================================================
# MODE
# ============================================================

PAPER_TRADING = True


# ============================================================
# DATABASE
# ============================================================

DB_FILE = "volume_khat_100.db"

SCHEMA_VERSION = "4.3"

RESET_DATABASE_ON_V43_START = (
    os.getenv(
        "RESET_DATABASE_ON_V43_START",
        "0"
    ).strip() == "1"
)

RESET_KEY = (
    "VOLUME_KHAT_V43_RESET_DONE"
)


# ============================================================
# TIMEZONE
# ============================================================

IRAN_TZ = timezone(
    timedelta(
        hours=3,
        minutes=30
    )
)


# ============================================================
# KRAKEN FUTURES
# ============================================================

KRAKEN_OHLCV_URL = (
    "https://futures.kraken.com/"
    "api/charts/v1/trade"
)

KRAKEN_TICKER_URL = (
    "https://futures.kraken.com/"
    "derivatives/api/v3/tickers"
)


# ============================================================
# GLOBAL DIAGNOSTICS
# ============================================================

DIAG = {

    "scanned": 0,

    "trend_neutral": 0,
    "trend_ok": 0,

    "setup_failed": 0,
    "setup_passed": 0,

    "structure_failed": 0,
    "pullback_failed": 0,
    "confirmation_failed": 0,
    "full_5m": 0,

    "score_failed": 0,

    "cooldown": 0,
    "already_open": 0,

    "sl_too_small": 0,
    "sl_too_large": 0,

    "rr_failed": 0,

    "other": 0,
    "errors": 0,

    "api_errors": 0,
    "api_empty": 0,
    "api_parse_errors": 0,

    "tf_5m_errors": 0,
    "tf_15m_errors": 0,
    "tf_1h_errors": 0,
}


# ============================================================
# UTILS
# ============================================================

def utc_now():

    return datetime.now(
        timezone.utc
    )


def iso_now():

    return utc_now().isoformat()


def safe_float(
    value,
    default=None
):

    try:

        return float(value)

    except Exception:

        return default


def pct_change(
    entry,
    current,
    side
):

    if (
        entry is None
        or current is None
        or entry == 0
    ):

        return 0.0

    if side == "LONG":

        return (
            (current - entry)
            / entry
        ) * 100.0

    return (
        (entry - current)
        / entry
    ) * 100.0


def format_pct(value):

    if value is None:

        return "0.00%"

    return f"{value:+.2f}%"


def format_price(value):

    if value is None:

        return "N/A"

    if abs(value) >= 1000:

        return f"{value:.2f}"

    if abs(value) >= 1:

        return f"{value:.5f}"

    if abs(value) >= 0.01:

        return f"{value:.6f}"

    return f"{value:.8f}"


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


def duration_minutes(
    entry_time
):

    dt = parse_datetime(
        entry_time
    )

    if dt is None:

        return 0.0

    seconds = (
        utc_now() - dt
    ).total_seconds()

    if seconds < 0:

        return 0.0

    return seconds / 60.0


# ============================================================
# DATABASE
# ============================================================

def get_db():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30,
        isolation_level=None
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA journal_mode=WAL"
    )

    conn.execute(
        "PRAGMA busy_timeout=30000"
    )

    return conn


def get_meta(key):

    conn = get_db()

    try:

        row = conn.execute(
            """
            SELECT value
            FROM system_meta
            WHERE key = ?
            """,
            (key,)
        ).fetchone()

        if row:

            return row["value"]

        return None

    finally:

        conn.close()


def set_meta(
    key,
    value
):

    conn = get_db()

    try:

        conn.execute(
            """
            INSERT INTO system_meta(
                key,
                value
            )
            VALUES (?, ?)

            ON CONFLICT(key)
            DO UPDATE SET
                value = excluded.value
            """,
            (
                key,
                str(value)
            )
        )

    finally:

        conn.close()


def table_columns(
    conn,
    table_name
):

    rows = conn.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    return {
        row["name"]
        for row in rows
    }


# ============================================================
# DATABASE MIGRATION
# ============================================================

def migrate_database(conn):

    columns = table_columns(
        conn,
        "trades"
    )

    migrated = []

    # --------------------------------------------------------
    # ENTRY TIME
    # --------------------------------------------------------

    if "entry_time" not in columns:

        conn.execute(
            """
            ALTER TABLE trades
            ADD COLUMN entry_time TEXT
            """
        )

        migrated.append(
            "entry_time"
        )

        columns = table_columns(
            conn,
            "trades"
        )

    # --------------------------------------------------------
    # OPTIONAL COLUMNS
    # --------------------------------------------------------

    optional_columns = {

        "exit": "REAL",

        "exit_time": "TEXT",

        "pnl_pct": "REAL",

        "result": "TEXT",

        "exit_reason": "TEXT",

        "duration_minutes":
            "REAL",

        "closed_reported":
            "INTEGER DEFAULT 0",
    }

    for column, definition in (
        optional_columns.items()
    ):

        if column not in columns:

            conn.execute(
                f"""
                ALTER TABLE trades
                ADD COLUMN {column}
                {definition}
                """
            )

            migrated.append(
                column
            )

    # --------------------------------------------------------
    # REPAIR ENTRY TIME FROM CREATED_AT
    # --------------------------------------------------------

    columns = table_columns(
        conn,
        "trades"
    )

    if "created_at" in columns:

        conn.execute(
            """
            UPDATE trades
            SET entry_time = created_at
            WHERE (
                entry_time IS NULL
                OR TRIM(entry_time) = ''
            )
            AND created_at IS NOT NULL
            """
        )

    # --------------------------------------------------------
    # REPAIR FROM ENTRY_TIMESTAMP
    # --------------------------------------------------------

    columns = table_columns(
        conn,
        "trades"
    )

    if "entry_timestamp" in columns:

        conn.execute(
            """
            UPDATE trades
            SET entry_time = entry_timestamp
            WHERE (
                entry_time IS NULL
                OR TRIM(entry_time) = ''
            )
            AND entry_timestamp IS NOT NULL
            """
        )

    # --------------------------------------------------------
    # LAST FALLBACK
    # --------------------------------------------------------

    columns = table_columns(
        conn,
        "trades"
    )

    if "exit_time" in columns:

        conn.execute(
            """
            UPDATE trades
            SET entry_time = exit_time
            WHERE (
                entry_time IS NULL
                OR TRIM(entry_time) = ''
            )
            AND exit_time IS NOT NULL
            """
        )

    # --------------------------------------------------------
    # ABSOLUTE LAST FALLBACK
    # --------------------------------------------------------

    conn.execute(
        """
        UPDATE trades
        SET entry_time = ?
        WHERE (
            entry_time IS NULL
            OR TRIM(entry_time) = ''
        )
        """,
        (iso_now(),)
    )

    if migrated:

        print(
            "🔧 DATABASE MIGRATION:"
        )

        for item in migrated:

            print(
                f"   + {item}"
            )

    remaining = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM trades
        WHERE entry_time IS NULL
           OR TRIM(entry_time) = ''
        """
    ).fetchone()

    if remaining["count"] > 0:

        raise RuntimeError(
            "Database migration failed: "
            "some trades still have "
            "no entry_time"
        )


# ============================================================
# INIT DATABASE
# ============================================================

def init_db():

    conn = get_db()

    try:

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (

                id INTEGER PRIMARY KEY
                AUTOINCREMENT,

                symbol TEXT NOT NULL,
                side TEXT NOT NULL,

                entry REAL NOT NULL,
                sl REAL NOT NULL,
                tp REAL NOT NULL,

                entry_time TEXT,

                exit REAL,
                exit_time TEXT,

                pnl_pct REAL,
                result TEXT,
                exit_reason TEXT,

                duration_minutes REAL,

                closed_reported
                    INTEGER DEFAULT 0
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

        migrate_database(
            conn
        )

        required = {

            "id",
            "symbol",
            "side",

            "entry",
            "sl",
            "tp",

            "entry_time",

            "exit",
            "exit_time",

            "pnl_pct",
            "result",

            "exit_reason",

            "duration_minutes",

            "closed_reported",
        }

        columns = table_columns(
            conn,
            "trades"
        )

        missing = (
            required - columns
        )

        if missing:

            raise RuntimeError(
                "Missing required DB "
                "columns: "
                f"{sorted(missing)}"
            )

        set_meta(
            "schema_version",
            SCHEMA_VERSION
        )

        print(
            "✅ DATABASE READY"
        )

    finally:

        conn.close()


# ============================================================
# DATABASE RESET
# ============================================================

def perform_v43_reset():

    if not RESET_DATABASE_ON_V43_START:

        return

    already_done = get_meta(
        RESET_KEY
    )

    if already_done == "1":

        return

    conn = get_db()

    try:

        conn.execute(
            "BEGIN IMMEDIATE"
        )

        conn.execute(
            "DELETE FROM trades"
        )

        conn.execute(
            """
            INSERT INTO system_meta(
                key,
                value
            )
            VALUES (?, '1')

            ON CONFLICT(key)
            DO UPDATE SET value = '1'
            """,
            (RESET_KEY,)
        )

        conn.commit()

        print(
            "⚠️ V4.3 DATABASE RESET "
            "COMPLETED"
        )

    except Exception:

        conn.rollback()

        raise

    finally:

        conn.close()


# ============================================================
# REPAIR TRADE RECORDS
# ============================================================

def repair_trade_records():

    conn = get_db()

    repaired = 0

    try:

        rows = conn.execute(
            """
            SELECT
                id,
                symbol,
                side,
                entry,
                exit,
                entry_time,
                exit_time,
                pnl_pct,
                result,
                duration_minutes

            FROM trades

            WHERE exit_time IS NOT NULL
            """
        ).fetchall()

        for row in rows:

            exit_price = safe_float(
                row["exit"]
            )

            if exit_price is None:

                continue

            pnl = row["pnl_pct"]

            if pnl is None:

                pnl = pct_change(
                    safe_float(
                        row["entry"]
                    ),
                    exit_price,
                    row["side"]
                )

            result = row["result"]

            if result not in (
                "WIN",
                "LOSS"
            ):

                result = (
                    "WIN"
                    if pnl > 0
                    else "LOSS"
                )

            duration = (
                row["duration_minutes"]
            )

            if duration is None:

                entry_dt = parse_datetime(
                    row["entry_time"]
                )

                exit_dt = parse_datetime(
                    row["exit_time"]
                )

                if (
                    entry_dt
                    and exit_dt
                ):

                    duration = max(
                        0.0,
                        (
                            exit_dt
                            - entry_dt
                        ).total_seconds()
                        / 60.0
                    )

            conn.execute(
                """
                UPDATE trades

                SET
                    pnl_pct = ?,
                    result = ?,
                    duration_minutes = ?

                WHERE id = ?
                """,
                (
                    pnl,
                    result,
                    duration,
                    row["id"]
                )
            )

            repaired += 1

        if repaired:

            print(
                f"🔧 DATABASE REPAIR: "
                f"{repaired} record(s)"
            )

    finally:

        conn.close()


# ============================================================
# KRAKEN OHLCV
# ============================================================

def fetch_ohlcv(
    symbol,
    interval
):

    url = (
        f"{KRAKEN_OHLCV_URL}/"
        f"{symbol}/"
        f"{interval}"
    )

    interval_seconds = (
        TF_SECONDS.get(interval)
    )

    if interval_seconds is None:

        DIAG["api_errors"] += 1

        print(
            f"❌ INVALID TIMEFRAME: "
            f"{interval}"
        )

        return []

    try:

        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            DIAG["api_errors"] += 1

            if interval == TF_5M:

                DIAG[
                    "tf_5m_errors"
                ] += 1

            elif interval == TF_15M:

                DIAG[
                    "tf_15m_errors"
                ] += 1

            elif interval == TF_1H:

                DIAG[
                    "tf_1h_errors"
                ] += 1

            print(
                f"❌ API ERROR "
                f"{symbol} {interval} "
                f"HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )

            return []

        try:

            data = response.json()

        except Exception:

            DIAG[
                "api_parse_errors"
            ] += 1

            print(
                f"❌ API JSON ERROR "
                f"{symbol} {interval}"
            )

            return []

    except Exception as exc:

        DIAG["api_errors"] += 1

        if interval == TF_5M:

            DIAG[
                "tf_5m_errors"
            ] += 1

        elif interval == TF_15M:

            DIAG[
                "tf_15m_errors"
            ] += 1

        elif interval == TF_1H:

            DIAG[
                "tf_1h_errors"
            ] += 1

        print(
            f"❌ REQUEST ERROR "
            f"{symbol} {interval}: "
            f"{exc}"
        )

        return []

    candles = None

    if isinstance(
        data,
        dict
    ):

        for key in (
            "candles",
            "data",
            "result",
            "ohlcv"
        ):

            if key in data:

                candles = data[key]

                break

    elif isinstance(
        data,
        list
    ):

        candles = data

    if not candles:

        DIAG["api_empty"] += 1

        return []

    parsed = []

    now_ts = int(
        time.time()
    )

    for candle in candles:

        try:

            if isinstance(
                candle,
                dict
            ):

                ts = (
                    candle.get("time")
                    or candle.get(
                        "timestamp"
                    )
                    or candle.get("ts")
                )

                o = (
                    candle.get("open")
                    or candle.get("o")
                )

                h = (
                    candle.get("high")
                    or candle.get("h")
                )

                l = (
                    candle.get("low")
                    or candle.get("l")
                )

                c = (
                    candle.get("close")
                    or candle.get("c")
                )

                v = (
                    candle.get("volume")
                    or candle.get("v")
                    or 0
                )

            else:

                if len(candle) < 6:

                    continue

                ts = candle[0]
                o = candle[1]
                h = candle[2]
                l = candle[3]
                c = candle[4]
                v = candle[5]

            ts = float(ts)

            if ts > 10_000_000_000:

                ts /= 1000.0

            o = float(o)
            h = float(h)
            l = float(l)
            c = float(c)
            v = float(v)

            # ------------------------------------------------
            # CLOSED CANDLES ONLY
            # ------------------------------------------------

            if (
                ts + interval_seconds
                > now_ts
            ):

                continue

            parsed.append({

                "ts": ts,

                "open": o,

                "high": h,

                "low": l,

                "close": c,

                "volume": v,
            })

        except Exception:

            DIAG[
                "api_parse_errors"
            ] += 1

            continue

    parsed.sort(
        key=lambda x: x["ts"]
    )

    unique = {}

    for candle in parsed:

        unique[
            candle["ts"]
        ] = candle

    parsed = list(
        unique.values()
    )

    parsed.sort(
        key=lambda x: x["ts"]
    )

    if len(parsed) < 20:

        DIAG["api_empty"] += 1

        return []

    return parsed[
        -OHLCV_LIMIT:
    ]


# ============================================================
# TICKERS
# ============================================================

def get_all_tickers():

    try:

        response = requests.get(
            KRAKEN_TICKER_URL,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            DIAG["api_errors"] += 1

            print(
                "❌ TICKER API ERROR "
                f"HTTP {response.status_code}"
            )

            return {}

        try:

            data = response.json()

        except Exception:

            DIAG[
                "api_parse_errors"
            ] += 1

            return {}

        if isinstance(
            data,
            dict
        ):

            tickers = (
                data.get("tickers")
                or data.get("data")
                or data.get("result")
                or []
            )

        else:

            tickers = data

        result = {}

        for ticker in tickers:

            if not isinstance(
                ticker,
                dict
            ):

                continue

            symbol = (
                ticker.get("symbol")
                or ticker.get("pair")
            )

            if not symbol:

                continue

            result[symbol] = ticker

        return result

    except Exception as exc:

        DIAG["api_errors"] += 1

        print(
            f"❌ TICKER ERROR: {exc}"
        )

        return {}


def get_current_price(
    symbol,
    tickers=None
):

    if tickers is None:

        tickers = (
            get_all_tickers()
        )

    ticker = tickers.get(
        symbol
    )

    if not ticker:

        return None

    for key in (
        "last",
        "lastPrice",
        "last_price",
        "price",
        "markPrice",
        "mark_price"
    ):

        value = ticker.get(
            key
        )

        price = safe_float(
            value
        )

        if (
            price is not None
            and price > 0
        ):

            return price

    return None


# ============================================================
# TOP 100 MARKETS
# ============================================================

def get_top_markets():

    tickers = (
        get_all_tickers()
    )

    markets = []

    for symbol, ticker in (
        tickers.items()
    ):

        if not symbol.startswith(
            "PF_"
        ):

            continue

        if not symbol.endswith(
            "USD"
        ):

            continue

        volume = (
            safe_float(
                ticker.get("vol24h")
            )
            or safe_float(
                ticker.get(
                    "volume24h"
                )
            )
            or safe_float(
                ticker.get(
                    "volume"
                )
            )
            or 0.0
        )

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
        x[0]
        for x in markets[:TOP_N]
    ]


# ============================================================
# INDICATORS
# ============================================================

def sma(
    values,
    period
):

    if len(values) < period:

        return None

    return (
        sum(values[-period:])
        / period
    )


def calculate_rvol(
    candles,
    period=RVOL_PERIOD
):

    if len(candles) < (
        period + 1
    ):

        return 0.0

    current_volume = (
        candles[-1]["volume"]
    )

    previous = [
        x["volume"]
        for x in candles[
            -period - 1:-1
        ]
    ]

    if not previous:

        return 0.0

    avg_volume = (
        sum(previous)
        / len(previous)
    )

    if avg_volume <= 0:

        return 0.0

    return (
        current_volume
        / avg_volume
    )


def calculate_atr(
    candles,
    period=ATR_PERIOD
):

    if len(candles) < (
        period + 1
    ):

        return None

    trs = []

    for i in range(
        1,
        len(candles)
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
            )
        )

        trs.append(tr)

    if len(trs) < period:

        return None

    return (
        sum(trs[-period:])
        / period
    )


# ============================================================
# 1H TREND
# ============================================================

def get_trend(candles):

    if len(candles) < 50:

        return "NEUTRAL"

    closes = [
        x["close"]
        for x in candles
    ]

    sma20 = sma(
        closes,
        20
    )

    sma50 = sma(
        closes,
        50
    )

    if (
        sma20 is None
        or sma50 is None
    ):

        return "NEUTRAL"

    close = closes[-1]

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
# 15M BREAKOUT
# ============================================================

def detect_breakout(
    candles,
    trend
):

    if len(candles) < (
        BREAKOUT_LOOKBACK + 2
    ):

        return None

    current = candles[-1]

    previous = candles[
        -(BREAKOUT_LOOKBACK + 1):-1
    ]

    if not previous:

        return None

    rvol = calculate_rvol(
        candles
    )

    prev_high = max(
        x["high"]
        for x in previous
    )

    prev_low = min(
        x["low"]
        for x in previous
    )

    if (
        trend == "LONG"
        and current["close"]
        > prev_high
        and rvol
        >= RVOL_ABNORMAL
    ):

        return {

            "type":
                "BREAKOUT",

            "side":
                "LONG",

            "level":
                prev_high,

            "rvol":
                rvol,
        }

    if (
        trend == "SHORT"
        and current["close"]
        < prev_low
        and rvol
        >= RVOL_ABNORMAL
    ):

        return {

            "type":
                "BREAKOUT",

            "side":
                "SHORT",

            "level":
                prev_low,

            "rvol":
                rvol,
        }

    return None


# ============================================================
# 15M REJECTION
# ============================================================

def detect_rejection(
    candles,
    trend
):

    if len(candles) < (
        SR_LOOKBACK + 2
    ):

        return None

    current = candles[-1]

    previous = candles[
        -(SR_LOOKBACK + 1):-1
    ]

    resistance = max(
        x["high"]
        for x in previous
    )

    support = min(
        x["low"]
        for x in previous
    )

    body = abs(
        current["close"]
        - current["open"]
    )

    if body <= 0:

        body = max(
            abs(
                current["close"]
            ) * 0.000001,
            1e-12
        )

    upper_wick = (
        current["high"]
        - max(
            current["open"],
            current["close"]
        )
    )

    lower_wick = (
        min(
            current["open"],
            current["close"]
        )
        - current["low"]
    )

    rvol = calculate_rvol(
        candles
    )

    if trend == "SHORT":

        distance = (
            abs(
                current["high"]
                - resistance
            )
            / resistance
        )

        if (
            distance
            <= REJECTION_DISTANCE

            and upper_wick / body
            >= WICK_BODY_RATIO

            and current["close"]
            < current["open"]

            and rvol
            >= RVOL_ABNORMAL
        ):

            return {

                "type":
                    "REJECTION",

                "side":
                    "SHORT",

                "level":
                    resistance,

                "rvol":
                    rvol,
            }

    if trend == "LONG":

        distance = (
            abs(
                current["low"]
                - support
            )
            / support
        )

        if (
            distance
            <= REJECTION_DISTANCE

            and lower_wick / body
            >= WICK_BODY_RATIO

            and current["close"]
            > current["open"]

            and rvol
            >= RVOL_ABNORMAL
        ):

            return {

                "type":
                    "REJECTION",

                "side":
                    "LONG",

                "level":
                    support,

                "rvol":
                    rvol,
            }

    return None


# ============================================================
# 15M SETUP
# ============================================================

def detect_setup(
    candles,
    trend
):

    if trend not in (
        "LONG",
        "SHORT"
    ):

        return None

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

def confirm_5m_structure(
    candles,
    side,
    setup
):

    if len(candles) < (
        STRUCTURE_LOOKBACK
        + MAX_PULLBACK_CANDLES
        + 10
    ):

        return None

    start = max(
        1,
        len(candles)
        - STRUCTURE_LOOKBACK
        - MAX_PULLBACK_CANDLES
        - 5
    )

    last_index = (
        len(candles) - 1
    )

    break_index = None
    break_level = None

    for i in range(
        start,
        last_index
    ):

        current = candles[i]

        left_start = max(
            0,
            i - STRUCTURE_LOOKBACK
        )

        left = candles[
            left_start:i
        ]

        if len(left) < (
            STRUCTURE_LOOKBACK
        ):

            continue

        previous_high = max(
            x["high"]
            for x in left
        )

        previous_low = min(
            x["low"]
            for x in left
        )

        if (
            side == "LONG"
            and current["close"]
            > previous_high
        ):

            break_index = i
            break_level = previous_high

            break

        if (
            side == "SHORT"
            and current["close"]
            < previous_low
        ):

            break_index = i
            break_level = previous_low

            break

    if break_index is None:

        return None

    pullback_index = None

    pullback_end = min(
        last_index,
        break_index
        + MAX_PULLBACK_CANDLES
    )

    for i in range(
        break_index + 1,
        pullback_end + 1
    ):

        candle = candles[i]

        distance = (
            abs(
                candle["close"]
                - break_level
            )
            / max(
                abs(break_level),
                1e-12
            )
        )

        if (
            distance
            <= PULLBACK_TOLERANCE
        ):

            if side == "LONG":

                if candle["low"] <= (
                    break_level
                    * (
                        1
                        + PULLBACK_TOLERANCE
                    )
                ):

                    pullback_index = i

                    break

            else:

                if candle["high"] >= (
                    break_level
                    * (
                        1
                        - PULLBACK_TOLERANCE
                    )
                ):

                    pullback_index = i

                    break

    if pullback_index is None:

        return None

    if pullback_index >= last_index:

        return None

    confirmation = candles[
        last_index
    ]

    body = abs(
        confirmation["close"]
        - confirmation["open"]
    )

    full_range = (
        confirmation["high"]
        - confirmation["low"]
    )

    if full_range <= 0:

        return None

    body_ratio = (
        body / full_range
    )

    if (
        body_ratio
        < MIN_CONFIRM_BODY_RATIO
    ):

        return None

    if side == "LONG":

        if (
            confirmation["close"]
            <= break_level
        ):

            return None

        if (
            confirmation["close"]
            <= confirmation["open"]
        ):

            return None

    else:

        if (
            confirmation["close"]
            >= break_level
        ):

            return None

        if (
            confirmation["close"]
            >= confirmation["open"]
        ):

            return None

    return {

        "break_index":
            break_index,

        "break_level":
            break_level,

        "pullback_index":
            pullback_index,

        "confirmation_index":
            last_index,

        "confirmation_body_ratio":
            body_ratio,
    }


# ============================================================
# STRUCTURAL SL
# ============================================================

def calculate_5m_structural_sl(
    candles,
    confirmation
):

    if not confirmation:

        return None

    start = confirmation[
        "pullback_index"
    ]

    end = confirmation[
        "confirmation_index"
    ]

    section = candles[
        start:end + 1
    ]

    if not section:

        return None

    low = min(
        x["low"]
        for x in section
    )

    high = max(
        x["high"]
        for x in section
    )

    atr = calculate_atr(
        candles
    )

    if atr is None:

        atr = 0.0

    return {

        "low":
            low,

        "high":
            high,

        "atr":
            atr,
    }


# ============================================================
# SL / TP
# ============================================================

def build_sl_tp(
    entry,
    side,
    structural
):

    if (
        entry is None
        or entry <= 0
    ):

        DIAG["other"] += 1

        return None

    if structural is None:

        DIAG["other"] += 1

        return None

    atr = structural["atr"]

    if side == "LONG":

        raw_sl = (
            structural["low"]
            - atr * SL_BUFFER
        )

        if raw_sl >= entry:

            DIAG["other"] += 1

            return None

        sl_distance = (
            entry - raw_sl
        )

        sl_pct = (
            sl_distance
            / entry
        ) * 100.0

        sl_fraction = (
            sl_distance
            / entry
        )

        if sl_fraction < MIN_SL_PCT:

            DIAG[
                "sl_too_small"
            ] += 1

            return None

        if sl_fraction > MAX_SL_PCT:

            DIAG[
                "sl_too_large"
            ] += 1

            return None

        tp = (
            entry
            + sl_distance
            * MIN_RR
        )

    else:

        raw_sl = (
            structural["high"]
            + atr * SL_BUFFER
        )

        if raw_sl <= entry:

            DIAG["other"] += 1

            return None

        sl_distance = (
            raw_sl - entry
        )

        sl_pct = (
            sl_distance
            / entry
        ) * 100.0

        sl_fraction = (
            sl_distance
            / entry
        )

        if sl_fraction < MIN_SL_PCT:

            DIAG[
                "sl_too_small"
            ] += 1

            return None

        if sl_fraction > MAX_SL_PCT:

            DIAG[
                "sl_too_large"
            ] += 1

            return None

        tp = (
            entry
            - sl_distance
            * MIN_RR
        )

    if side == "LONG":

        risk = (
            entry - raw_sl
        )

        reward = (
            tp - entry
        )

    else:

        risk = (
            raw_sl - entry
        )

        reward = (
            entry - tp
        )

    if risk <= 0:

        DIAG["other"] += 1

        return None

    rr = (
        reward / risk
    )

    if rr < MIN_RR:

        DIAG["rr_failed"] += 1

        return None

    return {

        "sl":
            raw_sl,

        "tp":
            tp,

        "sl_pct":
            sl_pct,

        "rr":
            rr,
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

    if trend in (
        "LONG",
        "SHORT"
    ):

        score += 3

    if setup:

        score += 4

    if (
        rvol_5m
        >= RVOL_VERY_STRONG
    ):

        score += 4

    elif (
        rvol_5m
        >= RVOL_STRONG
    ):

        score += 3

    elif (
        rvol_5m
        >= RVOL_ABNORMAL
    ):

        score += 2

    if confirmation:

        score += 4

    return min(
        score,
        MAX_SCORE
    )


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():

    conn = get_db()

    try:

        return conn.execute(
            """
            SELECT *
            FROM trades
            WHERE exit_time IS NULL
            ORDER BY entry_time ASC
            """
        ).fetchall()

    finally:

        conn.close()


def get_open_symbols():

    rows = (
        get_open_trades()
    )

    return {
        row["symbol"]
        for row in rows
    }


# ============================================================
# COOLDOWN
# ============================================================

def is_in_cooldown(
    symbol
):

    conn = get_db()

    try:

        row = conn.execute(
            """
            SELECT exit_time
            FROM trades

            WHERE symbol = ?
              AND exit_time IS NOT NULL

            ORDER BY exit_time DESC

            LIMIT 1
            """,
            (symbol,)
        ).fetchone()

        if not row:

            return False

        exit_dt = parse_datetime(
            row["exit_time"]
        )

        if exit_dt is None:

            return False

        elapsed = (
            utc_now()
            - exit_dt
        ).total_seconds()

        return (
            elapsed
            < COOLDOWN_SECONDS
        )

    finally:

        conn.close()


# ============================================================
# CREATE TRADE
# ============================================================

def create_trade(
    symbol,
    side,
    entry,
    sl,
    tp
):

    conn = get_db()

    try:

        conn.execute(
            "BEGIN IMMEDIATE"
        )

        existing = conn.execute(
            """
            SELECT id
            FROM trades

            WHERE symbol = ?
              AND exit_time IS NULL

            LIMIT 1
            """,
            (symbol,)
        ).fetchone()

        if existing:

            conn.rollback()

            print(
                f"⚠️ CREATE BLOCKED: "
                f"{symbol} already has "
                f"open trade"
            )

            return None

        latest = conn.execute(
            """
            SELECT exit_time
            FROM trades

            WHERE symbol = ?
              AND exit_time IS NOT NULL

            ORDER BY exit_time DESC

            LIMIT 1
            """,
            (symbol,)
        ).fetchone()

        if latest:

            exit_dt = parse_datetime(
                latest["exit_time"]
            )

            if exit_dt:

                elapsed = (
                    utc_now()
                    - exit_dt
                ).total_seconds()

                if (
                    elapsed
                    < COOLDOWN_SECONDS
                ):

                    conn.rollback()

                    print(
                        f"⚠️ CREATE BLOCKED: "
                        f"{symbol} cooldown"
                    )

                    return None

        entry_time = iso_now()

        cursor = conn.execute(
            """
            INSERT INTO trades (

                symbol,
                side,

                entry,
                sl,
                tp,

                entry_time,

                exit,
                exit_time,

                pnl_pct,
                result,

                exit_reason,

                duration_minutes,

                closed_reported
            )

            VALUES (

                ?, ?, ?, ?, ?,

                ?,

                NULL, NULL,

                NULL, NULL,

                NULL,

                NULL,

                0
            )
            """,
            (
                symbol,
                side,
                entry,
                sl,
                tp,
                entry_time,
            )
        )

        trade_id = (
            cursor.lastrowid
        )

        conn.commit()

        return trade_id

    except Exception:

        conn.rollback()

        raise

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

    conn = get_db()

    try:

        conn.execute(
            "BEGIN IMMEDIATE"
        )

        row = conn.execute(
            """
            SELECT *
            FROM trades

            WHERE id = ?
              AND exit_time IS NULL

            LIMIT 1
            """,
            (trade_id,)
        ).fetchone()

        if not row:

            conn.rollback()

            return False

        entry = safe_float(
            row["entry"]
        )

        exit_price = safe_float(
            exit_price
        )

        if (
            entry is None
            or exit_price is None
        ):

            conn.rollback()

            return False

        pnl = pct_change(
            entry,
            exit_price,
            row["side"]
        )

        result = (
            "WIN"
            if pnl > 0
            else "LOSS"
        )

        entry_dt = parse_datetime(
            row["entry_time"]
        )

        now = utc_now()

        if entry_dt:

            duration = max(
                0.0,
                (
                    now - entry_dt
                ).total_seconds()
                / 60.0
            )

        else:

            duration = 0.0

        exit_time = (
            now.isoformat()
        )

        cursor = conn.execute(
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
              AND exit_time IS NULL
            """,
            (
                exit_price,
                exit_time,
                pnl,
                result,
                reason,
                duration,
                trade_id,
            )
        )

        if cursor.rowcount != 1:

            conn.rollback()

            return False

        conn.commit()

        print(
            f"🔒 CLOSED "
            f"{row['symbol']} "
            f"{row['side']} "
            f"{format_pct(pnl)} "
            f"[{reason}] "
            f"{duration:.1f}m"
        )

        return True

    except Exception:

        conn.rollback()

        raise

    finally:

        conn.close()


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades():

    open_trades = (
        get_open_trades()
    )

    if not open_trades:

        return

    tickers = (
        get_all_tickers()
    )

    for trade in open_trades:

        conn = get_db()

        try:

            current_row = conn.execute(
                """
                SELECT exit_time
                FROM trades

                WHERE id = ?
                """,
                (trade["id"],)
            ).fetchone()

        finally:

            conn.close()

        if not current_row:

            continue

        if (
            current_row["exit_time"]
            is not None
        ):

            continue

        current = get_current_price(
            trade["symbol"],
            tickers
        )

        if current is None:

            continue

        entry = safe_float(
            trade["entry"]
        )

        sl = safe_float(
            trade["sl"]
        )

        tp = safe_float(
            trade["tp"]
        )

        if (
            entry is None
            or sl is None
            or tp is None
        ):

            continue

        pnl = pct_change(
            entry,
            current,
            trade["side"]
        )

        duration = duration_minutes(
            trade["entry_time"]
        )

        reason = None

        if trade["side"] == "LONG":

            if current <= sl:

                reason = "SL"

            elif current >= tp:

                reason = "TP"

        else:

            if current >= sl:

                reason = "SL"

            elif current <= tp:

                reason = "TP"

        if (
            reason is None

            and duration
            >= TIME_EXIT_MINUTES

            and pnl
            >= TIME_EXIT_MIN_PROFIT_PCT
        ):

            reason = "TIME_PROFIT"

        if reason:

            close_trade(
                trade["id"],
                current,
                reason
            )


# ============================================================
# PERFORMANCE
# ============================================================

def get_performance():

    conn = get_db()

    try:

        row = conn.execute(
            """
            SELECT

                COUNT(*) AS total_closed,

                SUM(
                    CASE
                        WHEN pnl_pct > 0
                        THEN 1
                        ELSE 0
                    END
                ) AS wins,

                SUM(
                    CASE
                        WHEN pnl_pct <= 0
                        THEN 1
                        ELSE 0
                    END
                ) AS losses,

                COALESCE(
                    SUM(pnl_pct),
                    0
                ) AS net_pnl

            FROM trades

            WHERE exit_time IS NOT NULL
            """
        ).fetchone()

        closed = int(
            row["total_closed"]
            or 0
        )

        wins = int(
            row["wins"]
            or 0
        )

        losses = int(
            row["losses"]
            or 0
        )

        net_pnl = float(
            row["net_pnl"]
            or 0.0
        )

        open_row = conn.execute(
            """
            SELECT COUNT(*) AS count

            FROM trades

            WHERE exit_time IS NULL
            """
        ).fetchone()

        open_count = int(
            open_row["count"]
            or 0
        )

        time_row = conn.execute(
            """
            SELECT COUNT(*) AS count

            FROM trades

            WHERE exit_time IS NOT NULL
              AND exit_reason =
                  'TIME_PROFIT'
            """
        ).fetchone()

        time_exits = int(
            time_row["count"]
            or 0
        )

        accounted = (
            wins + losses
        )

        if accounted != closed:

            print(
                "⚠️ PERFORMANCE WARNING: "
                f"Closed={closed}, "
                f"W+L={accounted}"
            )

        win_rate = (
            (wins / closed)
            * 100.0
            if closed > 0
            else 0.0
        )

        return {

            "open":
                open_count,

            "closed":
                closed,

            "wins":
                wins,

            "losses":
                losses,

            "win_rate":
                win_rate,

            "net_pnl":
                net_pnl,

            "time_exits":
                time_exits,
        }

    finally:

        conn.close()


# ============================================================
# LIVE OPEN DATA
# ============================================================

def get_live_open_data():

    rows = (
        get_open_trades()
    )

    if not rows:

        return []

    tickers = (
        get_all_tickers()
    )

    result = []

    for trade in rows:

        current = get_current_price(
            trade["symbol"],
            tickers
        )

        entry = safe_float(
            trade["entry"]
        )

        pnl = None

        if (
            current is not None
            and entry
        ):

            pnl = pct_change(
                entry,
                current,
                trade["side"]
            )

        duration = duration_minutes(
            trade["entry_time"]
        )

        result.append({

            "trade":
                trade,

            "current":
                current,

            "pnl":
                pnl,

            "duration":
                duration,
        })

    return result


# ============================================================
# REPORT
# ============================================================

def build_report(
    signals,
    candidates
):

    performance = (
        get_performance()
    )

    live_open = (
        get_live_open_data()
    )

    lines = []

    lines.append(
        "📊 VOLUME-KHAT 100"
    )

    lines.append(
        "🕐 "
        + datetime.now(
            IRAN_TZ
        ).strftime(
            "%Y/%m/%d %H:%M:%S"
        )
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

    lines.append(
        "🎯 NEW SIGNALS"
    )

    if not signals:

        lines.append(
            "None"
        )

    else:

        for signal in signals:

            icon = (
                "🟢"
                if signal["side"]
                == "LONG"
                else "🔴"
            )

            lines.append(
                f"{icon} "
                f"{signal['symbol']} "
                f"{signal['side']}"
            )

            lines.append(
                "Entry: "
                + format_price(
                    signal["entry"]
                )
            )

            lines.append(
                "SL: "
                + format_price(
                    signal["sl"]
                )
                + f" "
                f"({signal['sl_pct']:.2f}%)"
            )

            lines.append(
                "TP: "
                + format_price(
                    signal["tp"]
                )
            )

            lines.append(
                f"RR: "
                f"{signal['rr']:.2f}"
            )

            lines.append(
                f"Score: "
                f"{signal['score']}"
                f"/{MAX_SCORE}"
            )

            lines.append(
                f"RVOL: "
                f"{signal['rvol']:.2f}x"
            )

    lines.append("")

    lines.append(
        "🏆 TOP CANDIDATE"
    )

    if not candidates:

        lines.append(
            "None"
        )

    else:

        candidate = sorted(
            candidates,
            key=lambda x:
                x.get(
                    "score",
                    0
                ),
            reverse=True
        )[0]

        side_icon = (
            "🟢"
            if candidate["side"]
            == "LONG"
            else "🔴"
        )

        lines.append(
            f"{side_icon} "
            f"{candidate['symbol']} "
            f"{candidate['side']}"
        )

        lines.append(
            "Setup: "
            + candidate.get(
                "setup_type",
                "N/A"
            )
        )

        lines.append(
            f"Score: "
            f"{candidate.get('score', 0)}"
            f"/{MAX_SCORE}"
        )

        lines.append(
            f"RVOL: "
            f"{candidate.get('rvol', 0):.2f}x"
        )

        if candidate.get(
            "entry"
        ) is not None:

            lines.append(
                "Entry: "
                + format_price(
                    candidate["entry"]
                )
            )

        if candidate.get(
            "sl"
        ) is not None:

            lines.append(
                "SL: "
                + format_price(
                    candidate["sl"]
                )
                + " "
                f"({candidate.get('sl_pct', 0):.2f}%)"
            )

        if candidate.get(
            "tp"
        ) is not None:

            lines.append(
                "TP: "
                + format_price(
                    candidate["tp"]
                )
            )

        if candidate.get(
            "rr"
        ) is not None:

            lines.append(
                f"RR: "
                f"{candidate['rr']:.2f}"
            )

        if candidate.get(
            "reason"
        ):

            lines.append(
                "❌ REJECTED: "
                + candidate["reason"]
            )

    lines.append("")

    lines.append(
        f"📂 OPEN TRADES "
        f"({len(live_open)}/"
        f"{MAX_OPEN_TRADES})"
    )

    if not live_open:

        lines.append(
            "None"
        )

    else:

        for item in live_open:

            trade = item["trade"]

            icon = (
                "🟢"
                if trade["side"]
                == "LONG"
                else "🔴"
            )

            lines.append(
                f"{icon} "
                f"{trade['symbol']} "
                f"{trade['side']}"
            )

            lines.append(
                "Entry: "
                + format_price(
                    trade["entry"]
                )
            )

            lines.append(
                "Now: "
                + format_price(
                    item["current"]
                )
            )

            if item["pnl"] is not None:

                lines.append(
                    "Live PnL: "
                    + format_pct(
                        item["pnl"]
                    )
                )

            lines.append(
                "SL: "
                + format_price(
                    trade["sl"]
                )
            )

            lines.append(
                "TP: "
                + format_price(
                    trade["tp"]
                )
            )

            lines.append(
                f"Duration: "
                f"{item['duration']:.0f}m"
            )

    lines.append("")

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
        f"Net PnL: "
        f"{performance['net_pnl']:+.3f}%"
    )

    lines.append(
        f"Time-Profit Exits: "
        f"{performance['time_exits']}"
    )

    lines.append("")

    lines.append(
        "🔎 FILTER DIAGNOSTICS"
    )

    lines.append(
        f"Scanned: "
        f"{DIAG['scanned']}"
    )

    lines.append(
        f"1H Filter ❌ Neutral: "
        f"{DIAG['trend_neutral']}"
    )

    lines.append(
        f"1H Filter ✅ Trend OK: "
        f"{DIAG['trend_ok']}"
    )

    lines.append(
        f"15M Setup ❌ Failed: "
        f"{DIAG['setup_failed']}"
    )

    lines.append(
        f"15M Setup ✅ Passed: "
        f"{DIAG['setup_passed']}"
    )

    lines.append(
        f"5M Structure ❌ Failed: "
        f"{DIAG['structure_failed']}"
    )

    lines.append(
        f"5M Pullback ❌ Failed: "
        f"{DIAG['pullback_failed']}"
    )

    lines.append(
        f"5M Confirmation ❌ Failed: "
        f"{DIAG['confirmation_failed']}"
    )

    lines.append(
        f"5M Full Confirmation: "
        f"{DIAG['full_5m']}"
    )

    lines.append(
        f"Score < {MIN_SCORE}: "
        f"{DIAG['score_failed']}"
    )

    lines.append(
        f"Cooldown: "
        f"{DIAG['cooldown']}"
    )

    lines.append(
        f"Already Open: "
        f"{DIAG['already_open']}"
    )

    lines.append(
        f"SL < "
        f"{MIN_SL_PCT * 100:.2f}%: "
        f"{DIAG['sl_too_small']}"
    )

    lines.append(
        f"SL > "
        f"{MAX_SL_PCT * 100:.2f}%: "
        f"{DIAG['sl_too_large']}"
    )

    lines.append(
        f"RR < {MIN_RR:.1f}: "
        f"{DIAG['rr_failed']}"
    )

    lines.append(
        f"Other: "
        f"{DIAG['other']}"
    )

    lines.append(
        f"Errors: "
        f"{DIAG['errors']}"
    )

    # --------------------------------------------------------
    # API DIAGNOSTICS
    # --------------------------------------------------------

    lines.append("")

    lines.append(
        "🌐 API DIAGNOSTICS"
    )

    lines.append(
        f"API Errors: "
        f"{DIAG['api_errors']}"
    )

    lines.append(
        f"API Empty: "
        f"{DIAG['api_empty']}"
    )

    lines.append(
        f"API Parse Errors: "
        f"{DIAG['api_parse_errors']}"
    )

    lines.append(
        f"5M API Errors: "
        f"{DIAG['tf_5m_errors']}"
    )

    lines.append(
        f"15M API Errors: "
        f"{DIAG['tf_15m_errors']}"
    )

    lines.append(
        f"1H API Errors: "
        f"{DIAG['tf_1h_errors']}"
    )

    lines.append("")

    lines.append(
        "Real trading: "
        + (
            "ENABLED"
            if not PAPER_TRADING
            else "DISABLED"
        )
    )

    return "\n".join(
        lines
    )


# ============================================================
# SCAN
# ============================================================

def scan():

    markets = (
        get_top_markets()
    )

    if not markets:

        report = (
            "📊 VOLUME-KHAT 100\n"
            "❌ No markets found\n"
            "\n"
            "Real trading: DISABLED"
        )

        print(report)

        return report

    # --------------------------------------------------------
    # Update existing trades first
    # --------------------------------------------------------

    update_open_trades()

    open_trades = (
        get_open_trades()
    )

    open_symbols = {
        row["symbol"]
        for row in open_trades
    }

    available_slots = max(
        0,
        MAX_OPEN_TRADES
        - len(open_trades)
    )

    signals = []
    candidates = []

    for symbol in markets:

        DIAG["scanned"] += 1

        # ----------------------------------------------------
        # ALREADY OPEN
        # ----------------------------------------------------

        if symbol in open_symbols:

            DIAG[
                "already_open"
            ] += 1

            continue

        # ----------------------------------------------------
        # COOLDOWN
        # ----------------------------------------------------

        if is_in_cooldown(
            symbol
        ):

            DIAG[
                "cooldown"
            ] += 1

            continue

        try:

            # =================================================
            # 1H
            # =================================================

            candles_1h = (
                fetch_ohlcv(
                    symbol,
                    TF_1H
                )
            )

            if not candles_1h:

                DIAG["errors"] += 1

                continue

            trend = get_trend(
                candles_1h
            )

            if trend == "NEUTRAL":

                DIAG[
                    "trend_neutral"
                ] += 1

                continue

            DIAG[
                "trend_ok"
            ] += 1

            # =================================================
            # 15M
            # =================================================

            candles_15m = (
                fetch_ohlcv(
                    symbol,
                    TF_15M
                )
            )

            if not candles_15m:

                DIAG["errors"] += 1

                continue

            setup = detect_setup(
                candles_15m,
                trend
            )

            if not setup:

                DIAG[
                    "setup_failed"
                ] += 1

                continue

            DIAG[
                "setup_passed"
            ] += 1

            # =================================================
            # 5M
            # =================================================

            candles_5m = (
                fetch_ohlcv(
                    symbol,
                    TF_5M
                )
            )

            if not candles_5m:

                DIAG["errors"] += 1

                continue

            rvol_5m = (
                calculate_rvol(
                    candles_5m
                )
            )

            confirmation = (
                confirm_5m_structure(
                    candles_5m,
                    trend,
                    setup
                )
            )

            if not confirmation:

                DIAG[
                    "confirmation_failed"
                ] += 1

                continue

            DIAG[
                "full_5m"
            ] += 1

            # =================================================
            # SCORE
            # =================================================

            score = calculate_score(
                trend,
                setup,
                rvol_5m,
                confirmation
            )

            candidate = {

                "symbol":
                    symbol,

                "side":
                    trend,

                "setup_type":
                    setup["type"],

                "score":
                    score,

                "rvol":
                    rvol_5m,

                "reason":
                    None,

                "entry":
                    None,

                "sl":
                    None,

                "tp":
                    None,

                "sl_pct":
                    None,

                "rr":
                    None,
            }

            if score < MIN_SCORE:

                DIAG[
                    "score_failed"
                ] += 1

                candidate["reason"] = (
                    f"Score {score} "
                    f"< {MIN_SCORE}"
                )

                candidates.append(
                    candidate
                )

                continue

            # =================================================
            # ENTRY
            # =================================================

            entry = (
                candles_5m[-1]["close"]
            )

            structural = (
                calculate_5m_structural_sl(
                    candles_5m,
                    confirmation
                )
            )

            sltp = build_sl_tp(
                entry,
                trend,
                structural
            )

            if not sltp:

                candidate["reason"] = (
                    "Invalid SL/TP"
                )

                candidate["entry"] = (
                    entry
                )

                candidates.append(
                    candidate
                )

                continue

            candidate["entry"] = (
                entry
            )

            candidate["sl"] = (
                sltp["sl"]
            )

            candidate["tp"] = (
                sltp["tp"]
            )

            candidate["sl_pct"] = (
                sltp["sl_pct"]
            )

            candidate["rr"] = (
                sltp["rr"]
            )

            candidates.append(
                candidate
            )

            # ------------------------------------------------
            # OPEN SLOT CHECK
            # ------------------------------------------------

            if (
                len(signals)
                >= available_slots

                or

                len(signals)
                >= MAX_NEW_SIGNALS
            ):

                continue

            trade_id = create_trade(
                symbol,
                trend,
                entry,
                sltp["sl"],
                sltp["tp"]
            )

            if trade_id is None:

                continue

            signal = {

                "trade_id":
                    trade_id,

                "symbol":
                    symbol,

                "side":
                    trend,

                "entry":
                    entry,

                "sl":
                    sltp["sl"],

                "tp":
                    sltp["tp"],

                "sl_pct":
                    sltp["sl_pct"],

                "rr":
                    sltp["rr"],

                "score":
                    score,

                "rvol":
                    rvol_5m,
            }

            signals.append(
                signal
            )

            open_symbols.add(
                symbol
            )

        except Exception as exc:

            DIAG[
                "errors"
            ] += 1

            print(
                f"❌ ERROR "
                f"{symbol}: "
                f"{exc}"
            )

    # --------------------------------------------------------
    # FINAL REPORT
    # --------------------------------------------------------

    report = build_report(
        signals,
        candidates
    )

    print(report)

    return report


# ============================================================
# MAIN
# ============================================================

def main():

    try:

        print(
            "============================================================"
        )

        print(
            "KRAKEN FUTURES "
            "VOLUME-KHAT 100 v4.3"
        )

        print(
            "============================================================"
        )

        print(
            f"DB: {DB_FILE}"
        )

        print(
            f"Schema: "
            f"{SCHEMA_VERSION}"
        )

        print(
            "Real trading: "
            + (
                "ENABLED"
                if not PAPER_TRADING
                else "DISABLED"
            )
        )

        print(
            "============================================================"
        )

        print(
            "STARTING SCANNER"
        )

        print(
            "============================================================"
        )

        # ----------------------------------------------------
        # DATABASE
        # ----------------------------------------------------

        init_db()

        # ----------------------------------------------------
        # OPTIONAL RESET
        # ----------------------------------------------------

        perform_v43_reset()

        # ----------------------------------------------------
        # REPAIR
        # ----------------------------------------------------

        repair_trade_records()

        # ----------------------------------------------------
        # UPDATE OPEN TRADES
        # ----------------------------------------------------

        update_open_trades()

        # ----------------------------------------------------
        # SCAN
        # ----------------------------------------------------

        report = scan()

        # ----------------------------------------------------
        # TELEGRAM
        # ----------------------------------------------------

        print(
            "============================================================"
        )

        print(
            "SENDING TELEGRAM REPORT"
        )

        print(
            "============================================================"
        )

        telegram_ok = (
            send_telegram(
                report
            )
        )

        if telegram_ok:

            print(
                "✅ TELEGRAM REPORT SENT"
            )

        else:

            print(
                "❌ TELEGRAM REPORT FAILED"
            )

        print(
            "============================================================"
        )

        print(
            "SCANNER FINISHED"
        )

        print(
            "============================================================"
        )

    except Exception as exc:

        DIAG[
            "errors"
        ] += 1

        fatal_message = (
            "❌ VOLUME-KHAT 100 "
            "FATAL ERROR\n\n"
            f"{exc}\n\n"
            "Real trading: DISABLED"
        )

        print(
            fatal_message
        )

        # ----------------------------------------------------
        # Try to notify Telegram even
        # when scanner crashes.
        # ----------------------------------------------------

        try:

            send_telegram(
                fatal_message
            )

        except Exception as telegram_exc:

            print(
                "❌ Telegram fatal "
                "notification failed: "
                f"{telegram_exc}"
            )

        raise


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
