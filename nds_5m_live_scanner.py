# ============================================================
# NDS 5M LIVE SCANNER
# VERSION 2.2.1
# ============================================================
# PAPER ONLY
#
# NDS interpretation:
#   Rally -> Hook 1 -> Hook 2 -> Rally
#
# Timeframe:
#   5 Minutes
#
# Database:
#   nds_5m_v20.db
#
# IMPORTANT:
#   REAL_TRADING is permanently False.
# ============================================================

import os
import sqlite3
import time
import math
import traceback
from datetime import datetime, timezone

import requests
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "2.2.1"

REAL_TRADING = False

DB_FILE = "nds_5m_v20.db"
CHART_DIR = "charts"

TIMEFRAME = "5m"

API_URL = (
    "https://futures.kraken.com/api/charts/v1/trade/"
    "{symbol}/5m"
)

# ------------------------------------------------------------
# 40 Kraken Futures assets
# ------------------------------------------------------------

ASSETS = [
    "PF_XBTUSD",
    "PF_ETHUSD",
    "PF_SOLUSD",
    "PF_XRPUSD",
    "PF_DOGEUSD",
    "PF_ADAUSD",
    "PF_AVAXUSD",
    "PF_LINKUSD",
    "PF_DOTUSD",
    "PF_LTCUSD",
    "PF_BCHUSD",
    "PF_ATOMUSD",
    "PF_UNIUSD",
    "PF_AAVEUSD",
    "PF_NEARUSD",
    "PF_ARBUSD",
    "PF_OPUSD",
    "PF_INJUSD",
    "PF_PEPEUSD",
    "PF_SUIUSD",
    "PF_TRXUSD",
    "PF_XLMUSD",
    "PF_XMRUSD",
    "PF_ETCUSD",
    "PF_FILUSD",
    "PF_ALGOUSD",
    "PF_HBARUSD",
    "PF_ICPUSD",
    "PF_APTUSD",
    "PF_SEIUSD",
    "PF_TIAUSD",
    "PF_TONUSD",
    "PF_WIFUSD",
    "PF_BONKUSD",
    "PF_JTOUSD",
    "PF_PYTHUSD",
    "PF_LDOUSD",
    "PF_ENAUSD",
    "PF_TAOUSD",
    "PF_RUNEUSD",
]

CANDLES = 250
LOOKBACK = 200

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

MIN_TOUCHES = 3
LINE_TOLERANCE = 0.002
MIN_LINE_LENGTH = 10

MAX_SIGNAL_AGE_MIN = 15
BREAKOUT_LOOKBACK_BARS = 12

MIN_HOOK_RETRACE = 0.30
MAX_HOOK_RETRACE = 0.90

TARGET_FIB = 0.864

MAX_OPEN_TRADES = 3

REQUEST_TIMEOUT = 15

MIN_RR = 1.0

MIN_STOP_PCT = 0.15
MAX_STOP_PCT = 8.0


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("TELEGRAM_TOKEN")
    or ""
)

TELEGRAM_CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or os.getenv("TELEGRAM_CHAT")
    or ""
)


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "NDS-5M-Scanner/2.2.1"
    }
)


# ============================================================
# BASIC UTILITIES
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def fmt_dt(ts):
    if ts is None:
        return "-"

    try:
        if isinstance(
            ts,
            (
                int,
                float,
                np.integer,
                np.floating,
            ),
        ):
            dt = datetime.fromtimestamp(
                float(ts),
                tz=timezone.utc,
            )
        else:
            dt = (
                pd.to_datetime(
                    ts,
                    utc=True,
                )
                .to_pydatetime()
            )

        return dt.strftime(
            "%Y-%m-%d %H:%M UTC"
        )

    except Exception:
        return str(ts)


def pct(a, b):
    if a is None:
        return 0.0

    if b in (None, 0):
        return 0.0

    return (a / b) * 100.0


def safe_float(value):
    try:
        x = float(value)

        if math.isfinite(x):
            return x

        return None

    except Exception:
        return None


# ============================================================
# TELEGRAM SEND
# ============================================================

