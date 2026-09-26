# ============================================================
# NDS 5M LIVE SCANNER
# VERSION 2.2.0
# ============================================================
#
# FILE:
#   nds_5m_live_scanner.py
#
# DATABASE:
#   nds_5m_v20.db
#
# TIMEFRAME:
#   5M
#
# PAPER ONLY
#
# NDS:
#   Rally -> Hook 1 -> Hook 2 -> Rally / Break
#
# FEATURES:
#   40 Kraken Futures assets
#   Closed 5M candles
#   123 Hook structure
#   Hook 1
#   Hook 2
#   86.4% reference
#   Price symmetry
#   Time symmetry
#   Breakout confirmation
#   Structural SL / TP
#   RR filter
#   Best Candidate
#   New Signal
#   Telegram
#   Charts
#   Open trade monitoring
#   SQLite persistence
#
# ============================================================

import os
import io
import time
import sqlite3
import traceback
from datetime import datetime, timezone

import requests
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "2.2.0"

REAL_TRADING = False

TIMEFRAME = "5m"

DB_FILE = "nds_5m_v20.db"

CHART_DIR = "charts"

KRAKEN_BASE = (
    "https://futures.kraken.com/api/charts/v1/trade"
)

REQUEST_TIMEOUT = 20

LOOKBACK = 1000

# ------------------------------------------------------------
# Pivot
# ------------------------------------------------------------

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

# ------------------------------------------------------------
# Rally
# ------------------------------------------------------------

MIN_RALLY_PCT = 0.003

# ------------------------------------------------------------
# Hook
# ------------------------------------------------------------

MIN_HOOK_RETRACE = 0.30
MAX_HOOK_RETRACE = 0.90

# ------------------------------------------------------------
# 86.4%
# ------------------------------------------------------------

NDS_86 = 0.864

NDS_86_TOLERANCE = 0.10

# ------------------------------------------------------------
# Symmetry
# ------------------------------------------------------------

MIN_PRICE_SYMMETRY = 0.50
MAX_PRICE_SYMMETRY = 2.00

MIN_TIME_SYMMETRY = 0.50
MAX_TIME_SYMMETRY = 2.00

# ------------------------------------------------------------
# Breakout
# ------------------------------------------------------------

BREAK_BUFFER = 0.0005

FLAG_MAX_BARS = 12

# ------------------------------------------------------------
# Freshness
# ------------------------------------------------------------

MAX_SIGNAL_AGE_MINUTES = 15

# ------------------------------------------------------------
# Trading
# ------------------------------------------------------------

MAX_OPEN_TRADES = 3

MIN_RR = 1.0

# ------------------------------------------------------------
# Stop
# ------------------------------------------------------------

SL_BUFFER = 0.0015

MIN_SL_DISTANCE = 0.002

MAX_SL_DISTANCE = 0.08

# ------------------------------------------------------------
# Telegram
# ------------------------------------------------------------

TELEGRAM_BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("TELEGRAM_TOKEN")
)

TELEGRAM_CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or os.getenv("TELEGRAM_CHAT")
)


# ============================================================
# ASSETS
# ============================================================

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


# ============================================================
# BASIC HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def symbol_name(symbol):
    if symbol.startswith("PF_"):
        symbol = symbol[3:]

    if symbol.endswith("USD"):
        symbol = symbol[:-3]

    return symbol


def fmt_price(value):
    if value is None:
        return "-"

    value = float(value)

    if abs(value) >= 1000:
        return f"{value:,.2f}"

    if abs(value) >= 100:
        return f"{value:,.3f}"

    if abs(value) >= 1:
        return f"{value:,.4f}"

    if abs(value) >= 0.01:
        return f"{value:,.6f}"

    return f"{value:.8f}"


def pct(value):
    return f"{float(value) * 100:.2f}%"


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


# ============================================================
# TELEGRAM
# ============================================================

def telegram_ready():
    return bool(
        TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    )


def telegram_send(message):
    if not telegram_ready():
        print("[TELEGRAM] Secrets not configured")
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            print(
                "[TELEGRAM ERROR]",
                response.status_code,
                response.text[:300],
            )
            return False

        return True

    except Exception as exc:
        print(
            f"[TELEGRAM ERROR] {exc}"
        )
        return False


def telegram_photo(
    image_bytes,
    caption,
):
    if not telegram_ready():
        print("[TELEGRAM] Secrets not configured")
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    files = {
        "photo": (
            "nds_5m.png",
            image_bytes,
            "image/png",
        )
    }

    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "caption": caption,
        "parse_mode": "HTML",
    }

    try:
        response = requests.post(
            url,
            files=files,
            data=data,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            print(
                "[TELEGRAM PHOTO ERROR]",
                response.status_code,
                response.text[:300],
            )
            return False

        return True

    except Exception as exc:
        print(
            f"[TELEGRAM PHOTO ERROR] {exc}"
        )
        return False


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT NOT NULL,

            direction TEXT NOT NULL,

            created_at TEXT NOT NULL,

            entry REAL NOT NULL,

            sl REAL NOT NULL,

            tp REAL NOT NULL,

            rr REAL,

            status TEXT NOT NULL DEFAULT 'OPEN',

            close_time TEXT,

            close_price REAL,

            pnl_pct REAL,

            rally_pct REAL,

            hook1_retrace REAL,

            hook2_retrace REAL,

            price_symmetry REAL,

            time_symmetry REAL,

            score REAL,

            signal_key TEXT UNIQUE
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scanner_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            run_time TEXT NOT NULL,

            configured INTEGER,

            fetched INTEGER,

            analyzed INTEGER,

            data_errors INTEGER,

            analysis_errors INTEGER,

            new_signals INTEGER,

            candidates INTEGER
        )
        """
    )

    conn.commit()
    conn.close()

    print("[DB] Schema OK")


def count_open_trades():
    conn = db()

    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM signals
        WHERE status = 'OPEN'
        """
    ).fetchone()

    conn.close()

    return int(row["n"])


