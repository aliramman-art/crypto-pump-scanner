# ============================================================
# NDS M30 -> M1 LIVE SCANNER
# VERSION 4.4.4
# ============================================================
#
# PAPER ONLY
#
# NDS LOGIC
#
# M30:
#   Detect NDS Hook
#
# SHORT:
#   START -> H1 -> L1 -> H2 -> L2 -> H3
#   H2 > H1
#   L2 < L1
#   H3 > H2
#
# LONG:
#   START -> L1 -> H1 -> L2 -> H2 -> L3
#   L2 < L1
#   H2 > H1
#   L3 < L2
#
# IMPORTANT:
#   H3 / L3 = M1 TRADE ACTIVATOR
#   H3 / L3 IS NOT ENTRY
#
# M1:
#   123F = FOUR CONSECUTIVE PIVOT HIGHS
#
# SHORT:
#   1 < 2 < 3 < F
#
# LONG:
#   1 > 2 > 3 > F
#
# F = ENTRY
#
# M30:
#   86.4% = FINAL TP
#
# SHORT:
#   TP = H3 - 86.4% of START -> H3 movement
#
# LONG:
#   TP = L3 + 86.4% of START -> L3 movement
#
# NO REAL ORDERS
# ============================================================

from __future__ import annotations

import os
import sqlite3
import time
import traceback
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

import requests
import pandas as pd
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "4.4.4"

PAPER_ONLY = True

DB_FILE = os.getenv(
    "NDS_DB_FILE",
    "nds_m30_m1_v44.db"
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

M30_COUNT = int(
    os.getenv(
        "NDS_M30_COUNT",
        "250"
    )
)

M1_COUNT = int(
    os.getenv(
        "NDS_M1_COUNT",
        "700"
    )
)

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

# Minimum swing distance
MIN_SWING_PCT = float(
    os.getenv(
        "NDS_MIN_SWING_PCT",
        "0.0005"
    )
)

# Maximum age of M1 F
MAX_F_AGE_MIN = int(
    os.getenv(
        "NDS_MAX_F_AGE_MIN",
        "5"
    )
)

# Minimum distance of each 123F leg
MIN_123F_LEG_PCT = float(
    os.getenv(
        "NDS_MIN_123F_LEG_PCT",
        "0.0003"
    )
)

# 86.4%
RETRACE_864 = 0.864

# Maximum open paper trades
MAX_OPEN_TRADES = int(
    os.getenv(
        "NDS_MAX_OPEN_TRADES",
        "5"
    )
)

# Telegram
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
# KRAKEN UNIVERSE
# ============================================================

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

# Explicitly remove SNXX
SYMBOLS = [
    x for x in SYMBOLS
    if "SNXX" not in x
]


# ============================================================
# DIAGNOSTICS
# ============================================================

DIAG = {
    "m30_requests": 0,
    "m30_ok": 0,
    "m30_short": 0,

    "m30_pivot_highs": 0,
    "m30_pivot_lows": 0,

    "hook_candidates": 0,
    "hook_structure_valid": 0,
    "hook_incomplete": 0,
    "hook_confirmed": 0,
    "hook_confirmed_current": 0,

    "m1_symbols_selected": 0,
    "m1_requests": 0,
    "m1_ok": 0,
    "m1_short": 0,

    "m1_123f_candidates": 0,
    "m1_123f_short": 0,
    "m1_123f_long": 0,

    "new_signals": 0,
    "open_trades": 0,
    "closed": 0,
    "wins": 0,
    "losses": 0,
    "pnl": 0.0,

    "charts": 0,
    "errors": 0,
}


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
    symbol: str
    direction: str

    start: Pivot

    p1: Pivot
    q1: Pivot
    p2: Pivot
    q2: Pivot
    p3: Pivot
    q3: Pivot

    activator: Pivot

    tp_864: float


@dataclass
class Setup:
    symbol: str
    direction: str

    hook: Hook

    p1: Pivot
    p2: Pivot
    p3: Pivot
    f: Pivot

    entry: float
    sl: float
    tp: float

    created_at: int


# ============================================================
# TIME
# ============================================================

def now_ms() -> int:
    return int(
        datetime.now(
            timezone.utc
        ).timestamp() * 1000
    )


def format_time(ms: int) -> str:
    try:
        return datetime.fromtimestamp(
            ms / 1000,
            tz=timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    except Exception:
        return str(ms)


# ============================================================
# HELPERS
# ============================================================

def safe_float(
    value: Any,
    default: float = 0.0
) -> float:

    try:
        return float(value)
    except Exception:
        return default


def pct_distance(
    a: float,
    b: float
) -> float:

    if not a:
        return 999.0

    return abs(a - b) / abs(a)


def pct_move(
    a: float,
    b: float
) -> float:

    if not a:
        return 0.0

    return (
        (b - a) / a
    ) * 100.0


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(
    message: str,
    image_bytes: Optional[bytes] = None
) -> bool:

    if not TELEGRAM_BOT_TOKEN:
        print(
            "[WARN] TELEGRAM_BOT_TOKEN missing"
        )
        return False

    if not TELEGRAM_CHAT_ID:
        print(
            "[WARN] TELEGRAM_CHAT_ID missing"
        )
        return False

    try:

        if image_bytes:

            response = requests.post(
                f"https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": message,
                },
                files={
                    "photo": (
                        "nds_signal.png",
                        image_bytes,
                        "image/png"
                    )
                },
                timeout=REQUEST_TIMEOUT
            )

        else:

            response = requests.post(
                f"https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": message,
                    "disable_web_page_preview": True
                },
                timeout=REQUEST_TIMEOUT
            )

        return response.ok

    except Exception as e:

        print(
            f"[WARN] Telegram error: {e}"
        )

        return False


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

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            direction TEXT,
            entry REAL,
            sl REAL,
            tp REAL,
            hook_start REAL,
            hook_activator REAL,
            setup_time INTEGER,
            created_at INTEGER,
            status TEXT DEFAULT 'OPEN',
            exit_price REAL,
            exit_time INTEGER,
            exit_reason TEXT,
            pnl_pct REAL
        )
        """
    )

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS scanner_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_time INTEGER,
            version TEXT,
            assets_scanned INTEGER,
            new_signals INTEGER,
            open_trades INTEGER,
            errors INTEGER
        )
        """
    )

    con.commit()
    con.close()