def send_telegram(text, photo_path=None):

    if not TELEGRAM_BOT_TOKEN:
        print("[TELEGRAM] Bot token not configured")
        return False

    if not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Chat ID not configured")
        return False

    try:

        if (
            photo_path
            and os.path.exists(photo_path)
        ):

            url = (
                "https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            )

            with open(
                photo_path,
                "rb",
            ) as photo:

                response = SESSION.post(
                    url,
                    data={
                        "chat_id":
                            TELEGRAM_CHAT_ID,
                        "caption":
                            text[:1024],
                    },
                    files={
                        "photo": photo
                    },
                    timeout=REQUEST_TIMEOUT,
                )

        else:

            url = (
                "https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            )

            response = SESSION.post(
                url,
                data={
                    "chat_id":
                        TELEGRAM_CHAT_ID,
                    "text":
                        text[:4096],
                    "parse_mode":
                        "Markdown",
                },
                timeout=REQUEST_TIMEOUT,
            )

        if response.ok:
            return True

        print(
            "[TELEGRAM ERROR]",
            response.status_code,
            response.text[:500],
        )

        return False

    except Exception as exc:

        print(
            f"[TELEGRAM ERROR] {exc}"
        )

        return False


# ============================================================
# DATABASE
# ============================================================

def table_columns(conn, table):

    rows = conn.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    return {
        row[1]
        for row in rows
    }


def ensure_column(
    conn,
    table,
    column,
    definition,
):

    columns = table_columns(
        conn,
        table,
    )

    if column not in columns:

        conn.execute(
            f"""
            ALTER TABLE {table}
            ADD COLUMN {column}
            {definition}
            """
        )

        print(
            f"[DB MIGRATION] "
            f"Added {table}.{column}"
        )


def init_db():

    print("[DB] Opening database...")

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.execute(
        "PRAGMA journal_mode=WAL"
    )

    conn.execute(
        "PRAGMA busy_timeout=10000"
    )

    # --------------------------------------------------------
    # TRADES
    # --------------------------------------------------------

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT NOT NULL,

            side TEXT NOT NULL,

            entry REAL,

            sl REAL,

            tp REAL,

            entry_time TEXT,

            exit_time TEXT,

            exit_price REAL,

            pnl_pct REAL,

            status TEXT DEFAULT 'OPEN',

            exit_reason TEXT,

            touches INTEGER,

            hook_score REAL,

            recency_score REAL,

            created_at TEXT

        )
        """
    )

    # --------------------------------------------------------
    # SIGNALS
    # --------------------------------------------------------

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT NOT NULL,

            side TEXT NOT NULL,

            entry REAL,

            sl REAL,

            tp REAL,

            signal_time TEXT,

            touches INTEGER,

            hook_score REAL,

            recency_score REAL,

            notified INTEGER DEFAULT 0,

            created_at TEXT

        )
        """
    )

    # --------------------------------------------------------
    # SCANNER RUNS
    # --------------------------------------------------------

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scanner_runs (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            run_time TEXT,

            version TEXT,

            configured INTEGER DEFAULT 0,

            fetched INTEGER DEFAULT 0,

            analyzed INTEGER DEFAULT 0,

            data_errors INTEGER DEFAULT 0,

            analysis_errors INTEGER DEFAULT 0,

            new_signals INTEGER DEFAULT 0,

            open_trades INTEGER DEFAULT 0,

            closed_trades INTEGER DEFAULT 0,

            wins INTEGER DEFAULT 0,

            losses INTEGER DEFAULT 0,

            total_pnl REAL DEFAULT 0,

            errors INTEGER DEFAULT 0

        )
        """
    )

    # ========================================================
    # AUTOMATIC MIGRATION
    #
    # IMPORTANT:
    # Old database is NOT deleted.
    # Existing trades/signals remain intact.
    # ========================================================

    print("[DB] Checking schema...")

    migrations = [

        # TRADES
        (
            "trades",
            "symbol",
            "TEXT",
        ),
        (
            "trades",
            "side",
            "TEXT",
        ),
        (
            "trades",
            "entry",
            "REAL",
        ),
        (
            "trades",
            "sl",
            "REAL",
        ),
        (
            "trades",
            "tp",
            "REAL",
        ),
        (
            "trades",
            "entry_time",
            "TEXT",
        ),
        (
            "trades",
            "exit_time",
            "TEXT",
        ),
        (
            "trades",
            "exit_price",
            "REAL",
        ),
        (
            "trades",
            "pnl_pct",
            "REAL",
        ),
        (
            "trades",
            "status",
            "TEXT DEFAULT 'OPEN'",
        ),
        (
            "trades",
            "exit_reason",
            "TEXT",
        ),
        (
            "trades",
            "touches",
            "INTEGER",
        ),
        (
            "trades",
            "hook_score",
            "REAL",
        ),
        (
            "trades",
            "recency_score",
            "REAL",
        ),
        (
            "trades",
            "created_at",
            "TEXT",
        ),

        # SIGNALS
        (
            "signals",
            "symbol",
            "TEXT",
        ),
        (
            "signals",
            "side",
            "TEXT",
        ),
        (
            "signals",
            "entry",
            "REAL",
        ),
        (
            "signals",
            "sl",
            "REAL",
        ),
        (
            "signals",
            "tp",
            "REAL",
        ),
        (
            "signals",
            "signal_time",
            "TEXT",
        ),
        (
            "signals",
            "touches",
            "INTEGER",
        ),
        (
            "signals",
            "hook_score",
            "REAL",
        ),
        (
            "signals",
            "recency_score",
            "REAL",
        ),
        (
            "signals",
            "notified",
            "INTEGER DEFAULT 0",
        ),
        (
            "signals",
            "created_at",
            "TEXT",
        ),

        # SCANNER RUNS
        (
            "scanner_runs",
            "run_time",
            "TEXT",
        ),
        (
            "scanner_runs",
            "version",
            "TEXT",
        ),
        (
            "scanner_runs",
            "configured",
            "INTEGER DEFAULT 0",
        ),
        (
            "scanner_runs",
            "fetched",
            "INTEGER DEFAULT 0",
        ),
        (
            "scanner_runs",
            "analyzed",
            "INTEGER DEFAULT 0",
        ),
        (
            "scanner_runs",
            "data_errors",
            "INTEGER DEFAULT 0",
        ),
        (
            "scanner_runs",
            "analysis_errors",
            "INTEGER DEFAULT 0",
        ),
        (
            "scanner_runs",
            "new_signals",
            "INTEGER DEFAULT 0",
        ),
        (
            "scanner_runs",
            "open_trades",
            "INTEGER DEFAULT 0",
        ),
        (
            "scanner_runs",
            "closed_trades",
            "INTEGER DEFAULT 0",
        ),
        (
            "scanner_runs",
            "wins",
            "INTEGER DEFAULT 0",
        ),
        (
            "scanner_runs",
            "losses",
            "INTEGER DEFAULT 0",
        ),
        (
            "scanner_runs",
            "total_pnl",
            "REAL DEFAULT 0",
        ),
        (
            "scanner_runs",
            "errors",
            "INTEGER DEFAULT 0",
        ),
    ]

    for (
        table,
        column,
        definition,
    ) in migrations:

        ensure_column(
            conn,
            table,
            column,
            definition,
        )

    conn.commit()

    print("[DB] Schema migration complete")
    print("[DB] Schema OK")

    return conn


# ============================================================
# KRAKEN DATA PARSING
# ============================================================

def extract_candle_list(obj):

    if isinstance(obj, list):
        return obj

    if not isinstance(obj, dict):
        return []

    possible_keys = [
        "candles",
        "data",
        "results",
        "result",
    ]

    for key in possible_keys:

        value = obj.get(key)

        if isinstance(value, list):
            return value

        if isinstance(value, dict):

            nested = extract_candle_list(
                value
            )

            if nested:
                return nested

    return []


def parse_candles(raw):

    rows = extract_candle_list(
        raw
    )

    parsed = []

    for item in rows:

        try:

            if isinstance(
                item,
                dict,
            ):

                timestamp = (
                    item.get("time")
                    or item.get("timestamp")
                    or item.get("ts")
                    or item.get(
                        "interval_begin"
                    )
                )

                open_price = item.get(
                    "open"
                )

                high_price = item.get(
                    "high"
                )

                low_price = item.get(
                    "low"
                )

                close_price = item.get(
                    "close"
                )

                volume = item.get(
                    "volume",
                    0,
                )

            elif (
                isinstance(item, (list, tuple))
                and len(item) >= 5
            ):

                timestamp = item[0]
                open_price = item[1]
                high_price = item[2]
                low_price = item[3]
                close_price = item[4]

                volume = (
                    item[5]
                    if len(item) > 5
                    else 0
                )

            else:
                continue

            timestamp = safe_float(
                timestamp
            )

            open_price = safe_float(
                open_price
            )

            high_price = safe_float(
                high_price
            )

            low_price = safe_float(
                low_price
            )

            close_price = safe_float(
                close_price
            )

            volume = (
                safe_float(volume)
                or 0.0
            )

            if None in (
                timestamp,
                open_price,
                high_price,
                low_price,
                close_price,
            ):
                continue

            # Protect against millisecond timestamps.
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0

            parsed.append(
                [
                    timestamp,
                    open_price,
                    high_price,
                    low_price,
                    close_price,
                    volume,
                ]
            )

        except Exception:
            continue

    if not parsed:
        return pd.DataFrame()

    df = pd.DataFrame(
        parsed,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ],
    )

    df = (
        df
        .drop_duplicates(
            "timestamp"
        )
        .sort_values(
            "timestamp"
        )
        .reset_index(
            drop=True
        )
    )

    # --------------------------------------------------------
    # Remove currently forming 5M candle.
    # --------------------------------------------------------

    current_time = time.time()

    df = df[
        df["timestamp"] + 300
        <= current_time + 5
    ]

    return (
        df
        .tail(CANDLES)
        .reset_index(drop=True)
    )


def fetch_data(symbol):

    url = API_URL.format(
        symbol=symbol
    )

    response = SESSION.get(
        url,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    raw = response.json()

    df = parse_candles(raw)

    if len(df) < 30:
        raise ValueError(
            f"only {len(df)} closed candles"
        )

    return df


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(df):

    highs = []
    lows = []

    high_values = (
        df["high"].to_numpy()
    )

    low_values = (
        df["low"].to_numpy()
    )

    n = len(df)

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT,
    ):

        left_highs = (
            high_values[
                i - PIVOT_LEFT:i
            ]
        )

        right_highs = (
            high_values[
                i + 1:
                i + PIVOT_RIGHT + 1
            ]
        )

        left_lows = (
            low_values[
                i - PIVOT_LEFT:i
            ]
        )

        right_lows = (
            low_values[
                i + 1:
                i + PIVOT_RIGHT + 1
            ]
        )

        if (
            high_values[i]
            > np.max(left_highs)
            and
            high_values[i]
            >= np.max(right_highs)
        ):
            highs.append(i)

        if (
            low_values[i]
            < np.min(left_lows)
            and
            low_values[i]
            <= np.min(right_lows)
        ):
            lows.append(i)

    return highs, lows


# ============================================================
# TRENDLINE UTILITIES
# ============================================================

def line_value(
    p1_idx,
    p1_price,
    p2_idx,
    p2_price,
    idx,
):

    if p2_idx == p1_idx:
        return p2_price

    slope = (
        p2_price - p1_price
    ) / (
        p2_idx - p1_idx
    )

    return (
        p1_price
        + slope * (
            idx - p1_idx
        )
    )


def touch_points(
    df,
    pivots,
    p1,
    p2,
    kind,
):

    if p2 <= p1:
        return []

    column = (
        "high"
        if kind == "resistance"
        else "low"
    )

    price1 = float(
        df.iloc[p1][column]
    )

    price2 = float(
        df.iloc[p2][column]
    )

    points = []

    for index in pivots:

        if index < p1:
            continue

        if index > p2:
            continue

        line = line_value(
            p1,
            price1,
            p2,
            price2,
            index,
        )

        price = float(
            df.iloc[index][column]
        )

        distance = (
            abs(price - line)
            /
            max(
                abs(line),
                1e-12,
            )
        )

        if distance <= LINE_TOLERANCE:
            points.append(index)

    return points


def build_trendline_candidates(
    df,
    pivot_indices,
    kind,
):

    candidates = []

    if len(pivot_indices) < 2:
        return candidates

    start = max(
        0,
        len(df) - LOOKBACK,
    )

    pivots = [
        x
        for x in pivot_indices
        if x >= start
    ]

    for a in range(
        len(pivots) - 1
    ):

        for b in range(
            a + 1,
            len(pivots),
        ):

            p1 = pivots[a]
            p2 = pivots[b]

            if (
                p2 - p1
                < MIN_LINE_LENGTH
            ):
                continue

            column = (
                "high"
                if kind == "resistance"
                else "low"
            )

            y1 = float(
                df.iloc[p1][column]
            )

            y2 = float(
                df.iloc[p2][column]
            )

            # LONG:
            # descending resistance
            if (
                kind == "resistance"
                and not y2 < y1
            ):
                continue

            # SHORT:
            # ascending support
            if (
                kind == "support"
                and not y2 > y1
            ):
                continue

            touches = touch_points(
                df,
                pivots,
                p1,
                p2,
                kind,
            )

            if (
                len(touches)
                < MIN_TOUCHES
            ):
                continue

            candidates.append(
                {
                    "p1": p1,
                    "p2": p2,
                    "price1": y1,
                    "price2": y2,
                    "touches": touches,
                    "kind": kind,
                }
            )

    candidates.sort(
        key=lambda x: (
            len(x["touches"]),
            x["p2"],
        ),
        reverse=True,
    )

    return candidates


# ============================================================
# NDS HOOK STRUCTURE
# ============================================================

def retracement_ratio(
    rally_start,
    rally_end,
    hook_end,
    side,
):

    move = abs(
        rally_end - rally_start
    )

    if move <= 0:
        return None

    if side == "LONG":
        retrace = (
            rally_end - hook_end
        )
    else:
        retrace = (
            hook_end - rally_end
        )

    return (
        abs(retrace)
        / move
    )


def price_time_symmetry(
    hook1,
    hook2,
):

    price1 = abs(
        hook1["p2_price"]
        -
        hook1["p1_price"]
    )

    price2 = abs(
        hook2["p2_price"]
        -
        hook2["p1_price"]
    )

    time1 = max(
        1,
        hook1["p2_idx"]
        -
        hook1["p1_idx"],
    )

    time2 = max(
        1,
        hook2["p2_idx"]
        -
        hook2["p1_idx"],
    )

    price_ratio = (
        min(price1, price2)
        /
        max(
            price1,
            price2,
            1e-12,
        )
    )

    time_ratio = (
        min(time1, time2)
        /
        max(time1, time2)
    )

    return (
        price_ratio
        +
        time_ratio
    ) / 2.0


def hook_quality(
    rally_start,
    rally_end,
    hook1,
    hook2,
    side,
):

    r1 = retracement_ratio(
        rally_start,
        rally_end,
        hook1["p2_price"],
        side,
    )

    r2 = retracement_ratio(
        rally_start,
        rally_end,
        hook2["p2_price"],
        side,
    )

    if r1 is None or r2 is None:
        return None

    if not (
        MIN_HOOK_RETRACE
        <= r1
        <= MAX_HOOK_RETRACE
    ):
        return None

    if not (
        MIN_HOOK_RETRACE
        <= r2
        <= MAX_HOOK_RETRACE
    ):
        return None

    # --------------------------------------------------------
    # 86.4% is a soft quality reference.
    # It is NOT treated as an exact mandatory Fib.
    # --------------------------------------------------------

    fib_distance_1 = min(
        1.0,
        abs(
            r1 - TARGET_FIB
        )
        /
        TARGET_FIB,
    )

    fib_distance_2 = min(
        1.0,
        abs(
            r2 - TARGET_FIB
        )
        /
        TARGET_FIB,
    )

    fib_score = max(
        0.0,
        1.0
        -
        (
            fib_distance_1
            +
            fib_distance_2
        )
        / 2.0,
    )

    symmetry = (
        price_time_symmetry(
            hook1,
            hook2,
        )
    )

    score = (
        50.0
        +
        25.0 * fib_score
        +
        25.0 * symmetry
    )

    return {
        "score": float(score),
        "retrace1": float(r1),
        "retrace2": float(r2),
        "symmetry": float(symmetry),
    }


def find_hook_pair(
    df,
    highs,
    lows,
    side,
):

    n = len(df)

    start_index = max(
        0,
        n - LOOKBACK,
    )

    # --------------------------------------------------------
    # LONG
    #
    # Rally:
    #   Low -> High
    #
    # Hook 1:
    #   Low -> High
    #
    # Hook 2:
    #   Low -> High
    # --------------------------------------------------------

    if side == "LONG":

        highs_set = set(highs)
        lows_set = set(lows)

        for i in range(
            start_index,
            max(
                start_index,
                n - 7,
            ),
        ):

            if i not in lows_set:
                continue

            rally_high_candidates = [
                x
                for x in highs
                if x > i
            ]

            if not rally_high_candidates:
                continue

            rally_end = (
                rally_high_candidates[0]
            )

            if rally_end >= n - 6:
                continue

            hook1_low = [
                x
                for x in lows
                if (
                    rally_end < x
                    <= rally_end + 4
                )
            ]

            if not hook1_low:
                continue

            h1_p1 = hook1_low[0]

            hook1_high = [
                x
                for x in highs
                if (
                    h1_p1 < x
                    <= h1_p1 + 3
                )
            ]

            if not hook1_high:
                continue

            h1_p2 = hook1_high[0]

            hook2_low = [
                x
                for x in lows
                if (
                    h1_p2 < x
                    <= h1_p2 + 3
                )
            ]

            if not hook2_low:
                continue

            h2_p1 = hook2_low[0]

            hook2_high = [
                x
                for x in highs
                if (
                    h2_p1 < x
                    <= h2_p1 + 3
                )
            ]

            if not hook2_high:
                continue

            h2_p2 = hook2_high[0]

            hook1 = {
                "p1_idx": h1_p1,
                "p1_price": float(
                    df.iloc[h1_p1]["low"]
                ),
                "p2_idx": h1_p2,
                "p2_price": float(
                    df.iloc[h1_p2]["high"]
                ),
            }

            hook2 = {
                "p1_idx": h2_p1,
                "p1_price": float(
                    df.iloc[h2_p1]["low"]
                ),
                "p2_idx": h2_p2,
                "p2_price": float(
                    df.iloc[h2_p2]["high"]
                ),
            }

            quality = hook_quality(
                float(
                    df.iloc[i]["low"]
                ),
                float(
                    df.iloc[rally_end]["high"]
                ),
                hook1,
                hook2,
                side,
            )

            if not quality:
                continue

            return {
                "side": side,
                "rally_start": i,
                "rally_end": rally_end,
                "hook1": hook1,
                "hook2": hook2,
                **quality,
            }

    # --------------------------------------------------------
    # SHORT
    #
    # Rally:
    #   High -> Low
    #
    # Hook 1:
    #   High -> Low
    #
    # Hook 2:
    #   High -> Low
    # --------------------------------------------------------

    else:

        highs_set = set(highs)
        lows_set = set(lows)

        for i in range(
            start_index,
            max(
                start_index,
                n - 7,
            ),
        ):

            if i not in highs_set:
                continue

            rally_low_candidates = [
                x
                for x in lows
                if x > i
            ]

            if not rally_low_candidates:
                continue

            rally_end = (
                rally_low_candidates[0]
            )

            if rally_end >= n - 6:
                continue

            hook1_high = [
                x
                for x in highs
                if (
                    rally_end < x
                    <= rally_end + 4
                )
            ]

            if not hook1_high:
                continue

            h1_p1 = hook1_high[0]

            hook1_low = [
                x
                for x in lows
                if (
                    h1_p1 < x
                    <= h1_p1 + 3
                )
            ]

            if not hook1_low:
                continue

            h1_p2 = hook1_low[0]

            hook2_high = [
                x
                for x in highs
                if (
                    h1_p2 < x
                    <= h1_p2 + 3
                )
            ]

            if not hook2_high:
                continue

            h2_p1 = hook2_high[0]

            hook2_low = [
                x
                for x in lows
                if (
                    h2_p1 < x
                    <= h2_p1 + 3
                )
            ]

            if not hook2_low:
                continue

            h2_p2 = hook2_low[0]

            hook1 = {
                "p1_idx": h1_p1,
                "p1_price": float(
                    df.iloc[h1_p1]["high"]
                ),
                "p2_idx": h1_p2,
                "p2_price": float(
                    df.iloc[h1_p2]["low"]
                ),
            }

            hook2 = {
                "p1_idx": h2_p1,
                "p1_price": float(
                    df.iloc[h2_p1]["high"]
                ),
                "p2_idx": h2_p2,
                "p2_price": float(
                    df.iloc[h2_p2]["low"]
                ),
            }

            quality = hook_quality(
                float(
                    df.iloc[i]["high"]
                ),
                float(
                    df.iloc[rally_end]["low"]
                ),
                hook1,
                hook2,
                side,
            )

            if not quality:
                continue

            return {
                "side": side,
                "rally_start": i,
                "rally_end": rally_end,
                "hook1": hook1,
                "hook2": hook2,
                **quality,
            }

    return None


# ============================================================
# BREAKOUT
# ============================================================

def find_breakout(
    df,
    structure,
):

    side = structure["side"]

    hook2 = structure[
        "hook2"
    ]

    start = (
        hook2["p2_idx"]
        + 1
    )

    end = len(df) - 1

    if start > end:
        return None

    # Only inspect the latest breakout window.
    start = max(
        start,
        end
        -
        BREAKOUT_LOOKBACK_BARS
        +
        1,
    )

    level = (
        hook2["p2_price"]
    )

    if side == "LONG":

        for i in range(
            start,
            end + 1,
        ):

            close = float(
                df.iloc[i]["close"]
            )

            previous_close = (
                float(
                    df.iloc[i - 1][
                        "close"
                    ]
                )
                if i > 0
                else close
            )

            if (
                close > level
                and
                previous_close <= level
            ):

                return {
                    "index": i,
                    "entry": close,
                    "level": level,
                    "signal_time":
                        float(
                            df.iloc[i][
                                "timestamp"
                            ]
                        ),
                }

    else:

        for i in range(
            start,
            end + 1,
        ):

            close = float(
                df.iloc[i]["close"]
            )

            previous_close = (
                float(
                    df.iloc[i - 1][
                        "close"
                    ]
                )
                if i > 0
                else close
            )

            if (
                close < level
                and
                previous_close >= level
            ):

                return {
                    "index": i,
                    "entry": close,
                    "level": level,
                    "signal_time":
                        float(
                            df.iloc[i][
                                "timestamp"
                            ]
                        ),
                }

    return None


# ============================================================
# STRUCTURAL TP / SL
# ============================================================

def calculate_levels(
    df,
    structure,
    breakout,
    highs,
    lows,
):

    side = structure["side"]

    entry = float(
        breakout["entry"]
    )

    breakout_index = (
        breakout["index"]
    )

    sl = None
    tp = None

    sl_idx = None
    tp_idx = None

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if side == "LONG":

        # Nearest previous swing HIGH above entry = TP.
        for index in reversed(
            [
                x
                for x in highs
                if x < breakout_index
            ]
        ):

            price = float(
                df.iloc[index]["high"]
            )

            if price > entry:

                tp = price
                tp_idx = index
                break

        # Nearest previous swing LOW below entry = SL.
        for index in reversed(
            [
                x
                for x in lows
                if x < breakout_index
            ]
        ):

            price = float(
                df.iloc[index]["low"]
            )

            if price < entry:

                sl = price
                sl_idx = index
                break

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        # Nearest previous swing LOW below entry = TP.
        for index in reversed(
            [
                x
                for x in lows
                if x < breakout_index
            ]
        ):

            price = float(
                df.iloc[index]["low"]
            )

            if price < entry:

                tp = price
                tp_idx = index
                break

        # Nearest previous swing HIGH above entry = SL.
        for index in reversed(
            [
                x
                for x in highs
                if x < breakout_index
            ]
        ):

            price = float(
                df.iloc[index]["high"]
            )

            if price > entry:

                sl = price
                sl_idx = index
                break

    if sl is None or tp is None:
        return None

    # --------------------------------------------------------
    # Risk calculations
    # --------------------------------------------------------

    if side == "LONG":

        risk = (
            entry - sl
        )

        reward = (
            tp - entry
        )

    else:

        risk = (
            sl - entry
        )

        reward = (
            entry - tp
        )

    if risk <= 0:
        return None

    if reward <= 0:
        return None

    rr = (
        reward / risk
    )

    stop_pct = pct(
        risk,
        entry,
    )

    tp_pct = pct(
        reward,
        entry,
    )

    if stop_pct < MIN_STOP_PCT:
        return None

    if stop_pct > MAX_STOP_PCT:
        return None

    if rr < MIN_RR:
        return None

    return {
        "entry": entry,
        "sl": float(sl),
        "tp": float(tp),
        "rr": float(rr),
        "stop_pct": float(
            stop_pct
        ),
        "tp_pct": float(
            tp_pct
        ),
        "sl_idx": sl_idx,
        "tp_idx": tp_idx,
    }


# ============================================================
# FRESHNESS
# ============================================================

def signal_fresh(
    signal_time
):

    age = (
        time.time()
        -
        signal_time
    )

    return (
        age
        <=
        MAX_SIGNAL_AGE_MIN * 60
        + 10
    )


# ============================================================
# ANALYZE ASSET
# ============================================================

def analyze_asset(
    symbol,
    df,
):

    highs, lows = find_pivots(
        df
    )

    results = []
    candidates = []

    for side in (
        "LONG",
        "SHORT",
    ):

        structure = find_hook_pair(
            df,
            highs,
            lows,
            side,
        )

        if not structure:
            continue

        # Candidate exists even before breakout.
        candidates.append(
            {
                "symbol": symbol,
                "side": side,
                "df": df,
                "highs": highs,
                "lows": lows,
                "structure": structure,
                "score":
                    structure["score"],
                "recency_score": 0.0,
                "candidate_only": True,
            }
        )

        breakout = find_breakout(
            df,
            structure,
        )

        if not breakout:
            continue

        levels = calculate_levels(
            df,
            structure,
            breakout,
            highs,
            lows,
        )

        if not levels:
            continue

        result = {
            "symbol": symbol,
            "side": side,
            "df": df,
            "highs": highs,
            "lows": lows,
            "structure": structure,
            "breakout": breakout,
            "levels": levels,
            "score":
                structure["score"],
            "recency_score":
                max(
                    0.0,
                    100.0
                    -
                    (
                        time.time()
                        -
                        breakout[
                            "signal_time"
                        ]
                    )
                    / 60.0,
                ),
            "candidate_only": False,
        }

        results.append(
            result
        )

    return results, candidates


# ============================================================
# CHART
# ============================================================

def make_chart(
    result,
    filename_prefix,
):

    os.makedirs(
        CHART_DIR,
        exist_ok=True,
    )

    df = result["df"]

    structure = (
        result["structure"]
    )

    breakout = (
        result["breakout"]
    )

    levels = (
        result["levels"]
    )

    side = result["side"]

    left = max(
        0,
        len(df) - 100,
    )

    plot_df = (
        df.iloc[left:]
        .copy()
    )

    fig, ax = plt.subplots(
        figsize=(12, 7)
    )

    ax.plot(
        plot_df.index,
        plot_df["close"],
        linewidth=1.3,
        label="Close",
    )

    # --------------------------------------------------------
    # NDS nodes
    # --------------------------------------------------------

    nodes = [
        (
            "R1",
            structure[
                "rally_start"
            ],
        ),
        (
            "R2",
            structure[
                "rally_end"
            ],
        ),
        (
            "H1-P1",
            structure[
                "hook1"
            ]["p1_idx"],
        ),
        (
            "H1-P2",
            structure[
                "hook1"
            ]["p2_idx"],
        ),
        (
            "H2-P1",
            structure[
                "hook2"
            ]["p1_idx"],
        ),
        (
            "H2-P2",
            structure[
                "hook2"
            ]["p2_idx"],
        ),
    ]

    for label, index in nodes:

        if (
            left
            <= index
            <
            len(df)
        ):

            y = float(
                df.iloc[index][
                    "close"
                ]
            )

            ax.scatter(
                [index],
                [y],
                s=35,
            )

            ax.annotate(
                label,
                (index, y),
                xytext=(4, 6),
                textcoords=(
                    "offset points"
                ),
            )

    # --------------------------------------------------------
    # 86.4 reference
    # --------------------------------------------------------

    h2_p1 = (
        structure[
            "hook2"
        ]["p1_price"]
    )

    h2_p2 = (
        structure[
            "hook2"
        ]["p2_price"]
    )

    fib864 = (
        h2_p1
        +
        (
            h2_p2 - h2_p1
        )
        *
        TARGET_FIB
    )

    ax.axhline(
        h2_p2,
        linestyle="--",
        linewidth=0.9,
        label="Hook 2 trigger",
    )

    ax.axhline(
        fib864,
        linestyle=":",
        linewidth=0.9,
        label="86.4% reference",
    )

    # --------------------------------------------------------
    # Hook path
    # --------------------------------------------------------

    ax.plot(
        [
            structure[
                "hook1"
            ]["p1_idx"],
            structure[
                "hook2"
            ]["p1_idx"],
        ],
        [
            structure[
                "hook1"
            ]["p1_price"],
            structure[
                "hook2"
            ]["p1_price"],
        ],
        linewidth=1.0,
        label="Hook path",
    )

    # --------------------------------------------------------
    # Entry
    # --------------------------------------------------------

    if (
        left
        <= breakout["index"]
        <
        len(df)
    ):

        ax.scatter(
            [breakout["index"]],
            [breakout["entry"]],
            s=70,
            marker="*",
            label="ENTRY",
        )

    # --------------------------------------------------------
    # Entry / SL / TP
    # --------------------------------------------------------

    ax.axhline(
        levels["entry"],
        linewidth=1.0,
        label="Entry",
    )

    ax.axhline(
        levels["sl"],
        linestyle="--",
        linewidth=1.0,
        label="SL",
    )

    ax.axhline(
        levels["tp"],
        linestyle="--",
        linewidth=1.0,
        label="TP",
    )

    ax.set_title(
        (
            f"NDS 5M | "
            f"{result['symbol']} | "
            f"{side} | "
            f"Score "
            f"{result['score']:.1f} | "
            f"RR "
            f"{levels['rr']:.2f}"
        )
    )

    ax.set_xlabel(
        "Candle index"
    )

    ax.set_ylabel(
        "Price"
    )

    ax.grid(
        alpha=0.2
    )

    ax.legend(
        loc="best",
        fontsize=8,
    )

    path = os.path.join(
        CHART_DIR,
        (
            f"{filename_prefix}_"
            f"{result['symbol']}_"
            f"{side}.png"
        ),
    )

    fig.tight_layout()

    fig.savefig(
        path,
        dpi=130,
    )

    plt.close(fig)

    return path


# ============================================================
# SIGNAL DUPLICATE CHECK
# ============================================================

def signal_exists(
    conn,
    symbol,
    side,
    signal_time,
):

    row = conn.execute(
        """
        SELECT id
        FROM signals
        WHERE
            symbol = ?
            AND side = ?
            AND ABS(
                CAST(
                    strftime(
                        '%s',
                        signal_time
                    )
                    AS INTEGER
                )
                -
                CAST(
                    strftime(
                        '%s',
                        ?
                    )
                    AS INTEGER
                )
            ) < 300
        LIMIT 1
        """,
        (
            symbol,
            side,
            fmt_dt(
                signal_time
            ),
        ),
    ).fetchone()

    return row is not None


# ============================================================
# INSERT SIGNAL
# ============================================================

def insert_signal(
    conn,
    result,
):

    structure = (
        result["structure"]
    )

    breakout = (
        result["breakout"]
    )

    levels = (
        result["levels"]
    )

    signal_time = fmt_dt(
        breakout[
            "signal_time"
        ]
    )

    if signal_exists(
        conn,
        result["symbol"],
        result["side"],
        breakout[
            "signal_time"
        ],
    ):
        return None

    cursor = conn.execute(
        """
        INSERT INTO signals (

            symbol,
            side,
            entry,
            sl,
            tp,
            signal_time,
            touches,
            hook_score,
            recency_score,
            notified,
            created_at

        )
        VALUES (
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, 0, ?
        )
        """,
        (
            result["symbol"],
            result["side"],
            levels["entry"],
            levels["sl"],
            levels["tp"],
            signal_time,
            MIN_TOUCHES,
            result["score"],
            result[
                "recency_score"
            ],
            fmt_dt(
                time.time()
            ),
        ),
    )

    conn.commit()

    return cursor.lastrowid


# ============================================================
# OPEN TRADE COUNT
# ============================================================

def open_trade_count(
    conn
):

    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'OPEN'
        """
    ).fetchone()

    return int(
        row[0] or 0
    )


