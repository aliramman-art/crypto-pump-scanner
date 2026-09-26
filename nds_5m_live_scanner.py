# ============================================================
# NDS M30 -> M1 LIVE SCANNER
# VERSION 1.0.1
# ============================================================
#
# PAPER ONLY
#
# NDS STRUCTURE
#
# 30M:
#   Detect higher-timeframe NDS structure
#
# 1M:
#   123 Flag
#      ->
#   Hook 1
#      ->
#   Hook 2
#      ->
#   Rally
#      ->
#   Entry
#
# NO LIVE ORDERS
#
# 1.0.1 FIXES:
#   - Robust Kraken candle fetching
#   - Retry on incomplete/empty candle response
#   - Insufficient candle data is SKIPPED, not counted as ERROR
#   - M30/M1 data validation
#   - Separate SKIPPED count in report
#   - Keeps PAPER ONLY mode
# ============================================================

from __future__ import annotations

import os
import time
import math
import sqlite3
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any

import requests
import pandas as pd
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


# ============================================================
# CONFIG
# ============================================================

VERSION = "1.0.1"

PAPER_ONLY = True

DB_FILE = os.getenv(
    "NDS_DB_FILE",
    "nds_m30_m1_v10.db"
)

CHART_DIR = Path(
    os.getenv(
        "NDS_CHART_DIR",
        "nds_charts"
    )
)

CHART_DIR.mkdir(
    parents=True,
    exist_ok=True
)

KRAKEN_BASE = (
    "https://futures.kraken.com/api/charts/v1"
)

REQUEST_TIMEOUT = 20

# ------------------------------------------------------------
# Candle counts
# ------------------------------------------------------------

M30_COUNT = int(
    os.getenv(
        "NDS_M30_COUNT",
        "220"
    )
)

M1_COUNT = int(
    os.getenv(
        "NDS_M1_COUNT",
        "700"
    )
)

# Minimum candles required by strategy.
MIN_REQUIRED_CANDLES = int(
    os.getenv(
        "NDS_MIN_REQUIRED_CANDLES",
        "20"
    )
)

# Retry settings for Kraken candle endpoint.
CANDLE_FETCH_RETRIES = int(
    os.getenv(
        "NDS_CANDLE_FETCH_RETRIES",
        "3"
    )
)

CANDLE_RETRY_DELAY = float(
    os.getenv(
        "NDS_CANDLE_RETRY_DELAY",
        "1.0"
    )
)

# ------------------------------------------------------------
# Pivot settings
# ------------------------------------------------------------

PIVOT_LEFT = int(
    os.getenv(
        "NDS_PIVOT_LEFT",
        "2"
    )
)

PIVOT_RIGHT = int(
    os.getenv(
        "NDS_PIVOT_RIGHT",
        "2"
    )
)

MIN_SWING_PCT = float(
    os.getenv(
        "NDS_MIN_SWING_PCT",
        "0.0015"
    )
)

PRICE_TOL = float(
    os.getenv(
        "NDS_PRICE_TOL",
        "0.0025"
    )
)

# ------------------------------------------------------------
# NDS Hook settings
# ------------------------------------------------------------

MIN_HOOK_RANGE_PCT = float(
    os.getenv(
        "NDS_MIN_HOOK_RANGE_PCT",
        "0.003"
    )
)

NDS_864_TOL = float(
    os.getenv(
        "NDS_864_TOL",
        "0.015"
    )
)

MAX_SETUP_AGE_MIN = int(
    os.getenv(
        "NDS_MAX_SETUP_AGE_MIN",
        "8"
    )
)

SIGNAL_COOLDOWN_MIN = int(
    os.getenv(
        "NDS_SIGNAL_COOLDOWN_MIN",
        "60"
    )
)

MAX_OPEN_TRADES = int(
    os.getenv(
        "NDS_MAX_OPEN_TRADES",
        "3"
    )
)

# ------------------------------------------------------------
# Targets
# ------------------------------------------------------------

TP1_R = float(
    os.getenv(
        "NDS_TP1_R",
        "1.0"
    )
)

TP2_R = float(
    os.getenv(
        "NDS_TP2_R",
        "2.0"
    )
)

FINAL_R = float(
    os.getenv(
        "NDS_FINAL_R",
        "3.0"
    )
)

USE_864_FINAL_TARGET = (
    os.getenv(
        "NDS_USE_864_FINAL_TARGET",
        "1"
    ) == "1"
)

# ------------------------------------------------------------
# Telegram
# ------------------------------------------------------------

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

# ------------------------------------------------------------
# Kraken Futures symbols
# ------------------------------------------------------------

DEFAULT_SYMBOLS = [
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
    "PF_UNIUSD",
    "PF_ATOMUSD",
    "PF_AAVEUSD",
    "PF_FILUSD",
    "PF_INJUSD",
    "PF_ARBUSD",
    "PF_OPUSD",
    "PF_NEARUSD",
    "PF_SUIUSD",
    "PF_APTUSD",
    "PF_SEIUSD",
    "PF_TAOUSD",
    "PF_TIAUSD",
    "PF_WIFUSD",
    "PF_PEPEUSD",
    "PF_TRXUSD",
    "PF_VETUSD",
    "PF_ETCUSD",
    "PF_XLMUSD",
    "PF_ALGOUSD",
    "PF_MKRUSD",
    "PF_RUNEUSD",
    "PF_IMXUSD",
    "PF_STXUSD",
    "PF_LDOUSD",
    "PF_ENSUSD",
    "PF_GRTUSD",
    "PF_RENDERUSD",
    "PF_FLOKIUSD",
]

SYMBOLS = [
    x.strip().upper()
    for x in os.getenv(
        "KRAKEN_SYMBOLS",
        ",".join(DEFAULT_SYMBOLS)
    ).split(",")
    if x.strip()
]

# Remove SNXX if somebody puts it back.
SYMBOLS = [
    x for x in SYMBOLS
    if "SNXX" not in x
]

SLEEP_BETWEEN_SYMBOLS = float(
    os.getenv(
        "NDS_SLEEP_BETWEEN_SYMBOLS",
        "0.10"
    )
)


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "Accept": "application/json",
    "User-Agent": "NDS-M30-M1-Scanner/1.0.1"
})