# ============================================================
# KRAKEN DATA
# ============================================================

def fetch_candles(
    symbol: str,
    resolution: str,
    count: int
) -> pd.DataFrame:

    url = (
        f"{KRAKEN_BASE}/trade/"
        f"{symbol}/{resolution}"
    )

    response = requests.get(
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
        return pd.DataFrame()

    rows = []

    for c in candles:

        try:

            rows.append({
                "time": int(c["time"]),
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

        except Exception:
            continue

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    df = (
        df
        .sort_values("time")
        .drop_duplicates("time")
        .reset_index(drop=True)
    )

    # Remove currently forming candle
    if resolution == "30m":
        tf = 30 * 60 * 1000
    else:
        tf = 60 * 1000

    current_bucket = (
        now_ms() // tf
    ) * tf

    df = df[
        df["time"] < current_bucket
    ].reset_index(drop=True)

    return df


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(
    df: pd.DataFrame
) -> List[Pivot]:

    if df.empty:
        return []

    highs = df["high"].to_numpy(
        dtype=float
    )

    lows = df["low"].to_numpy(
        dtype=float
    )

    times = df["time"].to_numpy(
        dtype=np.int64
    )

    raw: List[Pivot] = []

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT
    ):

        h = highs[i]
        l = lows[i]

        left_h = highs[
            i - PIVOT_LEFT:i
        ]

        right_h = highs[
            i + 1:
            i + PIVOT_RIGHT + 1
        ]

        left_l = lows[
            i - PIVOT_LEFT:i
        ]

        right_l = lows[
            i + 1:
            i + PIVOT_RIGHT + 1
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
                    index=i,
                    time=int(times[i]),
                    kind="H",
                    price=float(h)
                )
            )

        if is_low:

            raw.append(
                Pivot(
                    index=i,
                    time=int(times[i]),
                    kind="L",
                    price=float(l)
                )
            )

    raw.sort(
        key=lambda x: x.index
    )

    # Alternating pivot series
    pivots: List[Pivot] = []

    for p in raw:

        if not pivots:
            pivots.append(p)
            continue

        last = pivots[-1]

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

        if (
            pct_distance(
                last.price,
                p.price
            ) < MIN_SWING_PCT
        ):
            continue

        pivots.append(p)

    return pivots


# ============================================================
# M30 HOOK
# ============================================================

def short_hook_candidates(
    symbol: str,
    pivots: List[Pivot]
) -> List[Hook]:

    result = []

    for i in range(
        0,
        len(pivots) - 5
    ):

        seq = pivots[
            i:i + 6
        ]

        if len(seq) < 6:
            continue

        if [
            p.kind for p in seq
        ] != [
            "H",
            "L",
            "H",
            "L",
            "H",
            "L"
        ]:
            continue

        DIAG[
            "hook_candidates"
        ] += 1

        start, l1, h2, l2, h3, l3 = seq

        # NDS SHORT Hook
        if not (
            h2.price > start.price
            and
            l2.price < l1.price
            and
            h3.price > h2.price
        ):
            continue

        DIAG[
            "hook_structure_valid"
        ] += 1

        # H3 is the activator.
        #
        # 86.4% is calculated from START
        # to H3 and is the FINAL TP.

        tp = (
            h3.price
            -
            RETRACE_864
            *
            (
                h3.price
                -
                start.price
            )
        )

        # For a valid SHORT TP
        if tp >= h3.price:
            continue

        result.append(
            Hook(
                symbol=symbol,
                direction="SHORT",
                start=start,
                p1=start,
                q1=l1,
                p2=h2,
                q2=l2,
                p3=h3,
                q3=l3,
                activator=h3,
                tp_864=tp
            )
        )

    return result


def long_hook_candidates(
    symbol: str,
    pivots: List[Pivot]
) -> List[Hook]:

    result = []

    for i in range(
        0,
        len(pivots) - 5
    ):

        seq = pivots[
            i:i + 6
        ]

        if len(seq) < 6:
            continue

        if [
            p.kind for p in seq
        ] != [
            "L",
            "H",
            "L",
            "H",
            "L",
            "H"
        ]:
            continue

        DIAG[
            "hook_candidates"
        ] += 1

        start, h1, l2, h2, l3, h3 = seq

        # NDS LONG Hook
        if not (
            l2.price < start.price
            and
            h2.price > h1.price
            and
            l3.price < l2.price
        ):
            continue

        DIAG[
            "hook_structure_valid"
        ] += 1

        tp = (
            l3.price
            +
            RETRACE_864
            *
            (
                start.price
                -
                l3.price
            )
        )

        if tp <= l3.price:
            continue

        result.append(
            Hook(
                symbol=symbol,
                direction="LONG",
                start=start,
                p1=start,
                q1=h1,
                p2=l2,
                q2=h2,
                p3=l3,
                q3=h3,
                activator=l3,
                tp_864=tp
            )
        )

    return result


def latest_confirmed_hook(
    symbol: str,
    df: pd.DataFrame
) -> Optional[Hook]:

    pivots = find_pivots(df)

    DIAG[
        "m30_pivot_highs"
    ] += sum(
        p.kind == "H"
        for p in pivots
    )

    DIAG[
        "m30_pivot_lows"
    ] += sum(
        p.kind == "L"
        for p in pivots
    )

    shorts = short_hook_candidates(
        symbol,
        pivots
    )

    longs = long_hook_candidates(
        symbol,
        pivots
    )

    candidates = (
        shorts + longs
    )

    if not candidates:
        return None

    # Newest H3/L3
    candidates.sort(
        key=lambda x:
        x.activator.time
    )

    hook = candidates[-1]

    DIAG[
        "hook_confirmed"
    ] += 1

    return hook


# ============================================================
# M1 123F
# ============================================================
#
# IMPORTANT:
#
# 123F consists of FOUR CONSECUTIVE
# PIVOT HIGHS.
#
# SHORT:
#     1 < 2 < 3 < F
#
# LONG:
#     1 > 2 > 3 > F
#
# F = ENTRY
# ============================================================

def detect_m1_123f(
    df: pd.DataFrame,
    hook: Hook
) -> Optional[Dict[str, Any]]:

    pivots = find_pivots(df)

    # Only pivots after H3/L3
    pivots = [
        p for p in pivots
        if p.time > hook.activator.time
    ]

    highs = [
        p for p in pivots
        if p.kind == "H"
    ]

    if len(highs) < 4:
        return None

    DIAG[
        "m1_123f_candidates"
    ] += 1

    # --------------------------------------------------------
    # IMPORTANT:
    # Use the LATEST FOUR CONSECUTIVE PIVOT HIGHS.
    #
    # They are exactly:
    #
    #       1 -> 2 -> 3 -> F
    #
    # No alternating low pivot is inserted between them.
    # --------------------------------------------------------

    for i in range(
        len(highs) - 4,
        -1,
        -1
    ):

        p1 = highs[i]
        p2 = highs[i + 1]
        p3 = highs[i + 2]
        pf = highs[i + 3]

        # F must be recent
        age_minutes = (
            now_ms()
            -
            pf.time
        ) / 60000.0

        if age_minutes < 0:
            continue

        if (
            age_minutes
            >
            MAX_F_AGE_MIN
        ):
            continue

        # ----------------------------------------------------
        # SHORT
        # Four rising highs
        # 1 < 2 < 3 < F
        # ----------------------------------------------------

        short_ok = (
            p1.price
            <
            p2.price
            <
            p3.price
            <
            pf.price
        )

        # ----------------------------------------------------
        # LONG
        # Four falling highs
        # 1 > 2 > 3 > F
        # ----------------------------------------------------

        long_ok = (
            p1.price
            >
            p2.price
            >
            p3.price
            >
            pf.price
        )

        # Leg size
        legs_ok = (
            pct_distance(
                p1.price,
                p2.price
            )
            >=
            MIN_123F_LEG_PCT
            and
            pct_distance(
                p2.price,
                p3.price
            )
            >=
            MIN_123F_LEG_PCT
            and
            pct_distance(
                p3.price,
                pf.price
            )
            >=
            MIN_123F_LEG_PCT
        )

        if not legs_ok:
            continue

        if hook.direction == "SHORT":

            if not short_ok:
                continue

            DIAG[
                "m1_123f_short"
            ] += 1

            return {
                "direction": "SHORT",
                "p1": p1,
                "p2": p2,
                "p3": p3,
                "f": pf
            }

        if hook.direction == "LONG":

            if not long_ok:
                continue

            DIAG[
                "m1_123f_long"
            ] += 1

            return {
                "direction": "LONG",
                "p1": p1,
                "p2": p2,
                "p3": p3,
                "f": pf
            }

    return None


# ============================================================
# STOP LOSS
# ============================================================

def calculate_sl(
    df: pd.DataFrame,
    direction: str,
    entry: float
) -> float:

    pivots = find_pivots(df)

    if direction == "SHORT":

        lows = [
            p for p in pivots
            if (
                p.kind == "L"
                and
                p.price < entry
            )
        ]

        highs = [
            p for p in pivots
            if (
                p.kind == "H"
                and
                p.price > entry
            )
        ]

        if highs:
            return highs[-1].price

        if lows:
            return entry + (
                entry * 0.01
            )

        return entry * 1.01

    else:

        lows = [
            p for p in pivots
            if (
                p.kind == "L"
                and
                p.price < entry
            )
        ]

        highs = [
            p for p in pivots
            if (
                p.kind == "H"
                and
                p.price > entry
            )
        ]

        if lows:
            return lows[-1].price

        if highs:
            return entry * 0.99

        return entry * 0.99


# ============================================================
# BUILD SETUP
# ============================================================

def build_setup(
    df: pd.DataFrame,
    hook: Hook,
    pattern: Dict[str, Any]
) -> Optional[Setup]:

    entry = float(
        pattern["f"].price
    )

    tp = float(
        hook.tp_864
    )

    sl = calculate_sl(
        df,
        hook.direction,
        entry
    )

    # TP direction validation
    if hook.direction == "SHORT":

        if not (
            tp < entry
        ):
            return None

        if not (
            sl > entry
        ):
            sl = entry * 1.01

    else:

        if not (
            tp > entry
        ):
            return None

        if not (
            sl < entry
        ):
            sl = entry * 0.99

    return Setup(
        symbol=hook.symbol,
        direction=hook.direction,
        hook=hook,

        p1=pattern["p1"],
        p2=pattern["p2"],
        p3=pattern["p3"],
        f=pattern["f"],

        entry=entry,
        sl=float(sl),
        tp=tp,

        created_at=now_ms()
    )


# ============================================================
# DATABASE - SIGNALS
# ============================================================

def has_duplicate_signal(
    setup: Setup
) -> bool:

    con = db_connect()

    row = con.execute(
        """
        SELECT id
        FROM signals
        WHERE symbol=?
          AND direction=?
          AND setup_time=?
        LIMIT 1
        """,
        (
            setup.symbol,
            setup.direction,
            setup.f.time
        )
    ).fetchone()

    con.close()

    return row is not None


def insert_signal(
    setup: Setup
) -> int:

    con = db_connect()

    cur = con.execute(
        """
        INSERT INTO signals (
            symbol,
            direction,
            entry,
            sl,
            tp,
            hook_start,
            hook_activator,
            setup_time,
            created_at,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
        """,
        (
            setup.symbol,
            setup.direction,
            setup.entry,
            setup.sl,
            setup.tp,
            setup.hook.start.price,
            setup.hook.activator.price,
            setup.f.time,
            now_ms()
        )
    )

    con.commit()

    signal_id = cur.lastrowid

    con.close()

    return int(
        signal_id
        or
        0
    )


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():

    con = db_connect()

    rows = con.execute(
        """
        SELECT *
        FROM signals
        WHERE status='OPEN'
        ORDER BY id DESC
        """
    ).fetchall()

    con.close()

    return rows


def count_open_trades() -> int:

    con = db_connect()

    row = con.execute(
        """
        SELECT COUNT(*)
        FROM signals
        WHERE status='OPEN'
        """
    ).fetchone()

    con.close()

    return int(
        row[0]
    )


# ============================================================
# CURRENT PRICE
# ============================================================

def current_price(
    symbol: str
) -> Optional[float]:

    try:

        df = fetch_candles(
            symbol,
            "1m",
            5
        )

        if df.empty:
            return None

        return float(
            df.iloc[-1]["close"]
        )

    except Exception:

        return None


# ============================================================
# MONITOR OPEN TRADES
# ============================================================

def monitor_open_trades():

    rows = get_open_trades()

    for row in rows:

        symbol = row["symbol"]

        price = current_price(
            symbol
        )

        if price is None:
            continue

        direction = row["direction"]

        entry = float(
            row["entry"]
        )

        sl = float(
            row["sl"]
        )

        tp = float(
            row["tp"]
        )

        exit_reason = None

        if direction == "SHORT":

            if price <= tp:
                exit_reason = "TP_86.4"

            elif price >= sl:
                exit_reason = "SL"

        else:

            if price >= tp:
                exit_reason = "TP_86.4"

            elif price <= sl:
                exit_reason = "SL"

        if exit_reason is None:
            continue

        if direction == "SHORT":

            pnl = (
                (entry - price)
                /
                entry
            ) * 100

        else:

            pnl = (
                (price - entry)
                /
                entry
            ) * 100

        con = db_connect()

        con.execute(
            """
            UPDATE signals
            SET
                status='CLOSED',
                exit_price=?,
                exit_time=?,
                exit_reason=?,
                pnl_pct=?
            WHERE id=?
            """,
            (
                price,
                now_ms(),
                exit_reason,
                pnl,
                row["id"]
            )
        )

        con.commit()
        con.close()

        DIAG["closed"] += 1

        if pnl > 0:
            DIAG["wins"] += 1
        else:
            DIAG["losses"] += 1

        DIAG["pnl"] += pnl

        message = (
            "🔔 NDS CLOSE\n\n"
            f"{'🟢' if direction == 'LONG' else '🔴'} "
            f"{direction} {symbol}\n\n"
            f"Entry: {entry:.8g}\n"
            f"Exit: {price:.8g}\n"
            f"Result: {pnl:+.2f}%\n"
            f"Reason: {exit_reason}\n"
            f"TP: {tp:.8g}\n"
            f"SL: {sl:.8g}"
        )

        telegram_send(
            message
        )


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
            linewidth=0.7
        )

        bottom = min(
            o,
            c
        )

        height = max(
            abs(c - o),
            abs(c) * 0.00001
        )

        ax.add_patch(
            plt.Rectangle(
                (
                    i - 0.30,
                    bottom
                ),
                0.60,
                height,
                fill=False,
                linewidth=0.7
            )
        )