# ============================================================
# INSERT PAPER TRADE
# ============================================================

def insert_trade(
    conn,
    result,
):

    if (
        open_trade_count(conn)
        >= MAX_OPEN_TRADES
    ):
        return None

    breakout = (
        result["breakout"]
    )

    levels = (
        result["levels"]
    )

    cursor = conn.execute(
        """
        INSERT INTO trades (

            symbol,
            side,
            entry,
            sl,
            tp,
            entry_time,
            status,
            touches,
            hook_score,
            recency_score,
            created_at

        )
        VALUES (
            ?, ?, ?, ?, ?, ?,
            'OPEN', ?, ?, ?, ?
        )
        """,
        (
            result["symbol"],
            result["side"],
            levels["entry"],
            levels["sl"],
            levels["tp"],
            fmt_dt(
                breakout[
                    "signal_time"
                ]
            ),
            MIN_TOUCHES,
            result["score"],
            result[
                "recency_score"
            ],
            fmt_dt(
                time.time()
            ),
        ),
    )

    conn.commit()

    return cursor.lastrowid


# ============================================================
# PNL
# ============================================================

def pnl_for_trade(
    side,
    entry,
    current,
):

    if side == "LONG":

        return pct(
            current - entry,
            entry,
        )

    return pct(
        entry - current,
        entry,
    )


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    conn,
    trade_id,
    current,
    reason,
):

    row = conn.execute(
        """
        SELECT
            side,
            entry
        FROM trades
        WHERE id = ?
        """,
        (trade_id,),
    ).fetchone()

    if not row:
        return

    side = row[0]
    entry = float(row[1])

    pnl = pnl_for_trade(
        side,
        entry,
        float(current),
    )

    conn.execute(
        """
        UPDATE trades
        SET
            status = 'CLOSED',
            exit_time = ?,
            exit_price = ?,
            pnl_pct = ?,
            exit_reason = ?
        WHERE id = ?
        """,
        (
            fmt_dt(
                time.time()
            ),
            current,
            pnl,
            reason,
            trade_id,
        ),
    )

    conn.commit()

    sign = (
        "+"
        if pnl >= 0
        else ""
    )

    emoji = (
        "🟢"
        if side == "LONG"
        else "🔴"
    )

    message = (
        f"🔔 *NDS 5M CLOSED*\n\n"
        f"{emoji} {side}\n"
        f"Trade ID: `{trade_id}`\n"
        f"Exit: `{current:.8g}`\n"
        f"P/L: *{sign}{pnl:.2f}%*\n"
        f"Reason: `{reason}`\n\n"
        f"🧪 PAPER ONLY"
    )

    send_telegram(
        message
    )