# ============================================================
# CUSTOM EXCEPTIONS
# ============================================================

class CandleDataError(Exception):
    """
    Expected data-quality problem.

    This is intentionally different from a real unexpected
    exception. A symbol with insufficient candles is skipped
    instead of being counted as a scanner failure.
    """
    pass


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class Pivot:
    index: int
    time: int
    kind: str
    price: float


@dataclass
class Hook:
    direction: str

    start_index: int
    end_index: int

    start_price: float
    end_price: float

    p1: Pivot
    p2: Pivot
    p3: Pivot

    q1: Pivot
    q2: Pivot
    q3: Pivot

    completion_864: float
    score: float


@dataclass
class Setup:
    symbol: str

    direction: str
    trend: str
    trend_score: float

    flag_p1: Pivot
    flag_p2: Pivot
    flag_p3: Pivot

    hook1: Hook
    hook2: Hook

    rally_start: Pivot
    rally_end: Pivot

    entry: float
    sl: float

    tp1: float
    tp2: float
    final_tp: float

    risk: float
    rr_final: float

    setup_time: int

    reason: str


# ============================================================
# HELPERS
# ============================================================

def now_ms() -> int:
    return int(
        time.time() * 1000
    )


def utc_text(ms: Optional[int] = None) -> str:

    if ms is None:
        ms = now_ms()

    return datetime.fromtimestamp(
        ms / 1000,
        tz=timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def safe_float(value: Any) -> float:

    try:
        return float(value)

    except Exception:
        return float("nan")


def pct_distance(
    a: float,
    b: float
) -> float:

    if a == 0:
        return float("inf")

    return abs(b - a) / abs(a)


def finite(value: float) -> bool:

    return math.isfinite(
        float(value)
    )


# ============================================================
# DATABASE
# ============================================================

def db_connect():

    con = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    con.row_factory = sqlite3.Row

    return con


def init_db():

    con = db_connect()

    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS scanner_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_time INTEGER NOT NULL,
            version TEXT NOT NULL,
            assets_scanned INTEGER NOT NULL,
            new_signals INTEGER NOT NULL,
            open_trades INTEGER NOT NULL,
            errors INTEGER NOT NULL DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            trend TEXT NOT NULL,
            entry REAL NOT NULL,
            sl REAL NOT NULL,
            tp1 REAL NOT NULL,
            tp2 REAL NOT NULL,
            final_tp REAL NOT NULL,
            risk REAL NOT NULL,
            rr_final REAL NOT NULL,
            setup_time INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            reason TEXT NOT NULL,
            chart_path TEXT,
            UNIQUE(symbol, direction, setup_time)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS open_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL UNIQUE,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry REAL NOT NULL,
            sl REAL NOT NULL,
            tp1 REAL NOT NULL,
            tp2 REAL NOT NULL,
            final_tp REAL NOT NULL,
            opened_at INTEGER NOT NULL,
            tp1_hit INTEGER NOT NULL DEFAULT 0,
            tp2_hit INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(signal_id)
                REFERENCES signals(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS closed_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry REAL NOT NULL,
            exit_price REAL NOT NULL,
            pnl_pct REAL NOT NULL,
            reason TEXT NOT NULL,
            opened_at INTEGER NOT NULL,
            closed_at INTEGER NOT NULL
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_signal_symbol_direction_time
        ON signals(
            symbol,
            direction,
            setup_time
        )
    """)

    con.commit()
    con.close()


def count_open_trades() -> int:

    con = db_connect()

    value = con.execute(
        "SELECT COUNT(*) FROM open_trades"
    ).fetchone()[0]

    con.close()

    return int(value)


def recent_duplicate(
    symbol: str,
    direction: str,
    setup_time: int
) -> bool:

    cutoff = (
        setup_time
        - SIGNAL_COOLDOWN_MIN * 60 * 1000
    )

    con = db_connect()

    row = con.execute("""
        SELECT 1
        FROM signals
        WHERE symbol = ?
          AND direction = ?
          AND setup_time >= ?
        LIMIT 1
    """, (
        symbol,
        direction,
        cutoff
    )).fetchone()

    con.close()

    return row is not None


def insert_signal(
    setup: Setup,
    chart_path: str
) -> int:

    con = db_connect()

    cur = con.cursor()

    cur.execute("""
        INSERT OR IGNORE INTO signals (
            symbol,
            direction,
            trend,
            entry,
            sl,
            tp1,
            tp2,
            final_tp,
            risk,
            rr_final,
            setup_time,
            created_at,
            reason,
            chart_path
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        setup.symbol,
        setup.direction,
        setup.trend,
        setup.entry,
        setup.sl,
        setup.tp1,
        setup.tp2,
        setup.final_tp,
        setup.risk,
        setup.rr_final,
        setup.setup_time,
        now_ms(),
        setup.reason,
        chart_path
    ))

    signal_id = cur.lastrowid

    if signal_id:

        cur.execute("""
            INSERT OR IGNORE INTO open_trades (
                signal_id,
                symbol,
                direction,
                entry,
                sl,
                tp1,
                tp2,
                final_tp,
                opened_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            signal_id,
            setup.symbol,
            setup.direction,
            setup.entry,
            setup.sl,
            setup.tp1,
            setup.tp2,
            setup.final_tp,
            now_ms()
        ))

    con.commit()
    con.close()

    return int(
        signal_id or 0
    )


# ============================================================
# KRAKEN DATA
# ============================================================

def fetch_candles(
    symbol: str,
    resolution: str,
    count: int
) -> pd.DataFrame:
    """
    Fetch Kraken Futures candles robustly.

    Expected data problems are converted to CandleDataError.
    The caller can then skip the symbol without counting it
    as a scanner error.

    Retries are used because Kraken can occasionally return
    a short/empty response for an otherwise valid contract.
    """

    url = (
        f"{KRAKEN_BASE}/trade/"
        f"{symbol}/{resolution}"
    )

    last_problem = ""

    attempts = max(
        1,
        CANDLE_FETCH_RETRIES
    )

    for attempt in range(
        1,
        attempts + 1
    ):

        try:

            response = SESSION.get(
                url,
                params={"count": count},
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            data = response.json()

            candles = data.get(
                "candles",
                []
            )

            if not candles:

                last_problem = (
                    f"No candles returned: "
                    f"{symbol} {resolution}"
                )

            else:

                rows = []

                for c in candles:

                    try:

                        rows.append({
                            "time": int(
                                c["time"]
                            ),
                            "open": safe_float(
                                c["open"]
                            ),
                            "high": safe_float(
                                c["high"]
                            ),
                            "low": safe_float(
                                c["low"]
                            ),
                            "close": safe_float(
                                c["close"]
                            ),
                            "volume": safe_float(
                                c.get(
                                    "volume",
                                    0
                                )
                            )
                        })

                    except (
                        KeyError,
                        TypeError,
                        ValueError
                    ):

                        continue

                if rows:

                    df = pd.DataFrame(
                        rows
                    )

                    df = (
                        df
                        .sort_values("time")
                        .drop_duplicates(
                            "time"
                        )
                        .reset_index(
                            drop=True
                        )
                    )

                    # ------------------------------------------------
                    # Never use currently forming candle.
                    # ------------------------------------------------

                    if len(df) > 3:

                        if resolution == "1m":

                            tf_ms = (
                                60 * 1000
                            )

                        elif resolution == "30m":

                            tf_ms = (
                                30 * 60 * 1000
                            )

                        else:

                            tf_ms = (
                                60 * 1000
                            )

                        current_bucket = (
                            now_ms() // tf_ms
                        ) * tf_ms

                        df = df[
                            df["time"]
                            <
                            current_bucket
                        ].copy()

                    # ------------------------------------------------
                    # Remove invalid numeric rows.
                    # ------------------------------------------------

                    numeric_columns = [
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume"
                    ]

                    for column in numeric_columns:

                        df[column] = pd.to_numeric(
                            df[column],
                            errors="coerce"
                        )

                    df = df.dropna(
                        subset=[
                            "time",
                            "open",
                            "high",
                            "low",
                            "close"
                        ]
                    )

                    df = df.reset_index(
                        drop=True
                    )

                    if len(df) >= MIN_REQUIRED_CANDLES:

                        return df

                    last_problem = (
                        f"Not enough candles: "
                        f"{symbol} {resolution} "
                        f"({len(df)}/"
                        f"{MIN_REQUIRED_CANDLES})"
                    )

                else:

                    last_problem = (
                        f"Invalid candle data: "
                        f"{symbol} {resolution}"
                    )

        except requests.RequestException as exc:

            last_problem = (
                f"HTTP error: "
                f"{symbol} {resolution}: "
                f"{exc}"
            )

        except ValueError as exc:

            last_problem = (
                f"Invalid response: "
                f"{symbol} {resolution}: "
                f"{exc}"
            )

        except Exception as exc:

            # Unexpected programming/data issue.
            # Retry first, then propagate as real error.
            last_problem = (
                f"Unexpected candle error: "
                f"{symbol} {resolution}: "
                f"{exc}"
            )

        if attempt < attempts:

            time.sleep(
                CANDLE_RETRY_DELAY
                *
                attempt
            )

    # ------------------------------------------------------------
    # After all retries, classify a data problem as expected.
    # ------------------------------------------------------------

    raise CandleDataError(
        last_problem
    )


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(
    df: pd.DataFrame,
    left: int = PIVOT_LEFT,
    right: int = PIVOT_RIGHT,
    min_swing_pct: float = MIN_SWING_PCT
) -> List[Pivot]:

    highs = df["high"].to_numpy(
        dtype=float
    )

    lows = df["low"].to_numpy(
        dtype=float
    )

    times = df["time"].to_numpy(
        dtype=np.int64
    )

    raw = []

    for i in range(
        left,
        len(df) - right
    ):

        h = highs[i]
        l = lows[i]

        left_h = highs[
            i-left:i
        ]

        right_h = highs[
            i+1:i+right+1
        ]

        left_l = lows[
            i-left:i
        ]

        right_l = lows[
            i+1:i+right+1
        ]

        is_high = (
            h >= np.max(left_h)
            and
            h > np.max(right_h)
        )

        is_low = (
            l <= np.min(left_l)
            and
            l < np.min(right_l)
        )

        if is_high:

            raw.append(
                Pivot(
                    i,
                    int(times[i]),
                    "H",
                    h
                )
            )

        if is_low:

            raw.append(
                Pivot(
                    i,
                    int(times[i]),
                    "L",
                    l
                )
            )

    raw.sort(
        key=lambda x: x.index
    )

    pivots = []

    for p in raw:

        if not pivots:

            pivots.append(p)

            continue

        last = pivots[-1]

        # Same type:
        # keep the more extreme pivot.
        if p.kind == last.kind:

            if (
                p.kind == "H"
                and p.price > last.price
            ):

                pivots[-1] = p

            elif (
                p.kind == "L"
                and p.price < last.price
            ):

                pivots[-1] = p

            continue

        # Remove tiny swings.
        if (
            pct_distance(
                last.price,
                p.price
            )
            < min_swing_pct
        ):

            continue

        pivots.append(p)

    return pivots


# ============================================================
# 30M NDS TREND
# ============================================================

def last_three(
    pivots: List[Pivot],
    kind: str
) -> List[Pivot]:

    return [
        p for p in pivots
        if p.kind == kind
    ][-3:]


def detect_m30_trend(
    df: pd.DataFrame
) -> Tuple[
    str,
    float,
    Dict[str, Pivot]
]:

    pivots = find_pivots(df)

    highs = last_three(
        pivots,
        "H"
    )

    lows = last_three(
        pivots,
        "L"
    )

    if (
        len(highs) < 3
        or
        len(lows) < 3
    ):

        return (
            "NEUTRAL",
            0.0,
            {}
        )

    h1, h2, h3 = highs
    l1, l2, l3 = lows

    bearish_highs = (
        h2.price > h1.price
        and
        h3.price > h2.price
    )

    bearish_lows = (
        l2.price < l1.price
        and
        l3.price < l2.price
    )

    bullish_highs = (
        h2.price < h1.price
        and
        h3.price < h2.price
    )

    bullish_lows = (
        l2.price > l1.price
        and
        l3.price > l2.price
    )

    if (
        bearish_highs
        and
        bearish_lows
    ):

        return (
            "DOWNTREND",
            1.0,
            {
                "N1": h1,
                "N2": h2,
                "N3": h3,
                "S1": l1,
                "S2": l2,
                "S3": l3
            }
        )

    if (
        bullish_highs
        and
        bullish_lows
    ):

        return (
            "UPTREND",
            1.0,
            {
                "N1": h1,
                "N2": h2,
                "N3": h3,
                "S1": l1,
                "S2": l2,
                "S3": l3
            }
        )

    if (
        h3.price > h2.price > h1.price
        and
        l3.price > l2.price > l1.price
    ):

        return (
            "UPTREND",
            0.70,
            {
                "N1": h1,
                "N2": h2,
                "N3": h3,
                "S1": l1,
                "S2": l2,
                "S3": l3
            }
        )

    if (
        h3.price < h2.price < h1.price
        and
        l3.price < l2.price < l1.price
    ):

        return (
            "DOWNTREND",
            0.70,
            {
                "N1": h1,
                "N2": h2,
                "N3": h3,
                "S1": l1,
                "S2": l2,
                "S3": l3
            }
        )

    return (
        "NEUTRAL",
        0.0,
        {}
    )


# ============================================================
# 123 FLAG
# ============================================================

def find_bearish_flag(
    pivots: List[Pivot],
    min_index: int
) -> Optional[
    Tuple[Pivot, Pivot, Pivot]
]:

    candidates = []

    for i in range(
        len(pivots) - 1,
        1,
        -1
    ):

        p3 = pivots[i]

        if p3.kind != "H":
            continue

        p2 = pivots[i - 1]
        p1 = pivots[i - 2]

        if (
            p2.kind != "L"
            or
            p1.kind != "H"
        ):

            continue

        if p3.index < min_index:
            continue

        if (
            p3.price
            >
            p1.price * (
                1 + PRICE_TOL
            )
        ):

            continue

        if (
            p2.price
            >=
            min(
                p1.price,
                p3.price
            )
        ):

            continue

        if (
            pct_distance(
                p1.price,
                p2.price
            )
            < MIN_SWING_PCT
        ):

            continue

        if (
            pct_distance(
                p1.price,
                p3.price
            )
            > 0.03
        ):

            continue

        candidates.append(
            (p1, p2, p3)
        )

    return (
        candidates[-1]
        if candidates
        else None
    )


def find_bullish_flag(
    pivots: List[Pivot],
    min_index: int
) -> Optional[
    Tuple[Pivot, Pivot, Pivot]
]:

    candidates = []

    for i in range(
        len(pivots) - 1,
        1,
        -1
    ):

        p3 = pivots[i]

        if p3.kind != "L":
            continue

        p2 = pivots[i - 1]
        p1 = pivots[i - 2]

        if (
            p2.kind != "H"
            or
            p1.kind != "L"
        ):

            continue

        if p3.index < min_index:
            continue

        if (
            p3.price
            <
            p1.price * (
                1 - PRICE_TOL
            )
        ):

            continue

        if (
            p2.price
            <=
            max(
                p1.price,
                p3.price
            )
        ):

            continue

        if (
            pct_distance(
                p1.price,
                p2.price
            )
            < MIN_SWING_PCT
        ):

            continue

        if (
            pct_distance(
                p1.price,
                p3.price
            )
            > 0.03
        ):

            continue

        candidates.append(
            (p1, p2, p3)
        )

    return (
        candidates[-1]
        if candidates
        else None
    )


# ============================================================
# 86.4%
# ============================================================

def hook_864(
    direction: str,
    p1: Pivot,
    q1: Pivot
) -> float:

    if direction == "SHORT":

        return (
            p1.price
            -
            0.864
            *
            (
                p1.price
                -
                q1.price
            )
        )

    return (
        p1.price
        +
        0.864
        *
        (
            q1.price
            -
            p1.price
        )
    )


# ============================================================
# HOOK DETECTOR
# ============================================================

def detect_hooks(
    pivots: List[Pivot],
    direction: str,
    end_before_index: int
) -> List[Hook]:

    hooks = []

    if direction == "SHORT":

        sequence = [
            "H",
            "L",
            "H",
            "L",
            "H",
            "L"
        ]

    else:

        sequence = [
            "L",
            "H",
            "L",
            "H",
            "L",
            "H"
        ]

    if len(pivots) < 6:
        return []

    for i in range(
        0,
        len(pivots) - 5
    ):

        six = pivots[
            i:i + 6
        ]

        if any(
            six[j].kind != sequence[j]
            for j in range(6)
        ):

            continue

        if (
            six[-1].index
            >
            end_before_index
        ):

            continue

        p1, q1, p2, q2, p3, q3 = six

        if direction == "SHORT":

            valid = (
                p2.price > p1.price
                and
                p3.price > p2.price
                and
                q2.price < q1.price
                and
                q3.price < q2.price
            )

            if not valid:
                continue

        else:

            valid = (
                p2.price < p1.price
                and
                p3.price < p2.price
                and
                q2.price > q1.price
                and
                q3.price > q2.price
            )

            if not valid:
                continue

        range_pct = pct_distance(
            p1.price,
            q3.price
        )

        if (
            range_pct
            <
            MIN_HOOK_RANGE_PCT
        ):

            continue

        completion = hook_864(
            direction,
            p1,
            q1
        )

        error = pct_distance(
            q3.price,
            completion
        )

        if error > NDS_864_TOL:
            continue

        score = max(
            0.0,
            1.0
            -
            error / NDS_864_TOL
        )

        hooks.append(
            Hook(
                direction=direction,
                start_index=p1.index,
                end_index=q3.index,
                start_price=p1.price,
                end_price=q3.price,
                p1=p1,
                p2=p2,
                p3=p3,
                q1=q1,
                q2=q2,
                q3=q3,
                completion_864=completion,
                score=score
            )
        )

    hooks.sort(
        key=lambda x: x.end_index,
        reverse=True
    )

    return hooks


# ============================================================
# TWO SEQUENTIAL HOOKS
# ============================================================

def choose_two_hooks(
    hooks: List[Hook]
) -> Optional[
    Tuple[Hook, Hook]
]:

    if len(hooks) < 2:
        return None

    for i in range(
        len(hooks) - 1
    ):

        newer = hooks[i]
        older = hooks[i + 1]

        if (
            older.end_index
            >=
            newer.start_index
        ):

            continue

        if (
            newer.end_index
            -
            older.end_index
            < 6
        ):

            continue

        return (
            older,
            newer
        )

    return None


# ============================================================
# RALLY
# ============================================================

def find_rally(
    pivots: List[Pivot],
    hook2: Hook,
    direction: str,
    max_index: int
) -> Optional[
    Tuple[Pivot, Pivot]
]:

    relevant = [
        p for p in pivots
        if (
            p.index
            >
            hook2.end_index
            and
            p.index
            <=
            max_index
        )
    ]

    if direction == "SHORT":

        lows = [
            p for p in relevant
            if p.kind == "L"
        ]

        highs = [
            p for p in relevant
            if p.kind == "H"
        ]

        if not lows or not highs:
            return None

        start = lows[0]

        ends = [
            h for h in highs
            if h.index > start.index
        ]

        if not ends:
            return None

        end = ends[-1]

        if (
            end.price
            <=
            start.price
            *
            (
                1 + MIN_SWING_PCT
            )
        ):

            return None

        return (
            start,
            end
        )

    highs = [
        p for p in relevant
        if p.kind == "H"
    ]

    lows = [
        p for p in relevant
        if p.kind == "L"
    ]

    if not highs or not lows:
        return None

    start = highs[0]

    ends = [
        l for l in lows
        if l.index > start.index
    ]

    if not ends:
        return None

    end = ends[-1]

    if (
        end.price
        >=
        start.price
        *
        (
            1 - MIN_SWING_PCT
        )
    ):

        return None

    return (
        start,
        end
    )


# ============================================================
# RR
# ============================================================

def calculate_rr(
    entry: float,
    sl: float,
    tp: float,
    direction: str
) -> float:

    risk = abs(
        entry - sl
    )

    if risk <= 0:
        return -1

    if direction == "LONG":

        reward = (
            tp - entry
        )

    else:

        reward = (
            entry - tp
        )

    return (
        reward / risk
    )


# ============================================================
# BUILD SETUP
# ============================================================

def build_setup(
    symbol: str,
    m1: pd.DataFrame,
    trend: str,
    trend_score: float
) -> Optional[Setup]:

    if trend not in (
        "UPTREND",
        "DOWNTREND"
    ):

        return None

    pivots = find_pivots(
        m1
    )

    if len(pivots) < 30:
        return None

    last_index = len(m1) - 1

    min_index = max(
        0,
        last_index
        -
        MAX_SETUP_AGE_MIN
    )

    direction = (
        "LONG"
        if trend == "UPTREND"
        else "SHORT"
    )

    if direction == "SHORT":

        flag = find_bearish_flag(
            pivots,
            min_index
        )

    else:

        flag = find_bullish_flag(
            pivots,
            min_index
        )

    if not flag:
        return None

    flag_p1, flag_p2, flag_p3 = flag

    hooks = detect_hooks(
        pivots,
        direction,
        flag_p3.index
    )

    pair = choose_two_hooks(
        hooks
    )

    if not pair:
        return None

    hook1, hook2 = pair

    if (
        hook2.end_index
        >=
        flag_p3.index
    ):

        return None

    rally = find_rally(
        pivots,
        hook2,
        direction,
        flag_p3.index
    )

    if not rally:
        return None

    rally_start, rally_end = rally

    if (
        rally_end.index
        >
        flag_p3.index
    ):

        return None

    if (
        last_index
        -
        flag_p3.index
        >
        MAX_SETUP_AGE_MIN
    ):

        return None

    current = float(
        m1.iloc[-1]["close"]
    )

    entry = float(
        flag_p3.price
    )

    # ========================================================
    # SHORT
    # ========================================================

    if direction == "SHORT":

        sl = max(
            flag_p1.price,
            hook2.p3.price,
            flag_p3.price
        )

        sl *= (
            1 + PRICE_TOL
        )

        risk = (
            sl - entry
        )

        if risk <= 0:
            return None

        target_864 = (
            flag_p1.price
            -
            0.864
            *
            (
                flag_p1.price
                -
                flag_p2.price
            )
        )

        lows = [
            p.price
            for p in pivots
            if (
                p.kind == "L"
                and
                p.index > flag_p2.index
                and
                p.price < entry
            )
        ]

        structural_tp = (
            max(lows)
            if lows
            else target_864
        )

        tp1 = (
            entry
            -
            TP1_R * risk
        )

        tp2 = (
            entry
            -
            TP2_R * risk
        )

        if USE_864_FINAL_TARGET:

            candidates = [
                x
                for x in (
                    target_864,
                    structural_tp
                )
                if x < entry
            ]

            final_tp = (
                min(candidates)
                if candidates
                else
                entry
                -
                FINAL_R * risk
            )

        else:

            final_tp = (
                entry
                -
                FINAL_R * risk
            )

        if (
            current
            >
            entry
            *
            (
                1 + PRICE_TOL
            )
        ):

            return None

    # ========================================================
    # LONG
    # ========================================================

    else:

        sl = min(
            flag_p1.price,
            hook2.p3.price,
            flag_p3.price
        )

        sl *= (
            1 - PRICE_TOL
        )

        risk = (
            entry - sl
        )

        if risk <= 0:
            return None

        target_864 = (
            flag_p1.price
            +
            0.864
            *
            (
                flag_p2.price
                -
                flag_p1.price
            )
        )

        highs = [
            p.price
            for p in pivots
            if (
                p.kind == "H"
                and
                p.index > flag_p2.index
                and
                p.price > entry
            )
        ]

        structural_tp = (
            min(highs)
            if highs
            else target_864
        )

        tp1 = (
            entry
            +
            TP1_R * risk
        )

        tp2 = (
            entry
            +
            TP2_R * risk
        )

        if USE_864_FINAL_TARGET:

            candidates = [
                x
                for x in (
                    target_864,
                    structural_tp
                )
                if x > entry
            ]

            final_tp = (
                max(candidates)
                if candidates
                else
                entry
                +
                FINAL_R * risk
            )

        else:

            final_tp = (
                entry
                +
                FINAL_R * risk
            )

        if (
            current
            <
            entry
            *
            (
                1 - PRICE_TOL
            )
        ):

            return None

    rr_final = calculate_rr(
        entry,
        sl,
        final_tp,
        direction
    )

    if rr_final < 1.2:
        return None

    setup_time = int(
        flag_p3.time
    )

    reason = (
        f"M30={trend}; "
        f"M1=123FLAG+HOOK1+HOOK2+RALLY; "
        f"Flag="
        f"{flag_p1.index}/"
        f"{flag_p2.index}/"
        f"{flag_p3.index}; "
        f"Hook1="
        f"{hook1.start_index}->"
        f"{hook1.end_index}; "
        f"Hook2="
        f"{hook2.start_index}->"
        f"{hook2.end_index}; "
        f"864={USE_864_FINAL_TARGET}"
    )

    return Setup(
        symbol=symbol,
        direction=direction,
        trend=trend,
        trend_score=trend_score,

        flag_p1=flag_p1,
        flag_p2=flag_p2,
        flag_p3=flag_p3,

        hook1=hook1,
        hook2=hook2,

        rally_start=rally_start,
        rally_end=rally_end,

        entry=entry,
        sl=sl,

        tp1=tp1,
        tp2=tp2,
        final_tp=final_tp,

        risk=risk,
        rr_final=rr_final,

        setup_time=setup_time,

        reason=reason
    )


# ============================================================
# PAPER TRADE MANAGEMENT
# ============================================================

def get_current_price(
    symbol: str
) -> float:

    df = fetch_candles(
        symbol,
        "1m",
        5
    )

    return float(
        df.iloc[-1]["close"]
    )


def close_trade(
    trade_id: int,
    signal_id: int,
    symbol: str,
    direction: str,
    entry: float,
    exit_price: float,
    reason: str,
    opened_at: int
):

    if direction == "LONG":

        pnl = (
            exit_price - entry
        ) / entry * 100

    else:

        pnl = (
            entry - exit_price
        ) / entry * 100

    con = db_connect()

    con.execute("""
        INSERT INTO closed_trades (
            signal_id,
            symbol,
            direction,
            entry,
            exit_price,
            pnl_pct,
            reason,
            opened_at,
            closed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        signal_id,
        symbol,
        direction,
        entry,
        exit_price,
        pnl,
        reason,
        opened_at,
        now_ms()
    ))

    con.execute(
        """
        DELETE FROM open_trades
        WHERE id = ?
        """,
        (trade_id,)
    )

    con.commit()
    con.close()


def manage_open_trades() -> List[str]:

    messages = []

    con = db_connect()

    trades = con.execute("""
        SELECT *
        FROM open_trades
        ORDER BY opened_at
    """).fetchall()

    con.close()

    for trade in trades:

        try:

            price = get_current_price(
                trade["symbol"]
            )

            direction = (
                trade["direction"]
            )

            if direction == "LONG":

                hit_sl = (
                    price <= trade["sl"]
                )

                hit_tp1 = (
                    price >= trade["tp1"]
                )

                hit_tp2 = (
                    price >= trade["tp2"]
                )

                hit_final = (
                    price >= trade["final_tp"]
                )

            else:

                hit_sl = (
                    price >= trade["sl"]
                )

                hit_tp1 = (
                    price <= trade["tp1"]
                )

                hit_tp2 = (
                    price <= trade["tp2"]
                )

                hit_final = (
                    price <= trade["final_tp"]
                )

            # ------------------------------------------------
            # SL
            # ------------------------------------------------

            if hit_sl:

                close_trade(
                    trade["id"],
                    trade["signal_id"],
                    trade["symbol"],
                    direction,
                    trade["entry"],
                    price,
                    "SL",
                    trade["opened_at"]
                )

                messages.append(
                    f"❌ CLOSED "
                    f"{trade['symbol']} "
                    f"{direction}\n"
                    f"Reason: SL\n"
                    f"Price: {price:g}"
                )

                continue

            # ------------------------------------------------
            # FINAL TP
            # ------------------------------------------------

            if hit_final:

                close_trade(
                    trade["id"],
                    trade["signal_id"],
                    trade["symbol"],
                    direction,
                    trade["entry"],
                    price,
                    "FINAL TP",
                    trade["opened_at"]
                )

                messages.append(
                    f"🎯 CLOSED "
                    f"{trade['symbol']} "
                    f"{direction}\n"
                    f"Reason: FINAL TP\n"
                    f"Price: {price:g}"
                )

                continue

            # ------------------------------------------------
            # TP FLAGS
            # ------------------------------------------------

            con = db_connect()

            if (
                hit_tp2
                and
                not trade["tp2_hit"]
            ):

                con.execute("""
                    UPDATE open_trades
                    SET tp2_hit = 1
                    WHERE id = ?
                """, (
                    trade["id"],
                ))

                messages.append(
                    f"🎯 "
                    f"{trade['symbol']} "
                    f"{direction} "
                    f"| TP2 reached "
                    f"| {price:g}"
                )

            elif (
                hit_tp1
                and
                not trade["tp1_hit"]
            ):

                con.execute("""
                    UPDATE open_trades
                    SET tp1_hit = 1
                    WHERE id = ?
                """, (
                    trade["id"],
                ))

                messages.append(
                    f"🎯 "
                    f"{trade['symbol']} "
                    f"{direction} "
                    f"| TP1 reached "
                    f"| {price:g}"
                )

            con.commit()
            con.close()

        except CandleDataError as exc:

            # Missing/insufficient candle data is expected.
            # Do not treat it as scanner failure.
            print(
                f"[SKIP TRADE PRICE] "
                f"{trade['symbol']}: "
                f"{exc}"
            )

        except Exception as exc:

            messages.append(
                f"⚠️ Trade management error "
                f"{trade['symbol']}: "
                f"{exc}"
            )

    return messages


# ============================================================
# CHART
# ============================================================

def draw_candles(
    ax,
    df: pd.DataFrame
):

    for i, row in df.iterrows():

        o = float(row["open"])
        h = float(row["high"])
        l = float(row["low"])
        c = float(row["close"])

        ax.vlines(
            i,
            l,
            h,
            linewidth=0.8
        )

        bottom = min(
            o,
            c
        )

        height = max(
            abs(c - o),
            abs(c) * 0.00001
        )

        rect = Rectangle(
            (
                i - 0.32,
                bottom
            ),
            0.64,
            height,
            fill=False,
            linewidth=0.8
        )

        ax.add_patch(rect)


def save_setup_chart(
    symbol: str,
    df: pd.DataFrame,
    setup: Setup
) -> str:

    chart = (
        df
        .iloc[-180:]
        .copy()
        .reset_index(drop=True)
    )

    fig, ax = plt.subplots(
        figsize=(15, 8)
    )

    draw_candles(
        ax,
        chart
    )

    times = chart[
        "time"
    ].to_numpy()

    def x_for_time(
        timestamp: int
    ) -> Optional[int]:

        if len(times) == 0:
            return None

        exact = np.where(
            times == timestamp
        )[0]

        if len(exact):
            return int(
                exact[-1]
            )

        return int(
            np.argmin(
                np.abs(
                    times - timestamp
                )
            )
        )

    points = [

        ("F1", setup.flag_p1),
        ("F2", setup.flag_p2),
        ("F3", setup.flag_p3),

        ("H1", setup.hook1.p1),
        ("H2", setup.hook1.p2),
        ("H3", setup.hook1.p3),

        ("H4", setup.hook2.p1),
        ("H5", setup.hook2.p2),
        ("H6", setup.hook2.p3),

        ("R1", setup.rally_start),
        ("R2", setup.rally_end),
    ]

    for label, pivot in points:

        x = x_for_time(
            pivot.time
        )

        if x is None:
            continue

        ax.scatter(
            [x],
            [pivot.price],
            s=28
        )

        ax.annotate(
            label,
            (
                x,
                pivot.price
            ),
            xytext=(4, 6),
            textcoords="offset points",
            fontsize=8
        )

    ax.axhline(
        setup.entry,
        linestyle="--",
        linewidth=1.0,
        label="ENTRY"
    )

    ax.axhline(
        setup.sl,
        linestyle=":",
        linewidth=1.0,
        label="SL"
    )

    ax.axhline(
        setup.tp1,
        linestyle="--",
        linewidth=0.8,
        label="TP1"
    )

    ax.axhline(
        setup.tp2,
        linestyle="--",
        linewidth=0.8,
        label="TP2"
    )

    ax.axhline(
        setup.final_tp,
        linestyle=":",
        linewidth=1.2,
        label="FINAL TP"
    )

    ax.set_title(
        f"NDS M30→M1 | "
        f"{symbol} | "
        f"{setup.direction} | "
        f"RR={setup.rr_final:.2f}"
    )

    ax.set_xlabel(
        "1M candles"
    )

    ax.set_ylabel(
        "Price"
    )

    ax.legend(
        loc="best"
    )

    ax.grid(
        alpha=0.15
    )

    path = (
        CHART_DIR
        /
        f"{symbol}_"
        f"{setup.direction}_"
        f"{setup.setup_time}.png"
    )

    fig.tight_layout()

    fig.savefig(
        path,
        dpi=140
    )

    plt.close(fig)

    return str(path)


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(
    text: str,
    image_path: Optional[str] = None
) -> bool:

    if (
        not TELEGRAM_BOT_TOKEN
        or
        not TELEGRAM_CHAT_ID
    ):

        return False

    try:

        base = (
            "https://api.telegram.org/"
            f"bot{TELEGRAM_BOT_TOKEN}"
        )

        if (
            image_path
            and
            Path(image_path).exists()
        ):

            with open(
                image_path,
                "rb"
            ) as photo:

                response = SESSION.post(
                    f"{base}/sendPhoto",
                    data={
                        "chat_id":
                            TELEGRAM_CHAT_ID,
                        "caption":
                            text[:1024]
                    },
                    files={
                        "photo": photo
                    },
                    timeout=REQUEST_TIMEOUT
                )

        else:

            response = SESSION.post(
                f"{base}/sendMessage",
                data={
                    "chat_id":
                        TELEGRAM_CHAT_ID,
                    "text":
                        text,
                    "parse_mode":
                        "HTML",
                    "disable_web_page_preview":
                        True
                },
                timeout=REQUEST_TIMEOUT
            )

        return bool(
            response.ok
        )

    except Exception:

        return False


def fmt_price(
    value: float
) -> str:

    if value >= 1000:

        return (
            f"{value:,.2f}"
        )

    if value >= 1:

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


def signal_message(
    setup: Setup
) -> str:

    emoji = (
        "🟢"
        if setup.direction == "LONG"
        else
        "🔴"
    )

    return (
        f"{emoji} "
        f"<b>NDS M30→M1 "
        f"{setup.direction}</b>\n\n"

        f"<b>{setup.symbol}</b>\n"

        f"30M Trend: "
        f"<b>{setup.trend}</b>\n"

        f"Entry: "
        f"<b>{fmt_price(setup.entry)}</b>\n"

        f"SL: "
        f"<b>{fmt_price(setup.sl)}</b>\n"

        f"TP1: "
        f"<b>{fmt_price(setup.tp1)}</b>\n"

        f"TP2: "
        f"<b>{fmt_price(setup.tp2)}</b>\n"

        f"Final TP: "
        f"<b>{fmt_price(setup.final_tp)}</b>\n"

        f"Risk: "
        f"{setup.risk:.6g}\n"

        f"RR: "
        f"<b>{setup.rr_final:.2f}</b>\n\n"

        f"Structure:\n"
        f"123 Flag → "
        f"Hook 1 → "
        f"Hook 2 → "
        f"Rally\n\n"

        f"Setup: "
        f"{utc_text(setup.setup_time)}\n"

        f"Mode: "
        f"<b>PAPER ONLY</b>"
    )


# ============================================================
# PERFORMANCE
# ============================================================

def performance_summary():

    con = db_connect()

    closed = con.execute(
        """
        SELECT COUNT(*)
        FROM closed_trades
        """
    ).fetchone()[0]

    wins = con.execute(
        """
        SELECT COUNT(*)
        FROM closed_trades
        WHERE pnl_pct > 0
        """
    ).fetchone()[0]

    losses = con.execute(
        """
        SELECT COUNT(*)
        FROM closed_trades
        WHERE pnl_pct <= 0
        """
    ).fetchone()[0]

    pnl = con.execute(
        """
        SELECT COALESCE(
            SUM(pnl_pct),
            0
        )
        FROM closed_trades
        """
    ).fetchone()[0]

    con.close()

    return (
        int(closed),
        int(wins),
        int(losses),
        float(pnl)
    )


def report(
    scanned: int,
    new_setups: List[Setup],
    errors: int,
    skipped: int
) -> str:

    closed, wins, losses, pnl = (
        performance_summary()
    )

    opens = count_open_trades()

    longs = sum(
        1
        for s in new_setups
        if s.direction == "LONG"
    )

    shorts = sum(
        1
        for s in new_setups
        if s.direction == "SHORT"
    )

    return (
        "📊 "
        "<b>NDS M30→M1 REPORT</b>\n\n"

        f"Assets scanned: "
        f"<b>{scanned}</b>\n"

        f"New LONG: "
        f"<b>{longs}</b>\n"

        f"New SHORT: "
        f"<b>{shorts}</b>\n"

        f"Open trades: "
        f"<b>{opens}</b>\n\n"

        f"Closed: {closed}\n"
        f"Wins: {wins}\n"
        f"Losses: {losses}\n"

        f"PnL: "
        f"<b>{pnl:.2f}%</b>\n"

        f"Skipped: {skipped}\n"
        f"Errors: {errors}\n\n"

        f"Version: "
        f"{VERSION}\n"

        f"Mode: "
        f"<b>PAPER ONLY</b>"
    )


# ============================================================
# SCAN ONE SYMBOL
# ============================================================

def scan_symbol(
    symbol: str
) -> Optional[Setup]:

    # --------------------------------------------------------
    # 30M TREND
    # --------------------------------------------------------

    m30 = fetch_candles(
        symbol,
        "30m",
        M30_COUNT
    )

    trend, trend_score, _ = (
        detect_m30_trend(
            m30
        )
    )

    if trend == "NEUTRAL":
        return None

    # --------------------------------------------------------
    # 1M ENTRY
    # --------------------------------------------------------

    m1 = fetch_candles(
        symbol,
        "1m",
        M1_COUNT
    )

    return build_setup(
        symbol=symbol,
        m1=m1,
        trend=trend,
        trend_score=trend_score
    )


# ============================================================
# MAIN
# ============================================================

def run_once():

    if not PAPER_ONLY:

        raise RuntimeError(
            "PAPER_ONLY must remain True."
        )

    init_db()

    run_time = now_ms()

    scanned = 0
    errors = 0
    skipped = 0

    new_setups = []

    # --------------------------------------------------------
    # Manage existing trades first.
    # --------------------------------------------------------

    messages = (
        manage_open_trades()
    )

    for message in messages:

        telegram_send(
            message
        )

    # --------------------------------------------------------
    # Scan
    # --------------------------------------------------------

    for symbol in SYMBOLS:

        try:

            scanned += 1

            if (
                count_open_trades()
                >=
                MAX_OPEN_TRADES
            ):

                break

            setup = scan_symbol(
                symbol
            )

            if setup is None:

                time.sleep(
                    SLEEP_BETWEEN_SYMBOLS
                )

                continue

            # ------------------------------------------------
            # Duplicate protection
            # ------------------------------------------------

            if recent_duplicate(
                setup.symbol,
                setup.direction,
                setup.setup_time
            ):

                time.sleep(
                    SLEEP_BETWEEN_SYMBOLS
                )

                continue

            # ------------------------------------------------
            # Chart
            # ------------------------------------------------

            chart_df = fetch_candles(
                setup.symbol,
                "1m",
                M1_COUNT
            )

            chart_path = (
                save_setup_chart(
                    setup.symbol,
                    chart_df,
                    setup
                )
            )

            # ------------------------------------------------
            # DB
            # ------------------------------------------------

            signal_id = (
                insert_signal(
                    setup,
                    chart_path
                )
            )

            if signal_id:

                new_setups.append(
                    setup
                )

                telegram_send(
                    signal_message(
                        setup
                    ),
                    chart_path
                )

            time.sleep(
                SLEEP_BETWEEN_SYMBOLS
            )

        except CandleDataError as exc:

            # ------------------------------------------------
            # EXPECTED DATA PROBLEM
            #
            # Example:
            # PF_MKRUSD 30m has insufficient candles.
            #
            # This is NOT a scanner error.
            # ------------------------------------------------

            skipped += 1

            print(
                f"[SKIP] "
                f"{symbol}: "
                f"{exc}"
            )

            time.sleep(
                SLEEP_BETWEEN_SYMBOLS
            )

            continue

        except Exception as exc:

            errors += 1

            print(
                f"[ERROR] "
                f"{symbol}: "
                f"{exc}"
            )

            print(
                traceback.format_exc()
            )

            time.sleep(
                SLEEP_BETWEEN_SYMBOLS
            )

            continue

    # --------------------------------------------------------
    # Scanner run
    # --------------------------------------------------------

    con = db_connect()

    con.execute("""
        INSERT INTO scanner_runs (
            run_time,
            version,
            assets_scanned,
            new_signals,
            open_trades,
            errors
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        run_time,
        VERSION,
        scanned,
        len(new_setups),
        count_open_trades(),
        errors
    ))

    con.commit()
    con.close()

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    text = report(
        scanned,
        new_setups,
        errors,
        skipped
    )

    print(
        text
        .replace("<b>", "")
        .replace("</b>", "")
    )

    telegram_send(
        text
    )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    run_once()