def create_chart(
    symbol: str,
    m30: pd.DataFrame,
    m1: pd.DataFrame,
    setup: Setup
) -> bytes:

    fig, axes = plt.subplots(
        2,
        1,
        figsize=(16, 11)
    )

    ax30 = axes[0]
    ax1 = axes[1]

    # --------------------------------------------------------
    # M30
    # --------------------------------------------------------

    d30 = m30.tail(100).reset_index(
        drop=True
    )

    draw_candles(
        ax30,
        d30
    )

    def x30(t):

        arr = d30[
            "time"
        ].to_numpy()

        if len(arr) == 0:
            return None

        return int(
            np.argmin(
                np.abs(
                    arr - t
                )
            )
        )

    hook_points = [
        (
            "START",
            setup.hook.start
        ),
        (
            "H1",
            setup.hook.p1
        ),
        (
            "L1",
            setup.hook.q1
        ),
        (
            "H2",
            setup.hook.p2
        ),
        (
            "L2",
            setup.hook.q2
        ),
        (
            "H3"
            if setup.direction == "SHORT"
            else "L3",
            setup.hook.activator
        ),
    ]

    for label, p in hook_points:

        x = x30(
            p.time
        )

        if x is None:
            continue

        ax30.scatter(
            [x],
            [p.price],
            s=45
        )

        ax30.annotate(
            label,
            (
                x,
                p.price
            ),
            xytext=(
                5,
                8
            ),
            textcoords="offset points",
            fontsize=9,
            fontweight="bold"
        )

    ax30.axhline(
        setup.tp,
        linestyle="--",
        linewidth=1.5,
        label="86.4% FINAL TP"
    )

    ax30.set_title(
        f"M30 NDS HOOK | "
        f"{symbol} | "
        f"{setup.direction}"
    )

    ax30.set_ylabel(
        "Price"
    )

    ax30.grid(
        alpha=0.15
    )

    ax30.legend(
        loc="best"
    )

    # --------------------------------------------------------
    # M1
    # --------------------------------------------------------

    d1 = m1.tail(180).reset_index(
        drop=True
    )

    draw_candles(
        ax1,
        d1
    )

    def x1(t):

        arr = d1[
            "time"
        ].to_numpy()

        if len(arr) == 0:
            return None

        return int(
            np.argmin(
                np.abs(
                    arr - t
                )
            )
        )

    m1_points = [
        (
            "1",
            setup.p1
        ),
        (
            "2",
            setup.p2
        ),
        (
            "3",
            setup.p3
        ),
        (
            "F = ENTRY",
            setup.f
        ),
    ]

    for label, p in m1_points:

        x = x1(
            p.time
        )

        if x is None:
            continue

        ax1.scatter(
            [x],
            [p.price],
            s=55
        )

        ax1.annotate(
            label,
            (
                x,
                p.price
            ),
            xytext=(
                5,
                8
            ),
            textcoords="offset points",
            fontsize=10,
            fontweight="bold"
        )

    ax1.axhline(
        setup.entry,
        linestyle="--",
        linewidth=1.3,
        label="F = ENTRY"
    )

    ax1.axhline(
        setup.tp,
        linestyle="--",
        linewidth=1.3,
        label="M30 86.4% FINAL TP"
    )

    ax1.axhline(
        setup.sl,
        linestyle=":",
        linewidth=1.3,
        label="SL"
    )

    ax1.set_title(
        f"M1 123F | "
        f"1 → 2 → 3 → F | "
        f"F = ENTRY"
    )

    ax1.set_xlabel(
        "1M Closed Candles"
    )

    ax1.set_ylabel(
        "Price"
    )

    ax1.grid(
        alpha=0.15
    )

    ax1.legend(
        loc="best"
    )

    fig.suptitle(
        f"NDS M30 → M1 | "
        f"{symbol} | "
        f"{setup.direction}",
        fontsize=14,
        fontweight="bold"
    )

    plt.tight_layout()

    image = io.BytesIO()

    plt.savefig(
        image,
        format="png",
        dpi=140,
        bbox_inches="tight"
    )

    plt.close(fig)

    image.seek(0)

    return image.getvalue()


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def signal_message(
    setup: Setup
) -> str:

    hook = setup.hook

    return (
        "🚨 NDS NEW SIGNAL\n\n"

        f"{'🟢' if setup.direction == 'LONG' else '🔴'} "
        f"{setup.direction} "
        f"{setup.symbol}\n\n"

        f"Entry F: {setup.entry:.8g}\n"
        f"SL: {setup.sl:.8g}\n"
        f"TP 86.4%: {setup.tp:.8g}\n\n"

        "M30 HOOK\n"
        f"START: {hook.start.price:.8g}\n"
        f"Activator: "
        f"{hook.activator.price:.8g}\n\n"

        "M1 123F\n"
        f"1: {setup.p1.price:.8g}\n"
        f"2: {setup.p2.price:.8g}\n"
        f"3: {setup.p3.price:.8g}\n"
        f"F: {setup.f.price:.8g}\n\n"

        "F = ENTRY\n"
        "86.4% M30 = FINAL TP\n\n"

        "🧪 PAPER ONLY"
    )