# ============================================================
# MONITOR OPEN TRADES
# ============================================================

def monitor_open_trades(
    conn,
    data_map,
):

    rows = conn.execute(
        """
        SELECT
            id,
            symbol,
            side,
            entry,
            sl,
            tp,
            entry_time
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY id
        """
    ).fetchall()

    for row in rows:

        (
            trade_id,
            symbol,
            side,
            entry,
            sl,
            tp,
            entry_time,
        ) = row

        df = data_map.get(
            symbol
        )

        if (
            df is None
            or df.empty
        ):
            continue

        current = float(
            df.iloc[-1]["close"]
        )

        entry = float(entry)
        sl = float(sl)
        tp = float(tp)

        reason = None

        if side == "LONG":

            if current <= sl:
                reason = "SL"

            elif current >= tp:
                reason = "TP"

        else:

            if current >= sl:
                reason = "SL"

            elif current <= tp:
                reason = "TP"

        if reason:

            close_trade(
                conn,
                trade_id,
                current,
                reason,
            )


# ============================================================
# STATISTICS
# ============================================================

def stats(conn):

    row = conn.execute(
        """
        SELECT
            COUNT(*),

            COALESCE(
                SUM(
                    CASE
                        WHEN pnl_pct > 0
                        THEN 1
                        ELSE 0
                    END
                ),
                0
            ),

            COALESCE(
                SUM(
                    CASE
                        WHEN pnl_pct <= 0
                        THEN 1
                        ELSE 0
                    END
                ),
                0
            ),

            COALESCE(
                SUM(pnl_pct),
                0
            )

        FROM trades
        WHERE status = 'CLOSED'
        """
    ).fetchone()

    return {
        "closed":
            int(row[0] or 0),

        "wins":
            int(row[1] or 0),

        "losses":
            int(row[2] or 0),

        "pnl":
            float(row[3] or 0),

        "open":
            open_trade_count(conn),
    }


