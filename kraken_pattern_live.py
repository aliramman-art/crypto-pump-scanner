# ============================================================
# KRAKEN FUTURES TRENDLINE LIVE SIGNAL SCANNER
# VERSION 7.2.0
# ============================================================
#
# PAPER ONLY
# REAL_TRADING = False
#
# STRATEGY
#
# LONG:
#   1H swing highs
#        ↓
#   descending resistance trendline
#        ↓
#   1H breakout ABOVE trendline
#        ↓
#   15M swing highs
#        ↓
#   descending resistance trendline
#        ↓
#   15M breakout ABOVE trendline
#        ↓
#   RVOL confirmation
#        ↓
#   LONG
#
# SHORT:
#   1H swing lows
#        ↓
#   ascending support trendline
#        ↓
#   1H breakout BELOW trendline
#        ↓
#   15M swing lows
#        ↓
#   ascending support trendline
#        ↓
#   15M breakout BELOW trendline
#        ↓
#   RVOL confirmation
#        ↓
#   SHORT
#
# TP / SL:
#   LONG:
#       TP = nearest confirmed 15M swing high above entry
#       SL = latest confirmed 15M swing low below entry
#
#   SHORT:
#       TP = nearest confirmed 15M swing low below entry
#       SL = latest confirmed 15M swing high above entry
#
# IMPORTANT:
# - Closed candles only
# - No lookahead
# - Existing DB preserved
# - OLD OPEN trades are removed ONCE on first run of this version
# - Performance starts from new baseline
# - One open trade per asset + direction
# - Opposite direction is allowed
# - Maximum 1 NEW signal per scan
# - REAL_TRADING permanently disabled
# - All reports are combined into ONE Telegram report message
# ============================================================

import os
import io
import json
import traceback
import sqlite3
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "7.2.0"

REAL_TRADING = False
PAPER_TRADING = True

DB_FILE = "kraken_pattern_live_v52.db"

KRAKEN_TICKER_URL = (
    "https://futures.kraken.com/derivatives/api/v3/tickers"
)

KRAKEN_CHART_URL = (
    "https://futures.kraken.com/api/charts/v1/trade"
)

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)

REQUEST_TIMEOUT = 20

MAX_SIGNALS_PER_SCAN = 1

MAX_HOLD_HOURS = 48


# ============================================================
# Trendline
# ============================================================

PIVOT_LEFT = 3
PIVOT_RIGHT = 3

MIN_TRENDLINE_BARS = 8

TRENDLINE_TOLERANCE_PCT = 0.20

BREAKOUT_BUFFER_PCT = 0.05


# ============================================================
# RVOL
# ============================================================

RVOL_LOOKBACK = 20
RVOL_MIN = 1.20


# ============================================================
# Risk
# ============================================================

MIN_RR = 1.01

SL_BUFFER_PCT = 0.05 / 100.0


# ============================================================
# Performance
#
# New reset key for this version.
# Performance only counts CLOSED trades after baseline.
# ============================================================

PERFORMANCE_RESET_KEY = "2026-09-22-7.2.0"


# ============================================================
# Tehran timezone
# ============================================================

TEHRAN_TZ = timezone(
    timedelta(
        hours=3,
        minutes=30
    )
)


# ============================================================
# ASSETS
# ============================================================

ASSETS = [
    "XBT",
    "ETH",
    "SOL",
    "XRP",
    "LTC",
    "DOGE",
    "ADA",
    "LINK",
    "AVAX",
    "DOT",
    "BNB",
    "TRX",
    "UNI",
    "AAVE",
    "SUI",
    "NEAR",
    "ATOM",
    "FIL",
    "ARB",
    "OP",
    "BCH",
    "ETC",
    "XMR",
    "XLM",
    "ALGO",
    "ICP",
    "INJ",
    "TIA",
    "SEI",
    "RUNE",
    "CRV",
    "HBAR",
    "HYPE",
    "ENA",
    "FET",
    "KAS",
    "STX",
    "JUP",
    "PEPE",
    "WIF",
]


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent":
            "Mozilla/5.0 Kraken-Trendline-Scanner/7.2.0"
    }
)


# ============================================================
# SCAN STATS
# ============================================================

SCAN_STATS = {}


def reset_scan_stats():

    global SCAN_STATS

    SCAN_STATS = {
        "assets_scanned": 0,

        "ohlc_1h_ok": 0,
        "ohlc_5m_ok": 0,

        "trendlines_1h": 0,
        "breakouts_1h": 0,

        "trendlines_5m": 0,
        "breakouts_5m": 0,

        "rvol_confirmed": 0,

        "new_signals": 0,

        "errors": 0,
    }


# ============================================================
# TIME
# ============================================================

def utc_now():

    return datetime.now(
        timezone.utc
    )


def iso_utc(dt=None):

    if dt is None:
        dt = utc_now()

    return dt.astimezone(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def parse_time(value):

    if value is None:
        return None

    if isinstance(
        value,
        (
            int,
            float,
            np.integer,
            np.floating
        )
    ):

        if value > 10_000_000_000:
            value = value / 1000.0

        return datetime.fromtimestamp(
            float(value),
            tz=timezone.utc
        )

    text_value = str(
        value
    ).strip()

    if not text_value:
        return None

    try:

        dt = datetime.fromisoformat(
            text_value.replace(
                "Z",
                "+00:00"
            )
        )

        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        )

    except Exception:
        pass

    try:

        return datetime.fromtimestamp(
            float(text_value),
            tz=timezone.utc
        )

    except Exception:

        return None


def tehran_time(value):

    dt = parse_time(
        value
    )

    if dt is None:
        return str(value)

    return dt.astimezone(
        TEHRAN_TZ
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def duration_text(seconds):

    if seconds is None:
        return "0m"

    try:

        seconds = max(
            0,
            int(seconds)
        )

    except Exception:

        return "0m"

    days = seconds // 86400

    seconds %= 86400

    hours = seconds // 3600

    seconds %= 3600

    minutes = seconds // 60

    if days > 0:

        return (
            f"{days}d "
            f"{hours}h "
            f"{minutes}m"
        )

    if hours > 0:

        return (
            f"{hours}h "
            f"{minutes}m"
        )

    return f"{minutes}m"


# ============================================================
# DATABASE
# ============================================================

def get_db():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.row_factory = sqlite3.Row

    return conn


def ensure_column(
    conn,
    table,
    column,
    definition
):

    cur = conn.execute(
        f"PRAGMA table_info({table})"
    )

    columns = {
        row[1]
        for row in cur.fetchall()
    }

    if column not in columns:

        conn.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN {column} {definition}"
        )

        conn.commit()