# ============================================================
# SCAN ONE SYMBOL
# ============================================================

def scan_symbol(
    symbol: str
) -> Optional[Setup]:

    # --------------------------------------------------------
    # M30
    # --------------------------------------------------------

    DIAG[
        "m30_requests"
    ] += 1

    m30 = fetch_candles(
        symbol,
        "30m",
        M30_COUNT
    )

    if m30.empty:

        DIAG[
            "m30_short"
        ] += 1

        return None

    DIAG[
        "m30_ok"
    ] += 1

    hook = latest_confirmed_hook(
        symbol,
        m30
    )

    if hook is None:
        return None

    DIAG[
        "hook_confirmed_current"
    ] += 1

    # --------------------------------------------------------
    # M1
    # --------------------------------------------------------

    DIAG[
        "m1_requests"
    ] += 1

    m1 = fetch_candles(
        symbol,
        "1m",
        M1_COUNT
    )

    if m1.empty:

        DIAG[
            "m1_short"
        ] += 1

        return None

    DIAG[
        "m1_ok"
    ] += 1

    pattern = detect_m1_123f(
        m1,
        hook
    )

    if pattern is None:
        return None

    setup = build_setup(
        m1,
        hook,
        pattern
    )

    return setup


# ============================================================
# REPORT
# ============================================================