# ============================================================
# SAVE SCANNER RUN
# ============================================================

def save_run(
    conn,
    counters,
    new_signals,
    statistics,
):

    total_errors = (
        counters["data_errors"]
        +
        counters["analysis_errors"]
    )

    conn.execute(
        """
        INSERT INTO scanner_runs (

            run_time,
            version,

            configured,
            fetched,
            analyzed,

            data_errors,
            analysis_errors,

            new_signals,
            open_trades,

            closed_trades,
            wins,
            losses,

            total_pnl,
            errors

        )
        VALUES (
            ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?
        )
        """,
        (
            fmt_dt(
                time.time()
            ),

            VERSION,

            counters[
                "configured"
            ],

            counters[
                "fetched"
            ],

            counters[
                "analyzed"
            ],

            counters[
                "data_errors"
            ],

            counters[
                "analysis_errors"
            ],

            new_signals,

            statistics[
                "open"
            ],

            statistics[
                "closed"
            ],

            statistics[
                "wins"
            ],

            statistics[
                "losses"
            ],

            statistics[
                "pnl"
            ],

            total_errors,
        ),
    )

    conn.commit()


# ============================================================
# BEST CANDIDATE MESSAGE
# ============================================================

def candidate_message(
    candidate
):

    if not candidate:

        return (
            "⭐ *Best Candidate:* NONE"
        )

    structure = (
        candidate["structure"]
    )

    side = candidate["side"]

    emoji = (
        "🟢"
        if side == "LONG"
        else "🔴"
    )

    return (
        f"⭐ *Best Candidate*\n"
        f"{emoji} `{candidate['symbol']}` "
        f"{side}\n"
        f"Hook score: "
        f"`{candidate['score']:.1f}`\n"
        f"Hook 1 retrace: "
        f"`{structure['retrace1']*100:.1f}%`\n"
        f"Hook 2 retrace: "
        f"`{structure['retrace2']*100:.1f}%`\n"
        f"Symmetry: "
        f"`{structure['symmetry']*100:.1f}%`\n"
        f"⚠️ Candidate only, "
        f"no trade"
    )