def signal_exists(signal_key):
    conn = db()

    row = conn.execute(
        """
        SELECT id
        FROM signals
        WHERE signal_key = ?
        LIMIT 1
        """,
        (signal_key,),
    ).fetchone()

    conn.close()

    return row is not None


def insert_signal(signal):
    conn = db()

    try:
        conn.execute(
            """
            INSERT INTO signals (
                symbol,
                direction,
                created_at,
                entry,
                sl,
                tp,
                rr,
                status,
                rally_pct,
                hook1_retrace,
                hook2_retrace,
                price_symmetry,
                time_symmetry,
                score,
                signal_key
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                'OPEN',
                ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                signal["symbol"],
                signal["direction"],
                signal["created_at"],
                signal["entry"],
                signal["sl"],
                signal["tp"],
                signal["rr"],
                signal["rally_pct"],
                signal["hook1_retrace"],
                signal["hook2_retrace"],
                signal["price_symmetry"],
                signal["time_symmetry"],
                signal["score"],
                signal["signal_key"],
            ),
        )

        conn.commit()
        conn.close()

        return True

    except sqlite3.IntegrityError:
        conn.close()
        return False

    except Exception:
        conn.close()
        raise


# ============================================================
# KRAKEN DATA
# ============================================================

def parse_kraken_response(data):
    """
    Supports common Kraken Futures candle response formats.
    """

    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    for key in (
        "candles",
        "data",
        "results",
    ):
        value = data.get(key)

        if isinstance(value, list):
            return value

        if isinstance(value, dict):
            for nested_key in (
                "candles",
                "data",
                "results",
            ):
                nested = value.get(
                    nested_key
                )

                if isinstance(
                    nested,
                    list,
                ):
                    return nested

    return []


def candle_from_item(item):
    try:

        if isinstance(item, dict):

            ts = (
                item.get("time")
                if item.get("time") is not None
                else item.get("timestamp")
            )

            if ts is None:
                ts = item.get("ts")

            op = (
                item.get("open")
                if item.get("open") is not None
                else item.get("o")
            )

            hi = (
                item.get("high")
                if item.get("high") is not None
                else item.get("h")
            )

            lo = (
                item.get("low")
                if item.get("low") is not None
                else item.get("l")
            )

            cl = (
                item.get("close")
                if item.get("close") is not None
                else item.get("c")
            )

            vol = (
                item.get("volume")
                if item.get("volume") is not None
                else item.get("v", 0)
            )

        else:

            if len(item) < 5:
                return None

            ts = item[0]
            op = item[1]
            hi = item[2]
            lo = item[3]
            cl = item[4]

            vol = (
                item[5]
                if len(item) > 5
                else 0
            )

        if ts is None:
            return None

        ts = float(ts)

        # Handle milliseconds.
        if ts > 10_000_000_000:
            ts /= 1000.0

        return [
            ts,
            float(op),
            float(hi),
            float(lo),
            float(cl),
            float(vol or 0),
        ]

    except Exception:
        return None


def fetch_candles(symbol):

    url = (
        f"{KRAKEN_BASE}/"
        f"{symbol}/"
        f"{TIMEFRAME}"
    )

    try:

        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            print(
                f"[DATA ERROR] {symbol}: "
                f"HTTP {response.status_code}"
            )

            return None

        data = response.json()

        raw = parse_kraken_response(
            data
        )

        if not raw:

            print(
                f"[DATA ERROR] {symbol}: "
                "no candle array"
            )

            return None

        rows = []

        for item in raw:

            row = candle_from_item(
                item
            )

            if row is not None:
                rows.append(row)

        if not rows:

            print(
                f"[DATA ERROR] {symbol}: "
                "no valid candles"
            )

            return None

        df = pd.DataFrame(
            rows,
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
            df.drop_duplicates(
                subset=["timestamp"]
            )
            .sort_values("timestamp")
            .reset_index(drop=True)
        )

        df = df.tail(
            LOOKBACK
        ).reset_index(
            drop=True
        )

        # ----------------------------------------------------
        # Remove current unfinished 5M candle.
        # ----------------------------------------------------

        current_time = time.time()

        candle_seconds = 300

        df = df[
            (
                df["timestamp"]
                + candle_seconds
            )
            <= current_time
        ].reset_index(
            drop=True
        )

        if len(df) < 100:

            print(
                f"[DATA ERROR] {symbol}: "
                f"only {len(df)} closed candles"
            )

            return None

        print(
            f"[DATA OK] {symbol}: "
            f"{len(df)} closed 5M candles"
        )

        return df

    except Exception as exc:

        print(
            f"[DATA ERROR] {symbol}: "
            f"{exc}"
        )

        return None


# ============================================================
# PIVOTS
# ============================================================

def detect_pivots(df):

    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()

    pivots = []

    start = PIVOT_LEFT

    end = (
        len(df)
        - PIVOT_RIGHT
    )

    for i in range(
        start,
        end,
    ):

        left_high = highs[
            i - PIVOT_LEFT:i
        ]

        right_high = highs[
            i + 1:
            i + PIVOT_RIGHT + 1
        ]

        left_low = lows[
            i - PIVOT_LEFT:i
        ]

        right_low = lows[
            i + 1:
            i + PIVOT_RIGHT + 1
        ]

        is_high = (
            highs[i]
            >= max(left_high)
            and
            highs[i]
            >= max(right_high)
        )

        is_low = (
            lows[i]
            <= min(left_low)
            and
            lows[i]
            <= min(right_low)
        )

        if is_high and not is_low:

            pivots.append(
                {
                    "index": i,
                    "price": float(
                        highs[i]
                    ),
                    "type": "H",
                }
            )

        elif is_low and not is_high:

            pivots.append(
                {
                    "index": i,
                    "price": float(
                        lows[i]
                    ),
                    "type": "L",
                }
            )

    # --------------------------------------------------------
    # Remove consecutive same-type pivots.
    # --------------------------------------------------------

    cleaned = []

    for p in pivots:

        if not cleaned:

            cleaned.append(p)
            continue

        previous = cleaned[-1]

        if p["type"] != previous["type"]:

            cleaned.append(p)
            continue

        if p["type"] == "H":

            if p["price"] > previous["price"]:
                cleaned[-1] = p

        else:

            if p["price"] < previous["price"]:
                cleaned[-1] = p

    return cleaned


# ============================================================
# 123
# ============================================================

def valid_123(
    p1,
    p2,
    p3,
    direction,
):

    if direction == "LONG":

        return (
            p1["type"] == "L"
            and
            p2["type"] == "H"
            and
            p3["type"] == "L"
            and
            p3["price"] > p1["price"]
        )

    return (
        p1["type"] == "H"
        and
        p2["type"] == "L"
        and
        p3["type"] == "H"
        and
        p3["price"] < p1["price"]
    )


def retracement(
    p1,
    p2,
    p3,
    direction,
):

    if direction == "LONG":

        move = (
            p2["price"]
            - p1["price"]
        )

        correction = (
            p2["price"]
            - p3["price"]
        )

    else:

        move = (
            p1["price"]
            - p2["price"]
        )

        correction = (
            p3["price"]
            - p2["price"]
        )

    if move <= 0:
        return None

    return correction / move


# ============================================================
# RALLY
# ============================================================

def rally_direction(
    p1,
    p2,
):

    if (
        p1["type"] == "L"
        and
        p2["type"] == "H"
    ):
        return "LONG"

    if (
        p1["type"] == "H"
        and
        p2["type"] == "L"
    ):
        return "SHORT"

    return None


def rally_pct(
    p1,
    p2,
):

    if p1["price"] == 0:
        return 0.0

    return abs(
        p2["price"]
        -
        p1["price"]
    ) / p1["price"]


# ============================================================
# SYMMETRY
# ============================================================

def symmetry(
    rally_start,
    rally_end,
    hook1_p3,
    hook2_p3,
):

    rally_price = abs(
        rally_end["price"]
        -
        rally_start["price"]
    )

    second_price = abs(
        hook2_p3["price"]
        -
        hook1_p3["price"]
    )

    rally_time = abs(
        rally_end["index"]
        -
        rally_start["index"]
    )

    second_time = abs(
        hook2_p3["index"]
        -
        hook1_p3["index"]
    )

    if rally_price <= 0:
        price_sym = 999.0
    else:
        price_sym = (
            second_price
            /
            rally_price
        )

    if rally_time <= 0:
        time_sym = 999.0
    else:
        time_sym = (
            second_time
            /
            rally_time
        )

    return (
        price_sym,
        time_sym,
    )


def symmetry_score(value):

    if not (
        MIN_PRICE_SYMMETRY
        <= value
        <= MAX_PRICE_SYMMETRY
    ):
        return 0.0

    return clamp(
        1.0
        -
        abs(value - 1.0)
    )


# ============================================================
# 86.4
# ============================================================

def score_864(retrace_value):

    if retrace_value is None:
        return 0.0

    distance = abs(
        retrace_value
        -
        NDS_86
    )

    return clamp(
        1.0
        -
        (
            distance
            /
            NDS_86_TOLERANCE
        )
    )


# ============================================================
# FIND HOOK PAIR
# ============================================================

def find_hook_pair(
    pivots,
    direction,
):

    structures = []

    # Need:
    #
    # R1 R2
    # H1-1 H1-2 H1-3
    # H2-1 H2-2 H2-3
    #
    # = 8 pivots

    if len(pivots) < 8:
        return structures

    for i in range(
        0,
        len(pivots) - 7,
    ):

        r1 = pivots[i]
        r2 = pivots[i + 1]

        if rally_direction(
            r1,
            r2,
        ) != direction:
            continue

        r_pct = rally_pct(
            r1,
            r2,
        )

        if r_pct < MIN_RALLY_PCT:
            continue

        h1 = pivots[
            i + 2:
            i + 5
        ]

        h2 = pivots[
            i + 5:
            i + 8
        ]

        if (
            len(h1) != 3
            or
            len(h2) != 3
        ):
            continue

        if not valid_123(
            h1[0],
            h1[1],
            h1[2],
            direction,
        ):
            continue

        if not valid_123(
            h2[0],
            h2[1],
            h2[2],
            direction,
        ):
            continue

        h1_ret = retracement(
            h1[0],
            h1[1],
            h1[2],
            direction,
        )

        h2_ret = retracement(
            h2[0],
            h2[1],
            h2[2],
            direction,
        )

        if (
            h1_ret is None
            or
            h2_ret is None
        ):
            continue

        if not (
            MIN_HOOK_RETRACE
            <= h1_ret
            <= MAX_HOOK_RETRACE
        ):
            continue

        if not (
            MIN_HOOK_RETRACE
            <= h2_ret
            <= MAX_HOOK_RETRACE
        ):
            continue

        price_sym, time_sym = symmetry(
            r1,
            r2,
            h1[2],
            h2[2],
        )

        price_score = (
            symmetry_score(
                price_sym
            )
        )

        time_score = (
            symmetry_score(
                time_sym
            )
        )

        h1_864 = score_864(
            h1_ret
        )

        h2_864 = score_864(
            h2_ret
        )

        score_86 = (
            h1_864 * 0.35
            +
            h2_864 * 0.65
        )

        # Hook 2 being smaller than Hook 1
        # is treated as a quality factor.
        if h2_ret <= h1_ret:
            hook_quality = 1.0
        else:
            hook_quality = clamp(
                1.0
                -
                (
                    h2_ret
                    -
                    h1_ret
                )
            )

        latest_index = h2[2]["index"]

        age_bars = (
            len(pivots)
            -
            1
            -
            latest_index
        )

        recency = clamp(
            1.0
            -
            age_bars / 30.0
        )

        total_score = (
            0.20
            +
            0.15 * hook_quality
            +
            0.20 * score_86
            +
            0.15 * price_score
            +
            0.15 * time_score
            +
            0.15 * recency
        )

        structure = {
            "direction": direction,

            "rally_start": r1,
            "rally_end": r2,

            "hook1": h1,
            "hook2": h2,

            "rally_pct": r_pct,

            "hook1_retrace": h1_ret,
            "hook2_retrace": h2_ret,

            "price_symmetry": price_sym,
            "time_symmetry": time_sym,

            "score_864": score_86,

            "score": (
                total_score * 100.0
            ),

            "recency_score": recency,

            "age_bars": age_bars,

            "breakout": None,

            "levels": None,

            "fresh": False,
        }

        structures.append(
            structure
        )

    return structures


# ============================================================
# BREAKOUT
# ============================================================

def find_breakout(
    df,
    structure,
):

    direction = structure[
        "direction"
    ]

    h2 = structure[
        "hook2"
    ]

    trigger = h2[1]

    trigger_price = (
        trigger["price"]
    )

    start = (
        trigger["index"]
        + 1
    )

    if start >= len(df):
        return None

    end = min(
        start + FLAG_MAX_BARS,
        len(df),
    )

    for i in range(
        start,
        end,
    ):

        close = float(
            df.iloc[i]["close"]
        )

        if direction == "LONG":

            level = (
                trigger_price
                *
                (
                    1.0
                    +
                    BREAK_BUFFER
                )
            )

            if close > level:

                return {
                    "index": i,
                    "price": close,
                    "trigger": trigger_price,
                    "timestamp": float(
                        df.iloc[i]["timestamp"]
                    ),
                }

        else:

            level = (
                trigger_price
                *
                (
                    1.0
                    -
                    BREAK_BUFFER
                )
            )

            if close < level:

                return {
                    "index": i,
                    "price": close,
                    "trigger": trigger_price,
                    "timestamp": float(
                        df.iloc[i]["timestamp"]
                    ),
                }

    return None


# ============================================================
# LEVELS
# ============================================================

def calculate_levels(
    structure,
    breakout,
):

    direction = structure[
        "direction"
    ]

    entry = float(
        breakout["price"]
    )

    h1 = structure[
        "hook1"
    ]

    h2 = structure[
        "hook2"
    ]

    r1 = structure[
        "rally_start"
    ]

    r2 = structure[
        "rally_end"
    ]

    if direction == "LONG":

        structural_low = min(
            h1[2]["price"],
            h2[2]["price"],
        )

        sl = (
            structural_low
            *
            (
                1.0
                -
                SL_BUFFER
            )
        )

        highs = [
            r2["price"],
            h1[1]["price"],
            h2[1]["price"],
        ]

        valid_highs = [
            x
            for x in highs
            if x > entry
        ]

        if valid_highs:

            tp = min(
                valid_highs
            )

        else:

            rally_size = abs(
                r2["price"]
                -
                r1["price"]
            )

            tp = (
                entry
                +
                rally_size
            )

        sl_distance = (
            entry - sl
        ) / entry

        tp_distance = (
            tp - entry
        ) / entry

    else:

        structural_high = max(
            h1[2]["price"],
            h2[2]["price"],
        )

        sl = (
            structural_high
            *
            (
                1.0
                +
                SL_BUFFER
            )
        )

        lows = [
            r2["price"],
            h1[1]["price"],
            h2[1]["price"],
        ]

        valid_lows = [
            x
            for x in lows
            if x < entry
        ]

        if valid_lows:

            tp = max(
                valid_lows
            )

        else:

            rally_size = abs(
                r2["price"]
                -
                r1["price"]
            )

            tp = (
                entry
                -
                rally_size
            )

        sl_distance = (
            sl - entry
        ) / entry

        tp_distance = (
            entry - tp
        ) / entry

    if sl_distance <= 0:
        return None

    if tp_distance <= 0:
        return None

    if not (
        MIN_SL_DISTANCE
        <= sl_distance
        <= MAX_SL_DISTANCE
    ):
        return None

    rr = (
        tp_distance
        /
        sl_distance
    )

    if rr < MIN_RR:
        return None

    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
    }


# ============================================================
# ANALYSIS
# ============================================================

def analyze_asset(
    symbol,
    df,
):

    pivots = detect_pivots(
        df
    )

    structures = []

    for direction in (
        "LONG",
        "SHORT",
    ):

        structures.extend(
            find_hook_pair(
                pivots,
                direction,
            )
        )

    if not structures:

        return {
            "symbol": symbol,
            "pivots": pivots,
            "structures": [],
            "signals": [],
            "best": None,
        }

    # --------------------------------------------------------
    # Analyze breakout
    # --------------------------------------------------------

    for s in structures:

        breakout = find_breakout(
            df,
            s,
        )

        s["breakout"] = breakout

        if breakout is None:
            continue

        age_minutes = (
            time.time()
            -
            breakout["timestamp"]
        ) / 60.0

        s["age_minutes"] = (
            age_minutes
        )

        levels = calculate_levels(
            s,
            breakout,
        )

        s["levels"] = levels

        s["fresh"] = bool(
            levels is not None
            and
            age_minutes
            <= MAX_SIGNAL_AGE_MINUTES
        )

    structures.sort(
        key=lambda x: (
            x["score"],
            x["recency_score"],
        ),
        reverse=True,
    )

    signals = [
        s
        for s in structures
        if s["fresh"]
        and s["levels"] is not None
    ]

    best = structures[0]

    best["symbol"] = symbol

    return {
        "symbol": symbol,
        "pivots": pivots,
        "structures": structures,
        "signals": signals,
        "best": best,
    }


# ============================================================
# SIGNAL
# ============================================================

def make_signal(
    symbol,
    structure,
):

    levels = structure[
        "levels"
    ]

    breakout = structure[
        "breakout"
    ]

    signal_key = (
        f"{symbol}|"
        f"{structure['direction']}|"
        f"{int(breakout['timestamp'])}|"
        f"{breakout['trigger']:.12f}"
    )

    return {
        "symbol": symbol,

        "direction": structure[
            "direction"
        ],

        "created_at": utc_now().isoformat(),

        "entry": levels["entry"],

        "sl": levels["sl"],

        "tp": levels["tp"],

        "rr": levels["rr"],

        "rally_pct": structure[
            "rally_pct"
        ],

        "hook1_retrace": structure[
            "hook1_retrace"
        ],

        "hook2_retrace": structure[
            "hook2_retrace"
        ],

        "price_symmetry": structure[
            "price_symmetry"
        ],

        "time_symmetry": structure[
            "time_symmetry"
        ],

        "score": structure[
            "score"
        ],

        "signal_key": signal_key,
    }


# ============================================================
# CHART
# ============================================================

def create_chart(
    symbol,
    df,
    structure,
    title,
):

    try:

        os.makedirs(
            CHART_DIR,
            exist_ok=True,
        )

        indices = [
            structure[
                "rally_start"
            ]["index"],

            structure[
                "rally_end"
            ]["index"],
        ]

        for p in structure[
            "hook1"
        ]:

            indices.append(
                p["index"]
            )

        for p in structure[
            "hook2"
        ]:

            indices.append(
                p["index"]
            )

        breakout = structure.get(
            "breakout"
        )

        if breakout:
            indices.append(
                breakout["index"]
            )

        left = max(
            min(indices) - 30,
            0,
        )

        right = min(
            max(indices) + 30,
            len(df) - 1,
        )

        view = df.iloc[
            left:
            right + 1
        ]

        x = np.arange(
            len(view)
        )

        fig, ax = plt.subplots(
            figsize=(15, 8)
        )

        ax.plot(
            x,
            view["close"].values,
            linewidth=1.4,
            label="Close",
        )

        def point(
            p,
            label,
            marker,
        ):

            lx = (
                p["index"]
                -
                left
            )

            if (
                0
                <= lx
                <
                len(view)
            ):

                ax.scatter(
                    lx,
                    p["price"],
                    s=70,
                    marker=marker,
                    zorder=5,
                )

                ax.annotate(
                    label,
                    (
                        lx,
                        p["price"],
                    ),
                    xytext=(
                        0,
                        10,
                    ),
                    textcoords=(
                        "offset points"
                    ),
                    ha="center",
                    fontsize=9,
                )

        point(
            structure[
                "rally_start"
            ],
            "R1",
            "o",
        )

        point(
            structure[
                "rally_end"
            ],
            "R2",
            "o",
        )

        for n, p in enumerate(
            structure["hook1"],
            1,
        ):

            point(
                p,
                f"H1-{n}",
                "^",
            )

        for n, p in enumerate(
            structure["hook2"],
            1,
        ):

            point(
                p,
                f"H2-{n}",
                "v",
            )

        trigger = structure[
            "hook2"
        ][1]

        ax.axhline(
            trigger["price"],
            linestyle="--",
            linewidth=1,
            label="NDS Trigger",
        )

        # ----------------------------------------------------
        # 86.4 reference
        # ----------------------------------------------------

        h2 = structure[
            "hook2"
        ]

        if structure[
            "direction"
        ] == "LONG":

            move = (
                h2[1]["price"]
                -
                h2[0]["price"]
            )

            level_864 = (
                h2[1]["price"]
                -
                move
                *
                NDS_86
            )

        else:

            move = (
                h2[0]["price"]
                -
                h2[1]["price"]
            )

            level_864 = (
                h2[1]["price"]
                +
                move
                *
                NDS_86
            )

        ax.axhline(
            level_864,
            linestyle=":",
            linewidth=1,
            label="86.4%",
        )

        # ----------------------------------------------------
        # Levels
        # ----------------------------------------------------

        levels = structure.get(
            "levels"
        )

        if levels:

            ax.axhline(
                levels["entry"],
                linestyle="-.",
                linewidth=1.2,
                label="Entry",
            )

            ax.axhline(
                levels["sl"],
                linestyle="--",
                linewidth=1.2,
                label="SL",
            )

            ax.axhline(
                levels["tp"],
                linestyle="--",
                linewidth=1.2,
                label="TP",
            )

        # ----------------------------------------------------
        # Breakout
        # ----------------------------------------------------

        if breakout:

            bx = (
                breakout["index"]
                -
                left
            )

            by = breakout[
                "price"
            ]

            ax.scatter(
                bx,
                by,
                s=140,
                marker="*",
                zorder=10,
                label="Breakout",
            )

        ax.set_title(
            f"{title} | "
            f"{symbol_name(symbol)} | "
            f"{structure['direction']} | "
            f"Score "
            f"{structure['score']:.1f}"
        )

        ax.set_xlabel(
            "5M candles"
        )

        ax.set_ylabel(
            "Price"
        )

        ax.grid(
            alpha=0.25
        )

        ax.legend(
            fontsize=8,
            loc="best",
        )

        fig.tight_layout()

        filename = (
            f"{symbol_name(symbol)}_"
            f"{structure['direction']}_"
            f"{int(time.time())}.png"
        )

        path = os.path.join(
            CHART_DIR,
            filename,
        )

        fig.savefig(
            path,
            dpi=140,
            bbox_inches="tight",
        )

        buffer = io.BytesIO()

        fig.savefig(
            buffer,
            format="png",
            dpi=140,
            bbox_inches="tight",
        )

        buffer.seek(0)

        image_bytes = (
            buffer.getvalue()
        )

        plt.close(
            fig
        )

        return (
            path,
            image_bytes,
        )

    except Exception as exc:

        print(
            f"[CHART ERROR] "
            f"{symbol}: {exc}"
        )

        try:
            plt.close("all")
        except Exception:
            pass

        return (
            None,
            None,
        )


# ============================================================
# SIGNAL TELEGRAM
# ============================================================

def send_signal_telegram(
    signal,
    structure,
    chart_bytes,
):

    emoji = (
        "🟢"
        if signal["direction"] == "LONG"
        else "🔴"
    )

    caption = (
        f"{emoji} "
        f"<b>NDS 5M NEW SIGNAL</b>\n\n"
        f"<b>{symbol_name(signal['symbol'])}</b> "
        f"{signal['direction']}\n\n"
        f"Entry: "
        f"<b>{fmt_price(signal['entry'])}</b>\n"
        f"SL: {fmt_price(signal['sl'])}\n"
        f"TP: {fmt_price(signal['tp'])}\n"
        f"RR: <b>{signal['rr']:.2f}</b>\n\n"
        f"Rally: {pct(signal['rally_pct'])}\n"
        f"Hook 1: {pct(signal['hook1_retrace'])}\n"
        f"Hook 2: {pct(signal['hook2_retrace'])}\n"
        f"86.4: "
        f"{structure['score_864'] * 100:.0f}/100\n"
        f"Price symmetry: "
        f"{structure['price_symmetry']:.2f}\n"
        f"Time symmetry: "
        f"{structure['time_symmetry']:.2f}\n"
        f"Score: "
        f"<b>{structure['score']:.1f}/100</b>\n\n"
        f"⏱ 5M\n"
        f"🧪 PAPER ONLY"
    )

    if chart_bytes:

        telegram_photo(
            chart_bytes,
            caption,
        )

    else:

        telegram_send(
            caption
        )


# ============================================================
# BEST CANDIDATE TELEGRAM
# ============================================================

def send_candidate_telegram(
    candidate,
    chart_bytes,
):

    emoji = (
        "🟢"
        if candidate["direction"] == "LONG"
        else "🔴"
    )

    breakout = candidate.get(
        "breakout"
    )

    if breakout:

        status = "BREAKOUT FOUND"

    else:

        status = "WAITING FOR BREAK"

    message = (
        f"⭐ "
        f"<b>NDS 5M BEST CANDIDATE</b>\n\n"
        f"{emoji} "
        f"<b>{symbol_name(candidate['symbol'])}</b> "
        f"{candidate['direction']}\n"
        f"Status: <b>{status}</b>\n\n"
        f"Rally: "
        f"{pct(candidate['rally_pct'])}\n"
        f"Hook 1: "
        f"{pct(candidate['hook1_retrace'])}\n"
        f"Hook 2: "
        f"{pct(candidate['hook2_retrace'])}\n"
        f"86.4: "
        f"{candidate['score_864'] * 100:.0f}/100\n"
        f"Price symmetry: "
        f"{candidate['price_symmetry']:.2f}\n"
        f"Time symmetry: "
        f"{candidate['time_symmetry']:.2f}\n"
        f"Score: "
        f"<b>{candidate['score']:.1f}/100</b>\n\n"
        f"⚠️ Candidate only\n"
        f"❌ No trade opened\n"
        f"⏱ 5M"
    )

    if chart_bytes:

        telegram_photo(
            chart_bytes,
            message,
        )

    else:

        telegram_send(
            message
        )


# ============================================================
# OPEN TRADE MONITOR
# ============================================================

def monitor_open_trades(
    data_map,
):

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY id ASC
        """
    ).fetchall()

    print(
        f"[MONITOR] "
        f"{len(rows)} open trades"
    )

    closed = []

    for row in rows:

        symbol = row[
            "symbol"
        ]

        df = data_map.get(
            symbol
        )

        if (
            df is None
            or
            len(df) == 0
        ):

            print(
                f"[MONITOR SKIP] "
                f"{symbol}: no data"
            )

            continue

        candle = df.iloc[-1]

        high = float(
            candle["high"]
        )

        low = float(
            candle["low"]
        )

        entry = float(
            row["entry"]
        )

        sl = float(
            row["sl"]
        )

        tp = float(
            row["tp"]
        )

        direction = row[
            "direction"
        ]

        reason = None

        close_price = None

        pnl = None

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if direction == "LONG":

            if low <= sl:

                reason = "SL"
                close_price = sl

            elif high >= tp:

                reason = "TP"
                close_price = tp

            if reason:

                pnl = (
                    close_price
                    -
                    entry
                ) / entry

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        else:

            if high >= sl:

                reason = "SL"
                close_price = sl

            elif low <= tp:

                reason = "TP"
                close_price = tp

            if reason:

                pnl = (
                    entry
                    -
                    close_price
                ) / entry

        if reason:

            close_time = (
                utc_now().isoformat()
            )

            conn.execute(
                """
                UPDATE signals
                SET
                    status = 'CLOSED',
                    close_time = ?,
                    close_price = ?,
                    pnl_pct = ?
                WHERE id = ?
                """,
                (
                    close_time,
                    close_price,
                    pnl,
                    row["id"],
                ),
            )

            closed.append(
                {
                    "row": row,
                    "reason": reason,
                    "price": close_price,
                    "pnl": pnl,
                }
            )

    conn.commit()
    conn.close()

    # --------------------------------------------------------
    # Alerts
    # --------------------------------------------------------

    for item in closed:

        row = item["row"]

        emoji = (
            "🟢"
            if row["direction"] == "LONG"
            else "🔴"
        )

        message = (
            f"{emoji} "
            f"<b>NDS 5M CLOSED</b>\n\n"
            f"<b>{symbol_name(row['symbol'])}</b> "
            f"{row['direction']}\n"
            f"Reason: "
            f"<b>{item['reason']}</b>\n"
            f"Entry: "
            f"{fmt_price(row['entry'])}\n"
            f"Close: "
            f"{fmt_price(item['price'])}\n"
            f"P/L: "
            f"<b>{pct(item['pnl'])}</b>"
        )

        telegram_send(
            message
        )

    return len(closed)