def print_diagnostic():

    print()
    print(
        "🔎 NDS DIAGNOSTIC"
    )

    print(
        f"Version: {VERSION}"
    )

    print(
        f"Time: "
        f"{format_time(now_ms())}"
    )

    print()

    print("M30")

    print(
        f"Requests: "
        f"{DIAG['m30_requests']}"
    )

    print(
        f"Data OK: "
        f"{DIAG['m30_ok']}"
    )

    print(
        f"Short data: "
        f"{DIAG['m30_short']}"
    )

    print(
        f"Pivot Highs: "
        f"{DIAG['m30_pivot_highs']}"
    )

    print(
        f"Pivot Lows: "
        f"{DIAG['m30_pivot_lows']}"
    )

    print(
        f"Hook Candidates: "
        f"{DIAG['hook_candidates']}"
    )

    print(
        f"Structure Valid: "
        f"{DIAG['hook_structure_valid']}"
    )

    print(
        f"Confirmed Hooks: "
        f"{DIAG['hook_confirmed']}"
    )

    print(
        f"Confirmed This Cycle: "
        f"{DIAG['hook_confirmed_current']}"
    )

    print()

    print(
        "M1 PIPELINE"
    )

    print(
        f"Symbols Selected: "
        f"{DIAG['hook_confirmed_current']}"
    )

    print(
        f"Requests: "
        f"{DIAG['m1_requests']}"
    )

    print(
        f"Data OK: "
        f"{DIAG['m1_ok']}"
    )

    print(
        f"Short data: "
        f"{DIAG['m1_short']}"
    )

    print(
        f"123F Candidates: "
        f"{DIAG['m1_123f_candidates']}"
    )

    print(
        f"123F SHORT: "
        f"{DIAG['m1_123f_short']}"
    )

    print(
        f"123F LONG: "
        f"{DIAG['m1_123f_long']}"
    )

    print()

    print("TRADES")

    print(
        f"New Signals: "
        f"{DIAG['new_signals']}"
    )

    print(
        f"Open Trades: "
        f"{count_open_trades()}"
    )

    print(
        f"Closed: "
        f"{DIAG['closed']}"
    )

    print(
        f"Wins: "
        f"{DIAG['wins']}"
    )

    print(
        f"Losses: "
        f"{DIAG['losses']}"
    )

    print(
        f"PnL: "
        f"{DIAG['pnl']:+.2f}%"
    )

    print()

    print(
        f"Charts: "
        f"{DIAG['charts']}"
    )

    print(
        f"Errors: "
        f"{DIAG['errors']}"
    )