# ============================================================
# SEND NEW SIGNAL
# ============================================================

def send_new_signal(
    conn,
    result,
):

    signal_id = insert_signal(
        conn,
        result,
    )

    if signal_id is None:
        return False

    trade_id = insert_trade(
        conn,
        result,
    )

    levels = (
        result["levels"]
    )

    side = result["side"]

    emoji = (
        "🟢"
        if side == "LONG"
        else "🔴"
    )

    chart = make_chart(
        result,
        f"signal_{signal_id}",
    )

    trade_text = (
        str(trade_id)
        if trade_id
        else "NOT OPENED"
    )

    message = (
        f"{emoji} *NDS 5M NEW SIGNAL*\n\n"

        f"`{result['symbol']}` "
        f"*{side}*\n"

        f"Entry: "
        f"`{levels['entry']:.8g}`\n"

        f"SL: "
        f"`{levels['sl']:.8g}`\n"

        f"TP: "
        f"`{levels['tp']:.8g}`\n"

        f"RR: "
        f"`{levels['rr']:.2f}`\n"

        f"Stop: "
        f"`{levels['stop_pct']:.2f}%`\n"

        f"TP distance: "
        f"`{levels['tp_pct']:.2f}%`\n"

        f"Hook score: "
        f"`{result['score']:.1f}`\n"

        f"86.4 ref: "
        f"`86.4%`\n"

        f"Touches: "
        f"`{MIN_TOUCHES}`\n"

        f"Signal: "
        f"`{fmt_dt(result['breakout']['signal_time'])}`\n\n"

        f"Trade ID: "
        f"`{trade_text}`\n\n"

        f"🧪 *PAPER ONLY*"
    )

    send_telegram(
        message,
        chart,
    )

    conn.execute(
        """
        UPDATE signals
        SET notified = 1
        WHERE id = ?
        """,
        (signal_id,),
    )

    conn.commit()

    return True