def init_db():

    conn = get_db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            asset TEXT,
            direction TEXT,

            signal_time TEXT,

            entry REAL,
            tp REAL,
            sl REAL,

            status TEXT,

            exit_time TEXT,
            exit_price REAL,
            exit_reason TEXT,
            pnl_pct REAL,
            duration_seconds INTEGER,

            rr REAL,

            trendline_1h TEXT,
            trendline_5m TEXT,

            breakout_1h_time TEXT,
            breakout_5m_time TEXT,

            tp_source TEXT,
            sl_source TEXT,

            created_at TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS performance_baseline (

            id INTEGER PRIMARY KEY CHECK(id = 1),

            reset_key TEXT NOT NULL,

            reset_time TEXT NOT NULL
        )
        """
    )

    # --------------------------------------------------------
    # Migration
    # --------------------------------------------------------

    ensure_column(
        conn,
        "signals",
        "exit_reason",
        "TEXT"
    )

    ensure_column(
        conn,
        "signals",
        "duration_seconds",
        "INTEGER"
    )

    ensure_column(
        conn,
        "signals",
        "rr",
        "REAL"
    )

    ensure_column(
        conn,
        "signals",
        "trendline_1h",
        "TEXT"
    )

    ensure_column(
        conn,
        "signals",
        "trendline_5m",
        "TEXT"
    )

    ensure_column(
        conn,
        "signals",
        "breakout_1h_time",
        "TEXT"
    )

    ensure_column(
        conn,
        "signals",
        "breakout_5m_time",
        "TEXT"
    )

    ensure_column(
        conn,
        "signals",
        "tp_source",
        "TEXT"
    )

    ensure_column(
        conn,
        "signals",
        "sl_source",
        "TEXT"
    )

    ensure_column(
        conn,
        "signals",
        "created_at",
        "TEXT"
    )

    # --------------------------------------------------------
    # Remove OLD OPEN trades ONCE
    #
    # We use a separate migration marker so the deletion
    # happens only on the first run of VERSION 7.2.0.
    # --------------------------------------------------------

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scanner_migrations (

            key TEXT PRIMARY KEY,

            created_at TEXT NOT NULL
        )
        """
    )

    migration_key = (
        "REMOVE_OLD_OPEN_TRADES_7.2.0"
    )

    migration = conn.execute(
        """
        SELECT key
        FROM scanner_migrations
        WHERE key = ?
        """,
        (migration_key,)
    ).fetchone()

    if migration is None:

        old_open_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM signals
            WHERE status = 'OPEN'
            """
        ).fetchone()[0]

        if old_open_count > 0:

            print(
                "[RESET] Removing "
                f"{old_open_count} old OPEN trades."
            )

            conn.execute(
                """
                DELETE FROM signals
                WHERE status = 'OPEN'
                """
            )

        else:

            print(
                "[RESET] No old OPEN trades found."
            )

        conn.execute(
            """
            INSERT INTO scanner_migrations
            (
                key,
                created_at
            )
            VALUES
            (
                ?,
                ?
            )
            """,
            (
                migration_key,
                iso_utc()
            )
        )

        conn.commit()

    # --------------------------------------------------------
    # Performance baseline
    # --------------------------------------------------------

    row = conn.execute(
        """
        SELECT *
        FROM performance_baseline
        WHERE id = 1
        """
    ).fetchone()

    if row is None:

        conn.execute(
            """
            INSERT INTO performance_baseline
            (
                id,
                reset_key,
                reset_time
            )
            VALUES
            (
                1,
                ?,
                ?
            )
            """,
            (
                PERFORMANCE_RESET_KEY,
                iso_utc()
            )
        )

    elif row["reset_key"] != PERFORMANCE_RESET_KEY:

        conn.execute(
            """
            UPDATE performance_baseline
            SET
                reset_key = ?,
                reset_time = ?
            WHERE id = 1
            """,
            (
                PERFORMANCE_RESET_KEY,
                iso_utc()
            )
        )

    conn.commit()

    conn.close()


# ============================================================
# TELEGRAM
# ============================================================

def telegram_enabled():

    return bool(
        TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    )


def send_telegram(
    text,
    image_bytes=None
):

    if not telegram_enabled():

        print(
            "[TELEGRAM] credentials not configured"
        )

        return False

    try:

        if image_bytes is not None:

            url = (
                "https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            )

            files = {
                "photo": (
                    "chart.png",
                    image_bytes,
                    "image/png"
                )
            }

            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": text,
                "parse_mode": "HTML",
            }

            response = SESSION.post(
                url,
                data=data,
                files=files,
                timeout=REQUEST_TIMEOUT
            )

        else:

            url = (
                "https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            )

            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }

            response = SESSION.post(
                url,
                data=data,
                timeout=REQUEST_TIMEOUT
            )

        if response.ok:

            return True

        print(
            "[TELEGRAM ERROR]",
            response.status_code,
            response.text[:500]
        )

        return False

    except Exception as e:

        print(
            "[TELEGRAM ERROR]",
            e
        )

        return False


# ============================================================
# KRAKEN DATA
# ============================================================

def contract_symbol(asset):

    return f"PF_{asset}USD"


def fetch_tickers():

    try:

        response = SESSION.get(
            KRAKEN_TICKER_URL,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        result = {}

        ticker_list = data.get(
            "tickers",
            []
        )

        for item in ticker_list:

            symbol = item.get(
                "symbol"
            )

            if not symbol:
                continue

            last = (
                item.get("last")
                or item.get("lastPrice")
                or item.get("markPrice")
                or item.get("indexPrice")
            )

            try:

                last = float(last)

            except Exception:

                continue

            result[symbol] = last

        print(
            f"[TICKERS] Loaded "
            f"{len(result)} futures prices"
        )

        return result

    except Exception as e:

        print(
            "[TICKER ERROR]",
            e
        )

        return {}


def normalize_ohlc_payload(
    payload
):

    rows = None

    if isinstance(
        payload,
        dict
    ):

        for key in [
            "candles",
            "data",
            "results",
            "result",
            "ohlc",
        ]:

            if key in payload:

                candidate = payload[key]

                if isinstance(
                    candidate,
                    list
                ):

                    rows = candidate

                    break

                if isinstance(
                    candidate,
                    dict
                ):

                    for nested_key in [
                        "candles",
                        "data",
                        "results",
                    ]:

                        nested = candidate.get(
                            nested_key
                        )

                        if isinstance(
                            nested,
                            list
                        ):

                            rows = nested

                            break

                    if rows is not None:
                        break

    elif isinstance(
        payload,
        list
    ):

        rows = payload

    if not rows:

        return None

    normalized = []

    for row in rows:

        if isinstance(
            row,
            dict
        ):

            timestamp = (
                row.get("time")
                or row.get("timestamp")
                or row.get("ts")
                or row.get("t")
            )

            open_price = (
                row.get("open")
                or row.get("o")
            )

            high_price = (
                row.get("high")
                or row.get("h")
            )

            low_price = (
                row.get("low")
                or row.get("l")
            )

            close_price = (
                row.get("close")
                or row.get("c")
            )

            volume = (
                row.get("volume")
                or row.get("v")
                or 0
            )

        elif isinstance(
            row,
            (list, tuple)
        ):

            if len(row) < 5:
                continue

            timestamp = row[0]
            open_price = row[1]
            high_price = row[2]
            low_price = row[3]
            close_price = row[4]

            volume = (
                row[5]
                if len(row) > 5
                else 0
            )

        else:

            continue

        try:

            timestamp = float(
                timestamp
            )

            if timestamp > 10_000_000_000:

                timestamp /= 1000.0

            open_price = float(
                open_price
            )

            high_price = float(
                high_price
            )

            low_price = float(
                low_price
            )

            close_price = float(
                close_price
            )

            volume = float(
                volume
            )

        except Exception:

            continue

        normalized.append(
            [
                timestamp,
                open_price,
                high_price,
                low_price,
                close_price,
                volume,
            ]
        )

    if not normalized:

        return None

    df = pd.DataFrame(
        normalized,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    )

    df["datetime"] = pd.to_datetime(
        df["timestamp"],
        unit="s",
        utc=True
    )

    df = (
        df
        .sort_values("datetime")
        .drop_duplicates(
            subset=["datetime"]
        )
        .reset_index(drop=True)
    )

    return df


def fetch_ohlc(
    asset,
    resolution
):

    symbol = contract_symbol(
        asset
    )

    if str(resolution) in (
        "60",
        "1H",
        "1h"
    ):

        api_resolution = "1h"

    elif str(resolution) in (
        "5",
        "5M",
        "5m"
    ):

        api_resolution = "5m"

    elif str(resolution) in (
        "15",
        "15M",
        "15m"
    ):

        api_resolution = "15m"

    else:

        api_resolution = str(
            resolution
        )

    url = (
        f"{KRAKEN_CHART_URL}/"
        f"{symbol}/"
        f"{api_resolution}"
    )

    try:

        response = SESSION.get(
            url,
            timeout=REQUEST_TIMEOUT
        )

        if not response.ok:

            print(
                f"[OHLC ERROR] "
                f"{asset} "
                f"{api_resolution}: "
                f"{response.status_code} "
                f"{response.text[:300]}"
            )

            return None

        payload = response.json()

        df = normalize_ohlc_payload(
            payload
        )

        if (
            df is None
            or
            len(df) < 50
        ):

            print(
                f"[OHLC ERROR] "
                f"{asset} "
                f"{api_resolution}: "
                f"not enough candles"
            )

            return None

        # ----------------------------------------------------
        # CLOSED CANDLES ONLY
        # ----------------------------------------------------

        now = utc_now()

        if api_resolution == "1h":

            candle_seconds = 3600

        elif api_resolution == "15m":

            candle_seconds = 900

        elif api_resolution == "5m":

            candle_seconds = 300

        elif api_resolution == "1m":

            candle_seconds = 60

        else:

            candle_seconds = 300

        last_time = df.iloc[-1][
            "datetime"
        ]

        last_dt = last_time.to_pydatetime()

        candle_end = (
            last_dt
            +
            timedelta(
                seconds=candle_seconds
            )
        )

        if candle_end > now:

            df = df.iloc[:-1].copy()

        if len(df) < 50:

            return None

        return df.reset_index(
            drop=True
        )

    except Exception as e:

        print(
            f"[OHLC ERROR] "
            f"{asset} "
            f"{api_resolution}: {e}"
        )

        return None


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(df):

    highs = []
    lows = []

    if (
        df is None
        or
        len(df)
        <
        (
            PIVOT_LEFT
            +
            PIVOT_RIGHT
            +
            5
        )
    ):

        return highs, lows

    highs_array = df[
        "high"
    ].to_numpy(
        dtype=float
    )

    lows_array = df[
        "low"
    ].to_numpy(
        dtype=float
    )

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT
    ):

        current_high = highs_array[i]

        left_highs = highs_array[
            i - PIVOT_LEFT:i
        ]

        right_highs = highs_array[
            i + 1:i + PIVOT_RIGHT + 1
        ]

        if (
            current_high
            >=
            left_highs.max()
            and
            current_high
            >=
            right_highs.max()
        ):

            highs.append(
                {
                    "index": i,
                    "time": df.iloc[i]["datetime"],
                    "price": float(
                        current_high
                    ),
                }
            )

        current_low = lows_array[i]

        left_lows = lows_array[
            i - PIVOT_LEFT:i
        ]

        right_lows = lows_array[
            i + 1:i + PIVOT_RIGHT + 1
        ]

        if (
            current_low
            <=
            left_lows.min()
            and
            current_low
            <=
            right_lows.min()
        ):

            lows.append(
                {
                    "index": i,
                    "time": df.iloc[i]["datetime"],
                    "price": float(
                        current_low
                    ),
                }
            )

    return highs, lows


# ============================================================
# TRENDLINE
# ============================================================

def line_price(
    p1,
    p2,
    index
):

    x1 = float(
        p1["index"]
    )

    x2 = float(
        p2["index"]
    )

    y1 = float(
        p1["price"]
    )

    y2 = float(
        p2["price"]
    )

    if x2 == x1:

        return y1

    slope = (
        (y2 - y1)
        /
        (x2 - x1)
    )

    return (
        y1
        +
        slope
        *
        (
            float(index)
            -
            x1
        )
    )


def trendline_error_pct(
    pivot,
    p1,
    p2
):

    expected = line_price(
        p1,
        p2,
        pivot["index"]
    )

    if expected == 0:

        return 999.0

    return (
        abs(
            pivot["price"]
            -
            expected
        )
        /
        expected
        *
        100.0
    )


def serialize_trendline(
    direction,
    p1,
    p2
):

    slope = (
        (
            p2["price"]
            -
            p1["price"]
        )
        /
        (
            p2["index"]
            -
            p1["index"]
        )
    )

    return json.dumps(
        {
            "direction": direction,

            "p1_index": int(
                p1["index"]
            ),

            "p1_time": str(
                p1["time"]
            ),

            "p1_price": float(
                p1["price"]
            ),

            "p2_index": int(
                p2["index"]
            ),

            "p2_time": str(
                p2["time"]
            ),

            "p2_price": float(
                p2["price"]
            ),

            "slope": float(
                slope
            ),
        }
    )


def find_valid_trendline(
    df,
    direction
):

    highs, lows = find_pivots(
        df
    )

    # LONG:
    #   SWING HIGHS
    #   descending resistance
    #
    # SHORT:
    #   SWING LOWS
    #   ascending support

    if direction == "LONG":

        pivots = highs

    elif direction == "SHORT":

        pivots = lows

    else:

        return None

    if len(pivots) < 2:

        return None

    best = None

    for j in range(
        len(pivots) - 1,
        0,
        -1
    ):

        p2 = pivots[j]

        for i in range(
            j - 1,
            -1,
            -1
        ):

            p1 = pivots[i]

            bars = (
                p2["index"]
                -
                p1["index"]
            )

            if bars < MIN_TRENDLINE_BARS:

                continue

            if direction == "LONG":

                if (
                    p2["price"]
                    >=
                    p1["price"]
                ):

                    continue

            elif direction == "SHORT":

                if (
                    p2["price"]
                    <=
                    p1["price"]
                ):

                    continue

            valid = True

            for pivot in pivots:

                if (
                    pivot["index"]
                    <=
                    p1["index"]
                ):

                    continue

                if (
                    pivot["index"]
                    >
                    p2["index"]
                ):

                    break

                error_pct = (
                    trendline_error_pct(
                        pivot,
                        p1,
                        p2
                    )
                )

                if (
                    error_pct
                    >
                    TRENDLINE_TOLERANCE_PCT
                ):

                    valid = False

                    break

            if not valid:

                continue

            slope_abs = abs(
                (
                    p2["price"]
                    -
                    p1["price"]
                )
                /
                bars
            )

            recency_score = (
                p2["index"]
            )

            score = (
                bars * 0.10
                +
                recency_score * 0.001
                +
                slope_abs * 1000000
            )

            candidate = {
                "direction": direction,
                "p1": p1,
                "p2": p2,
                "bars": bars,
                "score": score,
            }

            if (
                best is None
                or
                candidate["score"]
                >
                best["score"]
            ):

                best = candidate

    return best


# ============================================================
# TRENDLINE BREAKOUT
# ============================================================

def find_trendline_breakout(
    df,
    trendline,
    direction
):

    if trendline is None:

        return None

    p1 = trendline["p1"]

    p2 = trendline["p2"]

    start_index = (
        p2["index"] + 1
    )

    if start_index >= len(df):

        return None

    buffer = (
        BREAKOUT_BUFFER_PCT
        /
        100.0
    )

    for i in range(
        start_index,
        len(df)
    ):

        row = df.iloc[i]

        line = line_price(
            p1,
            p2,
            i
        )

        close = float(
            row["close"]
        )

        if direction == "LONG":

            breakout_level = (
                line
                *
                (
                    1.0
                    +
                    buffer
                )
            )

            if close > breakout_level:

                return {
                    "index": i,
                    "time": row["datetime"],
                    "price": close,
                    "line_price": line,
                }

        elif direction == "SHORT":

            breakout_level = (
                line
                *
                (
                    1.0
                    -
                    buffer
                )
            )

            if close < breakout_level:

                return {
                    "index": i,
                    "time": row["datetime"],
                    "price": close,
                    "line_price": line,
                }

    return None


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(
    df,
    index
):

    if index < RVOL_LOOKBACK:

        return None

    current_volume = float(
        df.iloc[index]["volume"]
    )

    start = max(
        0,
        index - RVOL_LOOKBACK
    )

    end = index

    previous = df.iloc[
        start:end
    ]["volume"].astype(
        float
    )

    if len(previous) < 5:

        return None

    average_volume = (
        previous.mean()
    )

    if average_volume <= 0:

        return None

    return (
        current_volume
        /
        average_volume
    )


# ============================================================
# 15M TP / SL LEVELS
#
# DB column names remain trendline_5m and breakout_5m_time
# for backward compatibility with the existing database.
# Actual stored data is now 15M.
# ============================================================

def get_confirmed_15m_levels(
    df,
    breakout_index,
    direction
):

    highs, lows = find_pivots(
        df
    )

    confirmed_highs = [
        x
        for x in highs
        if x["index"] < breakout_index
    ]

    confirmed_lows = [
        x
        for x in lows
        if x["index"] < breakout_index
    ]

    entry = float(
        df.iloc[
            breakout_index
        ]["close"]
    )

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        target_candidates = [
            x
            for x in confirmed_highs
            if x["price"] > entry
        ]

        stop_candidates = [
            x
            for x in confirmed_lows
            if x["price"] < entry
        ]

        if not target_candidates:

            return None

        if not stop_candidates:

            return None

        tp_pivot = min(
            target_candidates,
            key=lambda x: x["price"]
        )

        sl_pivot = max(
            stop_candidates,
            key=lambda x: x["index"]
        )

        tp = float(
            tp_pivot["price"]
        )

        sl = float(
            sl_pivot["price"]
        )

        sl = sl * (
            1.0
            -
            SL_BUFFER_PCT
        )

        risk = (
            entry
            -
            sl
        )

        reward = (
            tp
            -
            entry
        )

        if risk <= 0:

            return None

        if reward <= 0:

            return None

        rr = (
            reward
            /
            risk
        )

        if rr < MIN_RR:

            return None

        return {
            "entry": entry,
            "tp": tp,
            "sl": sl,
            "rr": rr,

            "tp_source": (
                f"15M swing high "
                f"{tp_pivot['price']:.8f}"
            ),

            "sl_source": (
                f"15M swing low "
                f"{sl_pivot['price']:.8f}"
            ),
        }

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        target_candidates = [
            x
            for x in confirmed_lows
            if x["price"] < entry
        ]

        stop_candidates = [
            x
            for x in confirmed_highs
            if x["price"] > entry
        ]

        if not target_candidates:

            return None

        if not stop_candidates:

            return None

        tp_pivot = max(
            target_candidates,
            key=lambda x: x["price"]
        )

        sl_pivot = max(
            stop_candidates,
            key=lambda x: x["index"]
        )

        tp = float(
            tp_pivot["price"]
        )

        sl = float(
            sl_pivot["price"]
        )

        sl = sl * (
            1.0
            +
            SL_BUFFER_PCT
        )

        risk = (
            sl
            -
            entry
        )

        reward = (
            entry
            -
            tp
        )

        if risk <= 0:

            return None

        if reward <= 0:

            return None

        rr = (
            reward
            /
            risk
        )

        if rr < MIN_RR:

            return None

        return {
            "entry": entry,
            "tp": tp,
            "sl": sl,
            "rr": rr,

            "tp_source": (
                f"15M swing low "
                f"{tp_pivot['price']:.8f}"
            ),

            "sl_source": (
                f"15M swing high "
                f"{sl_pivot['price']:.8f}"
            ),
        }

    return None


# ============================================================
# OPEN TRADE CHECK
# ============================================================

def has_open_trade(
    asset,
    direction
):

    conn = get_db()

    row = conn.execute(
        """
        SELECT id
        FROM signals
        WHERE
            asset = ?
            AND direction = ?
            AND status = 'OPEN'
        LIMIT 1
        """,
        (
            asset,
            direction
        )
    ).fetchone()

    conn.close()

    return row is not None


# ============================================================
# INSERT SIGNAL
# ============================================================

def insert_signal(
    signal
):

    conn = get_db()

    existing = conn.execute(
        """
        SELECT id
        FROM signals
        WHERE
            asset = ?
            AND direction = ?
            AND status = 'OPEN'
        LIMIT 1
        """,
        (
            signal["asset"],
            signal["direction"]
        )
    ).fetchone()

    if existing is not None:

        conn.close()

        return False, None

    cursor = conn.execute(
        """
        INSERT INTO signals
        (
            asset,
            direction,
            signal_time,
            entry,
            tp,
            sl,
            status,
            exit_time,
            exit_price,
            exit_reason,
            pnl_pct,
            duration_seconds,
            rr,
            trendline_1h,
            trendline_5m,
            breakout_1h_time,
            breakout_5m_time,
            tp_source,
            sl_source,
            created_at
        )
        VALUES
        (
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            'OPEN',
            NULL,
            NULL,
            NULL,
            NULL,
            NULL,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?
        )
        """,
        (
            signal["asset"],
            signal["direction"],
            signal["signal_time"],
            signal["entry"],
            signal["tp"],
            signal["sl"],
            signal["rr"],
            signal["trendline_1h"],
            signal["trendline_5m"],
            signal["breakout_1h_time"],
            signal["breakout_5m_time"],
            signal["tp_source"],
            signal["sl_source"],
            signal["created_at"],
        )
    )

    conn.commit()

    row_id = cursor.lastrowid

    conn.close()

    return True, row_id


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade_id,
    exit_price,
    exit_reason
):

    conn = get_db()

    row = conn.execute(
        """
        SELECT *
        FROM signals
        WHERE id = ?
        """,
        (trade_id,)
    ).fetchone()

    if row is None:

        conn.close()

        return None

    entry = float(
        row["entry"]
    )

    direction = row[
        "direction"
    ]

    if direction == "LONG":

        pnl_pct = (
            exit_price
            -
            entry
        ) / entry * 100.0

    else:

        pnl_pct = (
            entry
            -
            exit_price
        ) / entry * 100.0

    signal_dt = parse_time(
        row["signal_time"]
    )

    exit_dt = utc_now()

    duration_seconds = 0

    if signal_dt is not None:

        duration_seconds = int(
            (
                exit_dt
                -
                signal_dt
            ).total_seconds()
        )

    conn.execute(
        """
        UPDATE signals
        SET
            status = 'CLOSED',
            exit_time = ?,
            exit_price = ?,
            exit_reason = ?,
            pnl_pct = ?,
            duration_seconds = ?
        WHERE id = ?
        """,
        (
            iso_utc(exit_dt),
            float(exit_price),
            exit_reason,
            float(pnl_pct),
            duration_seconds,
            trade_id,
        )
    )

    conn.commit()

    updated = conn.execute(
        """
        SELECT *
        FROM signals
        WHERE id = ?
        """,
        (trade_id,)
    ).fetchone()

    conn.close()

    return updated


# ============================================================
# RECONCILE OPEN TRADES
#
# IMPORTANT:
# This function NO LONGER sends Telegram messages.
# Closed trades are returned and included in the unified report.
# ============================================================

def reconcile_open_trades(
    prices
):

    conn = get_db()

    trades = conn.execute(
        """
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY id ASC
        """
    ).fetchall()

    conn.close()

    closed_trades = []

    for trade in trades:

        asset = trade["asset"]

        direction = trade[
            "direction"
        ]

        current = prices.get(
            contract_symbol(asset)
        )

        if current is None:

            continue

        entry = float(
            trade["entry"]
        )

        tp = float(
            trade["tp"]
        )

        sl = float(
            trade["sl"]
        )

        exit_reason = None

        exit_price = None

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if direction == "LONG":

            if current >= tp:

                exit_reason = "TP"

                exit_price = tp

            elif current <= sl:

                exit_reason = "SL"

                exit_price = sl

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        elif direction == "SHORT":

            if current <= tp:

                exit_reason = "TP"

                exit_price = tp

            elif current >= sl:

                exit_reason = "SL"

                exit_price = sl

        # ----------------------------------------------------
        # TIME EXIT
        # ----------------------------------------------------

        if exit_reason is None:

            signal_dt = parse_time(
                trade["signal_time"]
            )

            if signal_dt is not None:

                age_hours = (
                    utc_now()
                    -
                    signal_dt
                ).total_seconds() / 3600.0

                if age_hours >= MAX_HOLD_HOURS:

                    exit_reason = "TIME"

                    exit_price = current

        if exit_reason is None:

            continue

        closed = close_trade(
            trade["id"],
            float(exit_price),
            exit_reason
        )

        if closed is None:

            continue

        closed_trades.append(
            closed
        )

    return closed_trades


# ============================================================
# CHART
# ============================================================

def create_chart(
    asset,
    df,
    direction,
    trendline,
    breakout,
    entry,
    tp,
    sl
):

    try:

        p1 = trendline["p1"]

        p2 = trendline["p2"]

        breakout_index = (
            breakout["index"]
        )

        start_index = max(
            0,
            min(
                p1["index"],
                p2["index"],
                breakout_index
            )
            -
            20
        )

        end_index = min(
            len(df),
            max(
                p1["index"],
                p2["index"],
                breakout_index
            )
            +
            40
        )

        chart_df = df.iloc[
            start_index:end_index
        ].copy()

        if len(chart_df) < 10:

            chart_df = df.tail(
                min(
                    200,
                    len(df)
                )
            ).copy()

            start_index = (
                len(df)
                -
                len(chart_df)
            )

        fig, ax = plt.subplots(
            figsize=(13, 7)
        )

        # ----------------------------------------------------
        # Candles
        # ----------------------------------------------------

        for local_i, (
            idx,
            row
        ) in enumerate(
            chart_df.iterrows()
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

            ax.plot(
                [local_i, local_i],
                [l, h],
                linewidth=1
            )

            if c >= o:

                bottom = o

                height = c - o

            else:

                bottom = c

                height = o - c

            if height == 0:

                height = max(
                    abs(c) * 0.0001,
                    1e-12
                )

            ax.bar(
                local_i,
                height,
                bottom=bottom,
                width=0.65
            )

        # ----------------------------------------------------
        # Trendline
        # ----------------------------------------------------

        x1 = (
            p1["index"]
            -
            start_index
        )

        x2 = (
            p2["index"]
            -
            start_index
        )

        line_x = np.arange(
            0,
            len(chart_df)
        )

        line_y = np.array(
            [
                line_price(
                    p1,
                    p2,
                    x + start_index
                )
                for x in line_x
            ]
        )

        ax.plot(
            line_x,
            line_y,
            linewidth=2,
            label=(
                "LONG Resistance"
                if direction == "LONG"
                else
                "SHORT Support"
            )
        )

        # ----------------------------------------------------
        # P1 / P2
        # ----------------------------------------------------

        if (
            0
            <=
            x1
            <
            len(chart_df)
        ):

            ax.scatter(
                [x1],
                [p1["price"]],
                s=70,
                marker="o",
                zorder=5
            )

            ax.annotate(
                "P1",
                (
                    x1,
                    p1["price"]
                ),
                xytext=(5, 8),
                textcoords="offset points"
            )

        if (
            0
            <=
            x2
            <
            len(chart_df)
        ):

            ax.scatter(
                [x2],
                [p2["price"]],
                s=70,
                marker="o",
                zorder=5
            )

            ax.annotate(
                "P2",
                (
                    x2,
                    p2["price"]
                ),
                xytext=(5, 8),
                textcoords="offset points"
            )

        # ----------------------------------------------------
        # Breakout
        # ----------------------------------------------------

        bx = (
            breakout["index"]
            -
            start_index
        )

        if (
            0
            <=
            bx
            <
            len(chart_df)
        ):

            ax.scatter(
                [bx],
                [breakout["price"]],
                s=100,
                marker="*",
                zorder=6
            )

            ax.annotate(
                "BREAKOUT",
                (
                    bx,
                    breakout["price"]
                ),
                xytext=(5, 10),
                textcoords="offset points"
            )

        # ----------------------------------------------------
        # Entry / TP / SL
        # ----------------------------------------------------

        ax.axhline(
            entry,
            linewidth=1.5,
            linestyle="--",
            label="ENTRY"
        )

        ax.axhline(
            tp,
            linewidth=1.2,
            linestyle="--",
            label="TP"
        )

        ax.axhline(
            sl,
            linewidth=1.2,
            linestyle="--",
            label="SL"
        )

        title = (
            f"KRAKEN 15M "
            f"{asset} {direction}\n"
            f"Trendline Breakout Confirmation"
        )

        ax.set_title(
            title
        )

        ax.set_xlabel(
            "15M Closed Candles"
        )

        ax.set_ylabel(
            "Price"
        )

        ax.grid(
            alpha=0.25
        )

        ax.legend(
            loc="best"
        )

        plt.tight_layout()

        buffer = io.BytesIO()

        fig.savefig(
            buffer,
            format="png",
            dpi=150,
            bbox_inches="tight"
        )

        plt.close(fig)

        buffer.seek(0)

        return buffer.getvalue()

    except Exception as e:

        print(
            "[CHART ERROR]",
            asset,
            e
        )

        try:

            plt.close("all")

        except Exception:

            pass

        return None


# ============================================================
# NEW SIGNAL TEXT
# ============================================================

def build_new_signal_text(
    signal
):

    direction = signal[
        "direction"
    ]

    emoji = (
        "🟢"
        if direction == "LONG"
        else
        "🔴"
    )

    entry = signal[
        "entry"
    ]

    tp = signal[
        "tp"
    ]

    sl = signal[
        "sl"
    ]

    if direction == "LONG":

        tp_pct = (
            tp
            -
            entry
        ) / entry * 100.0

        sl_pct = (
            sl
            -
            entry
        ) / entry * 100.0

    else:

        tp_pct = (
            entry
            -
            tp
        ) / entry * 100.0

        sl_pct = (
            entry
            -
            sl
        ) / entry * 100.0

    return (
        f"<b>🚨 NEW SIGNAL</b>\n\n"
        f"{emoji} <b>{direction} "
        f"{signal['asset']}</b>\n"
        f"Entry: "
        f"<code>{entry:.8f}</code>\n"
        f"TP: "
        f"<code>{tp:.8f}</code> "
        f"({tp_pct:+.2f}%)\n"
        f"SL: "
        f"<code>{sl:.8f}</code> "
        f"({sl_pct:+.2f}%)\n"
        f"RR: "
        f"<b>{signal['rr']:.2f}</b>\n"
        f"RVOL: "
        f"<b>{signal['rvol']:.2f}</b>\n"
        f"1H Breakout: "
        f"{tehran_time(signal['breakout_1h_time'])}\n"
        f"15M Breakout: "
        f"{tehran_time(signal['breakout_5m_time'])}\n"
        f"TP Source: "
        f"{signal['tp_source']}\n"
        f"SL Source: "
        f"{signal['sl_source']}\n"
        f"Signal Time: "
        f"{tehran_time(signal['signal_time'])}\n"
        f"Mode: <b>PAPER ONLY</b>"
    )


# ============================================================
# CLOSED TRADES REPORT SECTION
# ============================================================

def build_closed_trades_section(
    closed_trades
):

    if not closed_trades:

        return (
            "<b>🔔 CLOSED TRADES</b>\n"
            "No new closed trades."
        )

    lines = [
        "<b>🔔 CLOSED TRADES</b>",
        ""
    ]

    for trade in closed_trades:

        direction = trade[
            "direction"
        ]

        emoji = (
            "🟢"
            if direction == "LONG"
            else
            "🔴"
        )

        pnl = float(
            trade["pnl_pct"]
            or 0
        )

        reason = (
            trade["exit_reason"]
            or "-"
        )

        lines.extend(
            [
                (
                    f"{emoji} "
                    f"<b>{trade['asset']} "
                    f"{direction}</b>"
                ),
                (
                    f"Entry: "
                    f"<code>"
                    f"{float(trade['entry']):.8f}"
                    f"</code>"
                ),
                (
                    f"Exit: "
                    f"<code>"
                    f"{float(trade['exit_price']):.8f}"
                    f"</code>"
                ),
                (
                    f"Reason: <b>{reason}</b>"
                ),
                (
                    f"PnL: "
                    f"<b>{pnl:+.2f}%</b>"
                ),
                (
                    f"Duration: "
                    f"<b>"
                    f"{duration_text(trade['duration_seconds'])}"
                    f"</b>"
                ),
                (
                    f"Exit Time: "
                    f"{tehran_time(trade['exit_time'])}"
                ),
                ""
            ]
        )

    return "\n".join(
        lines
    ).rstrip()


# ============================================================
# OPEN TRADES REPORT SECTION
# ============================================================

def build_open_trades_section(
    prices
):

    conn = get_db()

    trades = conn.execute(
        """
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY signal_time ASC
        """
    ).fetchall()

    conn.close()

    if not trades:

        return (
            "<b>📂 OPEN TRADES</b>\n"
            "No open trades."
        )

    lines = [
        "<b>📂 OPEN TRADES</b>",
        ""
    ]

    now = utc_now()

    for trade in trades:

        asset = trade[
            "asset"
        ]

        direction = trade[
            "direction"
        ]

        emoji = (
            "🟢"
            if direction == "LONG"
            else
            "🔴"
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

        current = prices.get(
            contract_symbol(asset)
        )

        if current is None:

            current = entry

        if direction == "LONG":

            pnl = (
                current
                -
                entry
            ) / entry * 100.0

        else:

            pnl = (
                entry
                -
                current
            ) / entry * 100.0

        signal_dt = parse_time(
            trade["signal_time"]
        )

        duration = 0

        if signal_dt is not None:

            duration = int(
                (
                    now
                    -
                    signal_dt
                ).total_seconds()
            )

        lines.extend(
            [
                (
                    f"{emoji} "
                    f"<b>{asset} "
                    f"{direction}</b>"
                ),
                (
                    f"Entry: "
                    f"<code>{entry:.8f}</code>"
                ),
                (
                    f"Current: "
                    f"<code>{current:.8f}</code>"
                ),
                (
                    f"PnL: "
                    f"<b>{pnl:+.2f}%</b>"
                ),
                (
                    f"TP: "
                    f"<code>{tp:.8f}</code>"
                ),
                (
                    f"SL: "
                    f"<code>{sl:.8f}</code>"
                ),
                (
                    f"Duration: "
                    f"<b>{duration_text(duration)}</b>"
                ),
                ""
            ]
        )

    return "\n".join(
        lines
    ).rstrip()


# ============================================================
# PERFORMANCE SECTION
# ============================================================

def build_performance_section():

    conn = get_db()

    baseline = conn.execute(
        """
        SELECT reset_time
        FROM performance_baseline
        WHERE id = 1
        """
    ).fetchone()

    if baseline is None:

        conn.close()

        return (
            "<b>📈 PERFORMANCE</b>\n"
            "No baseline."
        )

    reset_time = baseline[
        "reset_time"
    ]

    rows = conn.execute(
        """
        SELECT *
        FROM signals
        WHERE
            status = 'CLOSED'
            AND exit_time >= ?
        ORDER BY id ASC
        """,
        (
            reset_time,
        )
    ).fetchall()

    conn.close()

    total = len(
        rows
    )

    wins = 0
    losses = 0
    breakeven = 0

    tp_count = 0
    sl_count = 0
    time_count = 0

    net_pnl = 0.0

    for row in rows:

        pnl = float(
            row["pnl_pct"]
            or 0
        )

        net_pnl += pnl

        if pnl > 0:

            wins += 1

        elif pnl < 0:

            losses += 1

        else:

            breakeven += 1

        reason = row[
            "exit_reason"
        ]

        if reason == "TP":

            tp_count += 1

        elif reason == "SL":

            sl_count += 1

        elif reason == "TIME":

            time_count += 1

    if total > 0:

        win_rate = (
            wins
            /
            total
            *
            100.0
        )

    else:

        win_rate = 0.0

    return (
        "<b>📈 PERFORMANCE</b>\n\n"
        f"Total Trades: <b>{total}</b>\n"
        f"Wins: <b>{wins}</b>\n"
        f"Losses: <b>{losses}</b>\n"
        f"Breakeven: <b>{breakeven}</b>\n"
        f"Win Rate: <b>{win_rate:.2f}%</b>\n"
        f"TP: <b>{tp_count}</b> | "
        f"SL: <b>{sl_count}</b> | "
        f"TIME: <b>{time_count}</b>\n"
        f"Net PnL: <b>{net_pnl:+.2f}%</b>\n"
        f"Since: "
        f"{tehran_time(reset_time)}"
    )


# ============================================================
# SCAN SUMMARY SECTION
# ============================================================

def build_scan_summary_section():

    return (
        "<b>📊 SCAN SUMMARY</b>\n\n"
        f"Version: <b>{VERSION}</b>\n"
        f"Mode: <b>PAPER ONLY</b>\n"
        f"Time: Iran\n\n"
        f"Assets Scanned: "
        f"<b>{SCAN_STATS['assets_scanned']}/"
        f"{len(ASSETS)}</b>\n"
        f"1H OHLC OK: "
        f"<b>{SCAN_STATS['ohlc_1h_ok']}</b>\n"
        f"15M OHLC OK: "
        f"<b>{SCAN_STATS['ohlc_5m_ok']}</b>\n"
        f"1H Trendlines: "
        f"<b>{SCAN_STATS['trendlines_1h']}</b>\n"
        f"1H Breakouts: "
        f"<b>{SCAN_STATS['breakouts_1h']}</b>\n"
        f"15M Trendlines: "
        f"<b>{SCAN_STATS['trendlines_5m']}</b>\n"
        f"15M Breakouts: "
        f"<b>{SCAN_STATS['breakouts_5m']}</b>\n"
        f"RVOL Confirmed: "
        f"<b>{SCAN_STATS['rvol_confirmed']}</b>\n"
        f"New Signals: "
        f"<b>{SCAN_STATS['new_signals']}</b>\n"
        f"Errors: "
        f"<b>{SCAN_STATS['errors']}</b>"
    )


# ============================================================
# UNIFIED REPORT
# ============================================================

def send_unified_report(
    prices,
    new_signals,
    closed_trades
):

    sections = []

    # --------------------------------------------------------
    # Header
    # --------------------------------------------------------

    sections.append(
        "<b>🚨 KRAKEN TRENDLINE REPORT</b>"
    )

    sections.append(
        f"Time: "
        f"{tehran_time(iso_utc())}"
    )

    # --------------------------------------------------------
    # New Signals
    # --------------------------------------------------------

    if new_signals:

        for signal in new_signals:

            sections.append(
                build_new_signal_text(
                    signal
                )
            )

    else:

        sections.append(
            "<b>🚨 NEW SIGNAL</b>\n"
            "No new signals."
        )

    # --------------------------------------------------------
    # Closed Trades
    # --------------------------------------------------------

    sections.append(
        build_closed_trades_section(
            closed_trades
        )
    )

    # --------------------------------------------------------
    # Open Trades
    # --------------------------------------------------------

    sections.append(
        build_open_trades_section(
            prices
        )
    )

    # --------------------------------------------------------
    # Performance
    # --------------------------------------------------------

    sections.append(
        build_performance_section()
    )

    # --------------------------------------------------------
    # Scan Summary
    # --------------------------------------------------------

    sections.append(
        build_scan_summary_section()
    )

    message = (
        "\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
    ).join(
        sections
    )

    send_telegram(
        message
    )


# ============================================================
# SCAN ONE ASSET
# ============================================================

def scan_asset(
    asset,
    prices
):

    result = {
        "signal": None,
        "reason": None,
    }

    # --------------------------------------------------------
    # 1H
    # --------------------------------------------------------

    df_1h = fetch_ohlc(
        asset,
        "1h"
    )

    if df_1h is None:

        SCAN_STATS[
            "errors"
        ] += 1

        result["reason"] = (
            "1H data"
        )

        return result

    SCAN_STATS[
        "ohlc_1h_ok"
    ] += 1

    # --------------------------------------------------------
    # LONG 1H TRENDLINE
    # --------------------------------------------------------

    long_tl_1h = (
        find_valid_trendline(
            df_1h,
            "LONG"
        )
    )

    if long_tl_1h is not None:

        SCAN_STATS[
            "trendlines_1h"
        ] += 1

    long_bo_1h = None

    if long_tl_1h is not None:

        long_bo_1h = (
            find_trendline_breakout(
                df_1h,
                long_tl_1h,
                "LONG"
            )
        )

        if long_bo_1h is not None:

            SCAN_STATS[
                "breakouts_1h"
            ] += 1

    # --------------------------------------------------------
    # SHORT 1H TRENDLINE
    # --------------------------------------------------------

    short_tl_1h = (
        find_valid_trendline(
            df_1h,
            "SHORT"
        )
    )

    if short_tl_1h is not None:

        SCAN_STATS[
            "trendlines_1h"
        ] += 1

    short_bo_1h = None

    if short_tl_1h is not None:

        short_bo_1h = (
            find_trendline_breakout(
                df_1h,
                short_tl_1h,
                "SHORT"
            )
        )

        if short_bo_1h is not None:

            SCAN_STATS[
                "breakouts_1h"
            ] += 1

    # --------------------------------------------------------
    # Most recent 1H breakout
    # --------------------------------------------------------

    candidates_1h = []

    if long_bo_1h is not None:

        candidates_1h.append(
            (
                long_bo_1h["index"],
                "LONG",
                long_tl_1h,
                long_bo_1h
            )
        )

    if short_bo_1h is not None:

        candidates_1h.append(
            (
                short_bo_1h["index"],
                "SHORT",
                short_tl_1h,
                short_bo_1h
            )
        )

    if not candidates_1h:

        result["reason"] = (
            "No 1H breakout"
        )

        return result

    candidates_1h.sort(
        key=lambda x: x[0],
        reverse=True
    )

    (
        _,
        direction,
        trendline_1h,
        breakout_1h
    ) = candidates_1h[0]

    # --------------------------------------------------------
    # Existing OPEN trade check
    # --------------------------------------------------------

    if has_open_trade(
        asset,
        direction
    ):

        result["reason"] = (
            "Open trade exists"
        )

        return result

    # ========================================================
    # 15M ENTRY LAYER
    # ========================================================

    df_15m = fetch_ohlc(
        asset,
        "15m"
    )

    if df_15m is None:

        SCAN_STATS[
            "errors"
        ] += 1

        result["reason"] = (
            "15M data"
        )

        return result

    SCAN_STATS[
        "ohlc_5m_ok"
    ] += 1

    # --------------------------------------------------------
    # 15M trendline
    #
    # LONG  = highs / descending
    # SHORT = lows / ascending
    # --------------------------------------------------------

    trendline_15m = (
        find_valid_trendline(
            df_15m,
            direction
        )
    )

    if trendline_15m is None:

        result["reason"] = (
            "No 15M trendline"
        )

        return result

    SCAN_STATS[
        "trendlines_5m"
    ] += 1

    # --------------------------------------------------------
    # 15M breakout
    # --------------------------------------------------------

    bo_15m = (
        find_trendline_breakout(
            df_15m,
            trendline_15m,
            direction
        )
    )

    if bo_15m is None:

        result["reason"] = (
            "No 15M breakout"
        )

        return result

    SCAN_STATS[
        "breakouts_5m"
    ] += 1

    # --------------------------------------------------------
    # Time ordering
    # --------------------------------------------------------

    bo_1h_dt = parse_time(
        breakout_1h["time"]
    )

    bo_15m_dt = parse_time(
        bo_15m["time"]
    )

    if (
        bo_1h_dt is None
        or
        bo_15m_dt is None
    ):

        result["reason"] = (
            "Invalid breakout time"
        )

        return result

    if bo_15m_dt < bo_1h_dt:

        result["reason"] = (
            "15M breakout before "
            "1H breakout"
        )

        return result

    # --------------------------------------------------------
    # RVOL on 15M
    # --------------------------------------------------------

    rvol = calculate_rvol(
        df_15m,
        bo_15m["index"]
    )

    if rvol is None:

        result["reason"] = (
            "RVOL unavailable"
        )

        return result

    if rvol < RVOL_MIN:

        result["reason"] = (
            f"RVOL {rvol:.2f} "
            f"< {RVOL_MIN:.2f}"
        )

        return result

    SCAN_STATS[
        "rvol_confirmed"
    ] += 1

    # --------------------------------------------------------
    # TP / SL from 15M
    # --------------------------------------------------------

    levels = (
        get_confirmed_15m_levels(
            df_15m,
            bo_15m["index"],
            direction
        )
    )

    if levels is None:

        result["reason"] = (
            "Invalid TP/SL or RR"
        )

        return result

    # --------------------------------------------------------
    # Entry = closed 15M breakout candle close
    # --------------------------------------------------------

    entry = float(
        levels["entry"]
    )

    # --------------------------------------------------------
    # Current price
    # --------------------------------------------------------

    current = prices.get(
        contract_symbol(asset)
    )

    if current is None:

        current = entry

    # --------------------------------------------------------
    # Build signal
    # --------------------------------------------------------

    signal_time = iso_utc()

    signal = {
        "asset": asset,

        "direction": direction,

        "signal_time": signal_time,

        "entry": entry,

        "tp": float(
            levels["tp"]
        ),

        "sl": float(
            levels["sl"]
        ),

        "rr": float(
            levels["rr"]
        ),

        "rvol": float(
            rvol
        ),

        "trendline_1h": (
            serialize_trendline(
                direction,
                trendline_1h["p1"],
                trendline_1h["p2"]
            )
        ),

        # ----------------------------------------------------
        # Keep old DB column name for compatibility.
        # The actual trendline is 15M.
        # ----------------------------------------------------

        "trendline_5m": (
            serialize_trendline(
                direction,
                trendline_15m["p1"],
                trendline_15m["p2"]
            )
        ),

        "breakout_1h_time": (
            iso_utc(
                bo_1h_dt
            )
        ),

        # ----------------------------------------------------
        # Keep old DB column name for compatibility.
        # Actual breakout is 15M.
        # ----------------------------------------------------

        "breakout_5m_time": (
            iso_utc(
                bo_15m_dt
            )
        ),

        "tp_source": levels[
            "tp_source"
        ],

        "sl_source": levels[
            "sl_source"
        ],

        "created_at": signal_time,
    }

    # --------------------------------------------------------
    # Insert only if actually new
    # --------------------------------------------------------

    inserted, signal_id = (
        insert_signal(
            signal
        )
    )

    if not inserted:

        result["reason"] = (
            "Duplicate/open signal"
        )

        return result

    signal["id"] = signal_id

    signal["current"] = current

    SCAN_STATS[
        "new_signals"
    ] += 1

    # --------------------------------------------------------
    # Chart
    # --------------------------------------------------------

    chart_bytes = create_chart(
        asset=asset,
        df=df_15m,
        direction=direction,
        trendline=trendline_15m,
        breakout=bo_15m,
        entry=signal["entry"],
        tp=signal["tp"],
        sl=signal["sl"],
    )

    signal[
        "chart_bytes"
    ] = chart_bytes

    result["signal"] = signal

    return result


# ============================================================
# MAIN SCAN
# ============================================================

def scan():

    print("=" * 70)

    print(
        f"KRAKEN FUTURES TRENDLINE LIVE "
        f"SCANNER VERSION {VERSION}"
    )

    print(
        "PAPER ONLY | REAL_TRADING = False"
    )

    print(
        "ENTRY TIMEFRAME = 15M"
    )

    print("=" * 70)

    reset_scan_stats()

    init_db()

    # --------------------------------------------------------
    # Safety
    # --------------------------------------------------------

    if REAL_TRADING:

        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    # --------------------------------------------------------
    # Prices
    # --------------------------------------------------------

    prices = fetch_tickers()

    # --------------------------------------------------------
    # Reconcile open trades
    #
    # In this version old OPEN trades were already removed
    # on the first run. New OPEN trades are reconciled normally.
    # --------------------------------------------------------

    closed_trades = (
        reconcile_open_trades(
            prices
        )
    )

    # --------------------------------------------------------
    # New signals collected here.
    # No immediate Telegram signal message.
    # --------------------------------------------------------

    new_signals = []

    # --------------------------------------------------------
    # Scan assets
    # --------------------------------------------------------

    for asset in ASSETS:

        SCAN_STATS[
            "assets_scanned"
        ] += 1

        print(
            f"\n[SCAN] {asset}"
        )

        try:

            result = scan_asset(
                asset,
                prices
            )

            if result[
                "signal"
            ] is not None:

                signal = result[
                    "signal"
                ]

                new_signals.append(
                    signal
                )

                print(
                    f"[NEW SIGNAL] "
                    f"{asset} "
                    f"{signal['direction']} "
                    f"Entry={signal['entry']} "
                    f"TP={signal['tp']} "
                    f"SL={signal['sl']} "
                    f"RR={signal['rr']:.2f} "
                    f"RVOL={signal['rvol']:.2f}"
                )

                # --------------------------------------------
                # Maximum one new signal per scan
                # --------------------------------------------

                if (
                    SCAN_STATS[
                        "new_signals"
                    ]
                    >=
                    MAX_SIGNALS_PER_SCAN
                ):

                    print(
                        "[LIMIT] "
                        "Maximum new signals reached."
                    )

                    break

            else:

                print(
                    f"[NO SIGNAL] "
                    f"{asset}: "
                    f"{result['reason']}"
                )

        except Exception as e:

            SCAN_STATS[
                "errors"
            ] += 1

            print(
                f"[ASSET ERROR] "
                f"{asset}: {e}"
            )

            traceback.print_exc()

    # ========================================================
    # ONE UNIFIED REPORT
    # ========================================================

    send_unified_report(
        prices=prices,
        new_signals=new_signals,
        closed_trades=closed_trades
    )

    # ========================================================
    # CHARTS
    #
    # Chart is sent separately only when a new signal exists.
    # The actual textual report remains ONE unified message.
    # ========================================================

    for signal in new_signals:

        chart_bytes = signal.get(
            "chart_bytes"
        )

        if chart_bytes is None:

            continue

        chart_caption = (
            f"📈 "
            f"<b>15M CHART | "
            f"{signal['asset']} "
            f"{signal['direction']}</b>\n"
            f"Entry: "
            f"<code>{signal['entry']:.8f}</code>\n"
            f"TP: "
            f"<code>{signal['tp']:.8f}</code>\n"
            f"SL: "
            f"<code>{signal['sl']:.8f}</code>\n"
            f"RR: "
            f"<b>{signal['rr']:.2f}</b>\n"
            f"RVOL: "
            f"<b>{signal['rvol']:.2f}</b>\n"
            f"Mode: <b>PAPER ONLY</b>"
        )

        send_telegram(
            chart_caption,
            chart_bytes
        )

    # ========================================================
    # CONSOLE
    # ========================================================

    print(
        "\n" + "=" * 70
    )

    print(
        "SCAN FINISHED"
    )

    print(
        json.dumps(
            SCAN_STATS,
            indent=2
        )
    )

    print(
        f"New Signals: "
        f"{len(new_signals)}"
    )

    print(
        f"Closed Trades: "
        f"{len(closed_trades)}"
    )

    print(
        "=" * 70
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        scan()

    except Exception as e:

        print(
            "[FATAL ERROR]",
            e
        )

        traceback.print_exc()

        try:

            send_telegram(
                "<b>❌ SCANNER FATAL ERROR</b>\n\n"
                f"<code>{str(e)[:1500]}</code>"
            )

        except Exception:

            pass

        raise