def telegram_report():

    text = (
        "🔎 NDS DIAGNOSTIC\n\n"

        f"Version: {VERSION}\n\n"

        "M30\n"
        f"Requests: {DIAG['m30_requests']}\n"
        f"Data OK: {DIAG['m30_ok']}\n"
        f"Hook Candidates: "
        f"{DIAG['hook_candidates']}\n"
        f"Structure Valid: "
        f"{DIAG['hook_structure_valid']}\n"
        f"Confirmed Hooks: "
        f"{DIAG['hook_confirmed_current']}\n\n"

        "M1 PIPELINE\n"
        f"Symbols Selected: "
        f"{DIAG['hook_confirmed_current']}\n"
        f"Requests: "
        f"{DIAG['m1_requests']}\n"
        f"Data OK: "
        f"{DIAG['m1_ok']}\n"
        f"123F Candidates: "
        f"{DIAG['m1_123f_candidates']}\n"
        f"123F SHORT: "
        f"{DIAG['m1_123f_short']}\n"
        f"123F LONG: "
        f"{DIAG['m1_123f_long']}\n\n"

        "TRADES\n"
        f"New Signals: "
        f"{DIAG['new_signals']}\n"
        f"Open Trades: "
        f"{count_open_trades()}\n"
        f"Closed: "
        f"{DIAG['closed']}\n"
        f"PnL: "
        f"{DIAG['pnl']:+.2f}%\n\n"

        "Mode: PAPER ONLY"
    )

    telegram_send(
        text
    )