# ============================================================
# MAIN
# ============================================================

def main():

    # Absolute safety.
    if REAL_TRADING:

        raise RuntimeError(
            "REAL_TRADING must remain False"
        )

    os.makedirs(
        CHART_DIR,
        exist_ok=True,
    )

    conn = init_db()

    # --------------------------------------------------------
    # IMPORTANT COUNTERS
    # --------------------------------------------------------

    counters = {

        "configured":
            len(ASSETS),

        "fetched":
            0,

        "analyzed":
            0,

        "data_errors":
            0,

        "analysis_errors":
            0,
    }

    data_map = {}

    fresh_results = []

    all_candidates = []

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    print()
    print("=" * 70)

    print(
        f"NDS 5M LIVE SCANNER "
        f"v{VERSION}"
    )

    print(
        "PAPER ONLY"
    )

    print(
        f"Assets configured: "
        f"{len(ASSETS)}"
    )

    print("=" * 70)
    print()

    # ========================================================
    # SCAN ALL 40 ASSETS
    # ========================================================

    for number, symbol in enumerate(
        ASSETS,
        1,
    ):

        print(
            f"[SCAN {number}/{len(ASSETS)}] "
            f"{symbol}"
        )

        # ----------------------------------------------------
        # DATA
        # ----------------------------------------------------

        try:

            df = fetch_data(
                symbol
            )

            counters[
                "fetched"
            ] += 1

            data_map[
                symbol
            ] = df

            print(
                f"[DATA OK] "
                f"{symbol}: "
                f"{len(df)} "
                f"closed candles"
            )

        except Exception as exc:

            counters[
                "data_errors"
            ] += 1

            print(
                f"[DATA ERROR] "
                f"{symbol}: "
                f"{exc}"
            )

            continue

        # ----------------------------------------------------
        # ANALYSIS
        # ----------------------------------------------------

        try:

            results, candidates = (
                analyze_asset(
                    symbol,
                    df,
                )
            )

            counters[
                "analyzed"
            ] += 1

            for candidate in candidates:

                all_candidates.append(
                    candidate
                )

            for result in results:

                signal_time = (
                    result[
                        "breakout"
                    ][
                        "signal_time"
                    ]
                )

                age_minutes = (
                    time.time()
                    -
                    signal_time
                ) / 60.0

                if signal_fresh(
                    signal_time
                ):

                    fresh_results.append(
                        result
                    )

                    print(
                        f"[SIGNAL] "
                        f"{symbol} "
                        f"{result['side']} "
                        f"score="
                        f"{result['score']:.1f} "
                        f"age="
                        f"{age_minutes:.1f}m"
                    )

                else:

                    print(
                        f"[STALE] "
                        f"{symbol} "
                        f"{result['side']} "
                        f"age="
                        f"{age_minutes:.1f}m"
                    )

        except Exception as exc:

            counters[
                "analysis_errors"
            ] += 1

            print(
                f"[ANALYSIS ERROR] "
                f"{symbol}: "
                f"{exc}"
            )

    # ========================================================
    # MONITOR EXISTING PAPER TRADES
    # ========================================================

    print()
    print(
        "[TRADES] Monitoring "
        "open trades..."
    )

    monitor_open_trades(
        conn,
        data_map,
    )

    # ========================================================
    # NEW SIGNALS
    # ========================================================

    fresh_results.sort(
        key=lambda item: (
            item["score"],
            item[
                "recency_score"
            ],
        ),
        reverse=True,
    )

    new_count = 0

    for result in fresh_results:

        current_open = (
            open_trade_count(
                conn
            )
        )

        if (
            current_open
            >= MAX_OPEN_TRADES
        ):

            print(
                "[LIMIT] "
                "MAX_OPEN_TRADES reached"
            )

            break

        try:

            if send_new_signal(
                conn,
                result,
            ):

                new_count += 1

        except Exception as exc:

            counters[
                "analysis_errors"
            ] += 1

            print(
                f"[SIGNAL ERROR] "
                f"{result['symbol']}: "
                f"{exc}"
            )

    # ========================================================
    # BEST CANDIDATE
    # ========================================================

    best_candidate = None

    if all_candidates:

        best_candidate = max(
            all_candidates,
            key=lambda item: (
                item.get(
                    "score",
                    0,
                ),
                item.get(
                    "recency_score",
                    0,
                ),
            ),
        )

    # ========================================================
    # FINAL STATISTICS
    # ========================================================

    statistics = stats(
        conn
    )

    # Save scanner run.
    save_run(
        conn,
        counters,
        new_count,
        statistics,
    )

    win_rate = (
        statistics["wins"]
        /
        statistics["closed"]
        *
        100.0
        if statistics["closed"]
        else 0.0
    )

    # ========================================================
    # REPORT
    # ========================================================

    report = (
        f"📊 *NDS 5M REPORT*\n\n"

        f"Assets configured: "
        f"*{counters['configured']}*\n"

        f"Assets fetched: "
        f"*{counters['fetched']}*\n"

        f"Assets analyzed: "
        f"*{counters['analyzed']}*\n"

        f"Data errors: "
        f"*{counters['data_errors']}*\n"

        f"Analysis errors: "
        f"*{counters['analysis_errors']}*\n\n"

        f"New signals: "
        f"*{new_count}*\n"

        f"Open trades: "
        f"*{statistics['open']}*\n\n"

        f"Closed: "
        f"{statistics['closed']}\n"

        f"Wins: "
        f"{statistics['wins']}\n"

        f"Losses: "
        f"{statistics['losses']}\n"

        f"Win rate: "
        f"{win_rate:.2f}%\n"

        f"Total P/L: "
        f"*{statistics['pnl']:+.2f}%*\n\n"

        f"{candidate_message(best_candidate)}\n\n"

        f"🧪 *PAPER ONLY*\n"

        f"Version: "
        f"{VERSION}"
    )

    # --------------------------------------------------------
    # Console
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print(
        report.replace(
            "*",
            "",
        )
    )
    print("=" * 70)

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    send_telegram(
        report
    )

    conn.close()


# ============================================================
# FATAL ERROR HANDLER
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as exc:

        print()
        print(
            "[FATAL ERROR]"
        )

        traceback.print_exc()

        send_telegram(
            (
                "🚨 *NDS 5M SCANNER ERROR*\n\n"
                f"`{str(exc)[:700]}`\n\n"
                f"Version: {VERSION}"
            )
        )

        raise