# ============================================================
# STATISTICS
# ============================================================

def get_stats():

    conn = db()

    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,

            SUM(
                CASE
                    WHEN status = 'CLOSED'
                    THEN 1
                    ELSE 0
                END
            ) AS closed,

            SUM(
                CASE
                    WHEN status = 'CLOSED'
                    AND pnl_pct > 0
                    THEN 1
                    ELSE 0
                END
            ) AS wins,

            SUM(
                CASE
                    WHEN status = 'CLOSED'
                    AND pnl_pct <= 0
                    THEN 1
                    ELSE 0
                END
            ) AS losses,

            COALESCE(
                SUM(
                    CASE
                        WHEN status = 'CLOSED'
                        THEN pnl_pct
                        ELSE 0
                    END
                ),
                0
            ) AS pnl

        FROM signals
        """
    ).fetchone()

    conn.close()

    closed = int(
        row["closed"] or 0
    )

    wins = int(
        row["wins"] or 0
    )

    losses = int(
        row["losses"] or 0
    )

    pnl = float(
        row["pnl"] or 0
    )

    win_rate = (
        wins / closed * 100.0
        if closed > 0
        else 0.0
    )

    return {
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "pnl": pnl,
        "win_rate": win_rate,
    }


# ============================================================
# SAVE RUN
# ============================================================

def save_run(
    configured,
    fetched,
    analyzed,
    data_errors,
    analysis_errors,
    new_signals,
    candidates,
):

    conn = db()

    conn.execute(
        """
        INSERT INTO scanner_runs (
            run_time,
            configured,
            fetched,
            analyzed,
            data_errors,
            analysis_errors,
            new_signals,
            candidates
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now().isoformat(),
            configured,
            fetched,
            analyzed,
            data_errors,
            analysis_errors,
            new_signals,
            candidates,
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# REPORT
# ============================================================

def build_report(
    configured,
    fetched,
    analyzed,
    data_errors,
    analysis_errors,
    new_signals,
    best,
):

    open_trades = (
        count_open_trades()
    )

    stats = get_stats()

    lines = [
        "📊 <b>NDS 5M REPORT</b>",
        "",
        f"Configured: <b>{configured}</b>",
        f"Fetched: <b>{fetched}</b>",
        f"Analyzed: <b>{analyzed}</b>",
        f"Data errors: {data_errors}",
        f"Analysis errors: {analysis_errors}",
        "",
        f"New signals: <b>{new_signals}</b>",
        f"Open trades: <b>{open_trades}</b>",
        "",
        f"Closed: {stats['closed']}",
        f"Wins: {stats['wins']}",
        f"Losses: {stats['losses']}",
        f"Win rate: {stats['win_rate']:.2f}%",
        f"Total P/L: "
        f"<b>{stats['pnl'] * 100:+.2f}%</b>",
    ]

    if best:

        lines.extend(
            [
                "",
                "⭐ <b>BEST CANDIDATE</b>",
                (
                    f"{symbol_name(best['symbol'])} "
                    f"{best['direction']}"
                ),
                (
                    f"Score: "
                    f"<b>{best['score']:.1f}/100</b>"
                ),
                (
                    f"Rally: "
                    f"{pct(best['rally_pct'])}"
                ),
                (
                    f"Hook 1: "
                    f"{pct(best['hook1_retrace'])}"
                ),
                (
                    f"Hook 2: "
                    f"{pct(best['hook2_retrace'])}"
                ),
                (
                    f"86.4: "
                    f"{best['score_864'] * 100:.0f}/100"
                ),
            ]
        )

    else:

        lines.extend(
            [
                "",
                "⭐ Best Candidate: <b>NONE</b>",
            ]
        )

    lines.extend(
        [
            "",
            "🧪 PAPER ONLY",
            f"Version: {VERSION}",
        ]
    )

    return "\n".join(
        lines
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)

    print(
        f"NDS 5M LIVE SCANNER v{VERSION}"
    )

    print("=" * 70)

    print(
        f"PAPER ONLY: "
        f"{REAL_TRADING is False}"
    )

    print(
        f"TIMEFRAME: {TIMEFRAME}"
    )

    print(
        f"CONFIGURED ASSETS: "
        f"{len(ASSETS)}"
    )

    print(
        "MODEL: "
        "RALLY -> HOOK 1 -> HOOK 2 -> BREAK"
    )

    print("=" * 70)

    if REAL_TRADING:

        raise RuntimeError(
            "REAL_TRADING MUST BE FALSE"
        )

    init_db()

    os.makedirs(
        CHART_DIR,
        exist_ok=True,
    )

    data_map = {}

    analyses = {}

    best_candidates = []

    new_signals = []

    configured = len(
        ASSETS
    )

    fetched = 0

    analyzed = 0

    data_errors = 0

    analysis_errors = 0

    # ========================================================
    # SCAN ALL 40
    # ========================================================

    for number, symbol in enumerate(
        ASSETS,
        start=1,
    ):

        print()
        print(
            f"[SCAN {number}/{configured}] "
            f"{symbol}"
        )

        try:

            df = fetch_candles(
                symbol
            )

        except Exception as exc:

            data_errors += 1

            print(
                f"[DATA EXCEPTION] "
                f"{symbol}: {exc}"
            )

            traceback.print_exc()

            continue

        if df is None:

            data_errors += 1

            continue

        fetched += 1

        data_map[
            symbol
        ] = df

        try:

            result = analyze_asset(
                symbol,
                df,
            )

            analyses[
                symbol
            ] = result

            analyzed += 1

            best = result.get(
                "best"
            )

            if best:

                best_candidates.append(
                    best
                )

                print(
                    f"[NDS] "
                    f"{symbol} "
                    f"{best['direction']} "
                    f"Rally="
                    f"{pct(best['rally_pct'])} "
                    f"H1="
                    f"{pct(best['hook1_retrace'])} "
                    f"H2="
                    f"{pct(best['hook2_retrace'])} "
                    f"Score="
                    f"{best['score']:.1f}"
                )

                if best.get(
                    "breakout"
                ):

                    print(
                        f"[BREAKOUT] "
                        f"{symbol} "
                        f"{best['direction']} "
                        f"age="
                        f"{best.get('age_minutes', 0):.1f}m"
                    )

            else:

                print(
                    f"[NO STRUCTURE] "
                    f"{symbol}"
                )

        except Exception as exc:

            analysis_errors += 1

            print(
                f"[ANALYSIS ERROR] "
                f"{symbol}: {exc}"
            )

            traceback.print_exc()

    # ========================================================
    # STATUS AFTER SCAN
    # ========================================================

    print()
    print("=" * 70)

    print(
        "SCAN SUMMARY"
    )

    print(
        f"Configured: {configured}"
    )

    print(
        f"Fetched: {fetched}"
    )

    print(
        f"Analyzed: {analyzed}"
    )

    print(
        f"Data errors: {data_errors}"
    )

    print(
        f"Analysis errors: {analysis_errors}"
    )

    print("=" * 70)

    # ========================================================
    # MONITOR EXISTING TRADES
    # ========================================================

    try:

        monitor_open_trades(
            data_map
        )

    except Exception as exc:

        print(
            f"[MONITOR ERROR] {exc}"
        )

        traceback.print_exc()

    # ========================================================
    # OPEN SLOTS
    # ========================================================

    open_count = (
        count_open_trades()
    )

    available_slots = max(
        0,
        MAX_OPEN_TRADES
        -
        open_count,
    )

    print(
        f"[TRADES] Open={open_count} "
        f"Slots={available_slots}"
    )

    # ========================================================
    # FRESH SIGNALS
    # ========================================================

    signal_structures = []

    for result in analyses.values():

        for structure in result.get(
            "signals",
            [],
        ):

            signal_structures.append(
                structure
            )

    signal_structures.sort(
        key=lambda x: (
            x["score"],
            x["recency_score"],
        ),
        reverse=True,
    )

    selected = signal_structures[
        :available_slots
    ]

    # ========================================================
    # INSERT SIGNALS
    # ========================================================

    for structure in selected:

        symbol = structure[
            "symbol"
        ]

        signal = make_signal(
            symbol,
            structure,
        )

        if signal_exists(
            signal["signal_key"]
        ):

            print(
                f"[DUPLICATE] "
                f"{symbol} "
                f"{signal['direction']}"
            )

            continue

        if insert_signal(
            signal
        ):

            new_signals.append(
                signal
            )

            print(
                f"[NEW SIGNAL] "
                f"{symbol} "
                f"{signal['direction']} "
                f"Entry="
                f"{fmt_price(signal['entry'])} "
                f"SL="
                f"{fmt_price(signal['sl'])} "
                f"TP="
                f"{fmt_price(signal['tp'])} "
                f"RR="
                f"{signal['rr']:.2f}"
            )

            df = data_map[
                symbol
            ]

            _, chart_bytes = (
                create_chart(
                    symbol,
                    df,
                    structure,
                    "NDS 5M SIGNAL",
                )
            )

            send_signal_telegram(
                signal,
                structure,
                chart_bytes,
            )

    # ========================================================
    # BEST CANDIDATE
    # ========================================================

    best = None

    if best_candidates:

        best_candidates.sort(
            key=lambda x: (
                x["score"],
                x["recency_score"],
            ),
            reverse=True,
        )

        best = best_candidates[
            0
        ]

    # --------------------------------------------------------
    # Candidate does NOT open a trade.
    # --------------------------------------------------------

    if not new_signals:

        if best:

            print()
            print(
                f"[BEST CANDIDATE] "
                f"{best['symbol']} "
                f"{best['direction']} "
                f"Score="
                f"{best['score']:.1f}"
            )

            df = data_map[
                best["symbol"]
            ]

            _, chart_bytes = (
                create_chart(
                    best["symbol"],
                    df,
                    best,
                    "NDS 5M BEST CANDIDATE",
                )
            )

            send_candidate_telegram(
                best,
                chart_bytes,
            )

        else:

            print(
                "[BEST CANDIDATE] NONE"
            )

    # ========================================================
    # SAVE RUN
    # ========================================================

    save_run(
        configured=configured,
        fetched=fetched,
        analyzed=analyzed,
        data_errors=data_errors,
        analysis_errors=analysis_errors,
        new_signals=len(
            new_signals
        ),
        candidates=len(
            best_candidates
        ),
    )

    # ========================================================
    # FINAL REPORT
    # ========================================================

    report = build_report(
        configured=configured,
        fetched=fetched,
        analyzed=analyzed,
        data_errors=data_errors,
        analysis_errors=analysis_errors,
        new_signals=len(
            new_signals
        ),
        best=best,
    )

    print()
    print("=" * 70)
    print("FINAL REPORT")
    print("=" * 70)

    # Console version
    print(
        report.replace(
            "<b>",
            ""
        ).replace(
            "</b>",
            ""
        )
    )

    print("=" * 70)

    telegram_send(
        report
    )

    print(
        "[DONE]"
    )


# ============================================================
# SAFE ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\n[STOP] Interrupted."
        )

    except Exception as exc:

        print(
            f"\n[FATAL] {exc}"
        )

        traceback.print_exc()

        # Try to notify Telegram.
        try:

            telegram_send(
                "🚨 <b>NDS 5M SCANNER ERROR</b>\n\n"
                f"<code>{str(exc)[:700]}</code>\n\n"
                "Version: "
                f"{VERSION}"
            )

        except Exception:

            pass