# ============================================================
# MAIN
# ============================================================

def run_once():

    if not PAPER_ONLY:

        raise RuntimeError(
            "PAPER_ONLY must remain True"
        )

    init_db()

    print(
        "=" * 65
    )

    print(
        f"NDS M30 -> M1 LIVE SCANNER "
        f"V{VERSION}"
    )

    print(
        "PAPER ONLY"
    )

    print(
        "M30 Hook -> M1 123F -> F ENTRY -> "
        "86.4% FINAL TP"
    )

    print(
        "=" * 65
    )

    # --------------------------------------------------------
    # Existing trades
    # --------------------------------------------------------

    monitor_open_trades()

    # --------------------------------------------------------
    # Current-cycle confirmed Hooks
    #
    # IMPORTANT:
    # Every confirmed Hook goes directly to M1.
    # No global/single-symbol queue.
    # --------------------------------------------------------

    confirmed_hooks = {}

    for symbol in SYMBOLS:

        try:

            DIAG[
                "m30_requests"
            ] += 0

            m30 = fetch_candles(
                symbol,
                "30m",
                M30_COUNT
            )

            if m30.empty:

                DIAG[
                    "m30_short"
                ] += 1

                continue

            DIAG[
                "m30_requests"
            ] += 1

            DIAG[
                "m30_ok"
            ] += 1

            hook = latest_confirmed_hook(
                symbol,
                m30
            )

            if hook is None:
                continue

            confirmed_hooks[
                symbol
            ] = (
                hook,
                m30
            )

        except Exception as e:

            DIAG["errors"] += 1

            print(
                f"[M30 ERROR] "
                f"{symbol}: {e}"
            )

    DIAG[
        "hook_confirmed_current"
    ] = len(
        confirmed_hooks
    )

    DIAG[
        "m1_symbols_selected"
    ] = len(
        confirmed_hooks
    )

    print()
    print(
        f"M30 confirmed Hooks: "
        f"{len(confirmed_hooks)}"
    )

    # --------------------------------------------------------
    # M1 for EVERY confirmed Hook
    # --------------------------------------------------------

    for symbol, item in (
        confirmed_hooks.items()
    ):

        hook, m30 = item

        try:

            DIAG[
                "m1_requests"
            ] += 1

            m1 = fetch_candles(
                symbol,
                "1m",
                M1_COUNT
            )

            if m1.empty:

                DIAG[
                    "m1_short"
                ] += 1

                continue

            DIAG[
                "m1_ok"
            ] += 1

            pattern = detect_m1_123f(
                m1,
                hook
            )

            if pattern is None:
                continue

            setup = build_setup(
                m1,
                hook,
                pattern
            )

            if setup is None:
                continue

            if has_duplicate_signal(
                setup
            ):
                continue

            if (
                count_open_trades()
                >=
                MAX_OPEN_TRADES
            ):
                continue

            signal_id = insert_signal(
                setup
            )

            if not signal_id:
                continue

            DIAG[
                "new_signals"
            ] += 1

            # ------------------------------------------------
            # Chart
            # ------------------------------------------------

            try:

                image = create_chart(
                    symbol,
                    m30,
                    m1,
                    setup
                )

                DIAG[
                    "charts"
                ] += 1

            except Exception as e:

                image = None

                print(
                    f"[CHART ERROR] "
                    f"{symbol}: {e}"
                )

            # ------------------------------------------------
            # Telegram
            # ------------------------------------------------

            telegram_send(
                signal_message(
                    setup
                ),
                image
            )

        except Exception as e:

            DIAG[
                "errors"
            ] += 1

            print(
                f"[M1 ERROR] "
                f"{symbol}: {e}"
            )

            traceback.print_exc()

    # --------------------------------------------------------
    # Scanner run DB
    # --------------------------------------------------------

    con = db_connect()

    con.execute(
        """
        INSERT INTO scanner_runs (
            run_time,
            version,
            assets_scanned,
            new_signals,
            open_trades,
            errors
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            now_ms(),
            VERSION,
            len(SYMBOLS),
            DIAG["new_signals"],
            count_open_trades(),
            DIAG["errors"]
        )
    )

    con.commit()
    con.close()

    # --------------------------------------------------------
    # Diagnostic
    # --------------------------------------------------------

    print_diagnostic()

    telegram_report()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        run_once()

    except Exception as e:

        DIAG[
            "errors"
        ] += 1

        print(
            "\n[FATAL ERROR]"
        )

        print(
            str(e)
        )

        traceback.print_exc()
