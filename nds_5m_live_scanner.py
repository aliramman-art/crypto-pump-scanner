# ============================================================
# NDS 5M LIVE SCANNER
# VERSION 2.1.1
# ============================================================
#
# FILE:
#   nds_5m_live_scanner.py
#
# DATABASE:
#   nds_5m_v20.db
#
# TIMEFRAME:
#   5 MINUTES
#
# MODEL:
#   RALLY -> HOOK 1 -> HOOK 2 -> RALLY / BREAK
#
# PAPER ONLY
#
# FEATURES:
#   - 40 Kraken Futures assets
#   - Closed 5M candles only
#   - 123 Hook detection
#   - Hook 1
#   - Hook 2
#   - 86.4% reference
#   - Price symmetry
#   - Time symmetry
#   - Breakout confirmation
#   - Structural SL / TP
#   - RR filter
#   - New signal detection
#   - Best Candidate detection
#   - Signal chart
#   - Candidate chart
#   - Telegram signal
#   - Telegram candidate
#   - Telegram report
#   - Open trade monitoring
#   - Closed trade alerts
#   - SQLite persistence
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

VERSION = "2.1.1"

REAL_TRADING = False

TIMEFRAME = "5m"

DB_FILE = "nds_5m_v20.db"

CHART_DIR = "charts"

KRAKEN_BASE = "https://futures.kraken.com/api/charts/v1/trade"

LOOKBACK = 1000

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
# NDS 86.4%
# ------------------------------------------------------------

NDS_86 = 0.864
NDS_86_SOFT_TOLERANCE = 0.10

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

# ------------------------------------------------------------
# Flag / confirmation
# ------------------------------------------------------------

FLAG_MAX_BARS = 12

# ------------------------------------------------------------
# Signal freshness
# ------------------------------------------------------------

MAX_SIGNAL_AGE_MINUTES = 15

# ------------------------------------------------------------
# Trade limit
# ------------------------------------------------------------

MAX_OPEN_TRADES = 3

# ------------------------------------------------------------
# Risk
# ------------------------------------------------------------

SL_BUFFER = 0.0015

MIN_SL_DISTANCE = 0.002

MAX_SL_DISTANCE = 0.08

MIN_RR = 1.0

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

REQUEST_TIMEOUT = 20


# ============================================================
# 40 KRAKEN FUTURES ASSETS
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
# HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


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
    return max(low, min(high, value))


def symbol_name(symbol):
    if symbol.startswith("PF_"):
        return symbol[3:].replace("USD", "")
    return symbol


# ============================================================
# TELEGRAM
# ============================================================

def telegram_ready():
    return bool(
        TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    )


def telegram_send(text):

    if not telegram_ready():
        print("[TELEGRAM] Not configured")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:

        response = requests.post(
            url,
            data=payload,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            print(
                "[TELEGRAM ERROR] "
                f"{response.status_code}: "
                f"{response.text[:300]}"
            )

            return False

        return True

    except Exception as exc:

        print(
            f"[TELEGRAM ERROR] {exc}"
        )

        return False


def telegram_send_photo(
    image_bytes,
    caption,
):

    if not telegram_ready():
        print("[TELEGRAM] Not configured")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    files = {
        "photo": (
            "nds_chart.png",
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
            data=data,
            files=files,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            print(
                "[TELEGRAM PHOTO ERROR] "
                f"{response.status_code}: "
                f"{response.text[:300]}"
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

def db_connect():

    conn = sqlite3.connect(DB_FILE)

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = db_connect()

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
            assets INTEGER,
            signals INTEGER,
            candidates INTEGER
        )
        """
    )

    conn.commit()

    conn.close()

    print("[DB] Ready")


def count_open_trades():

    conn = db_connect()

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

    conn = db_connect()

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

    conn = db_connect()

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
                ?, ?, ?, ?, ?, ?, ?, 'OPEN',
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

        response.raise_for_status()

        data = response.json()

        candles = None

        if isinstance(data, dict):

            for key in (
                "candles",
                "data",
                "results",
            ):

                if key in data:

                    candles = data[key]

                    break

        if candles is None:

            print(
                f"[DATA ERROR] "
                f"{symbol}: no candles"
            )

            return None

        rows = []

        for item in candles:

            try:

                if isinstance(item, dict):

                    ts = (
                        item.get("time")
                        or item.get("timestamp")
                        or item.get("ts")
                    )

                    op = (
                        item.get("open")
                        or item.get("o")
                    )

                    hi = (
                        item.get("high")
                        or item.get("h")
                    )

                    lo = (
                        item.get("low")
                        or item.get("l")
                    )

                    cl = (
                        item.get("close")
                        or item.get("c")
                    )

                    vol = (
                        item.get("volume")
                        or item.get("v")
                        or 0
                    )

                else:

                    if len(item) < 5:
                        continue

                    ts = item[0]
                    op = item[1]
                    hi = item[2]
                    lo = item[3]
                    cl = item[4]

                    if len(item) > 5:
                        vol = item[5]
                    else:
                        vol = 0

                rows.append(
                    [
                        float(ts),
                        float(op),
                        float(hi),
                        float(lo),
                        float(cl),
                        float(vol),
                    ]
                )

            except Exception:

                continue

        if not rows:

            print(
                f"[DATA ERROR] "
                f"{symbol}: empty"
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

        df = df.drop_duplicates(
            subset=["timestamp"]
        )

        df = df.sort_values(
            "timestamp"
        )

        df = df.tail(
            LOOKBACK
        )

        df = df.reset_index(
            drop=True
        )

        # ----------------------------------------------------
        # Remove unfinished candle
        # ----------------------------------------------------

        current_ts = int(
            time.time()
        )

        candle_seconds = 300

        df = df[
            df["timestamp"]
            + candle_seconds
            <= current_ts
        ]

        df = df.reset_index(
            drop=True
        )

        print(
            f"[DATA] {symbol}: "
            f"{len(df)} candles"
        )

        return df

    except Exception as exc:

        print(
            f"[DATA ERROR] "
            f"{symbol}: {exc}"
        )

        return None


# ============================================================
# PIVOTS
# ============================================================

def detect_pivots(df):

    highs = df["high"].values

    lows = df["low"].values

    pivots = []

    start = PIVOT_LEFT

    end = len(df) - PIVOT_RIGHT

    for i in range(
        start,
        end,
    ):

        left_highs = highs[
            i - PIVOT_LEFT:i
        ]

        right_highs = highs[
            i + 1:
            i + PIVOT_RIGHT + 1
        ]

        left_lows = lows[
            i - PIVOT_LEFT:i
        ]

        right_lows = lows[
            i + 1:
            i + PIVOT_RIGHT + 1
        ]

        is_high = (
            highs[i]
            >= max(left_highs)
            and
            highs[i]
            >= max(right_highs)
        )

        is_low = (
            lows[i]
            <= min(left_lows)
            and
            lows[i]
            <= min(right_lows)
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
    # Remove consecutive same-type pivots
    # --------------------------------------------------------

    cleaned = []

    for pivot in pivots:

        if not cleaned:

            cleaned.append(pivot)

            continue

        previous = cleaned[-1]

        if (
            pivot["type"]
            != previous["type"]
        ):

            cleaned.append(
                pivot
            )

            continue

        if pivot["type"] == "H":

            if (
                pivot["price"]
                > previous["price"]
            ):

                cleaned[-1] = pivot

        else:

            if (
                pivot["price"]
                < previous["price"]
            ):

                cleaned[-1] = pivot

    return cleaned


# ============================================================
# 123 STRUCTURE
# ============================================================

def valid_123(
    p1,
    p2,
    p3,
    direction,
):

    if direction == "LONG":

        if not (
            p1["type"] == "L"
            and
            p2["type"] == "H"
            and
            p3["type"] == "L"
        ):

            return False

        if (
            p3["price"]
            <= p1["price"]
        ):

            return False

        return True

    if not (
        p1["type"] == "H"
        and
        p2["type"] == "L"
        and
        p3["type"] == "H"
    ):

        return False

    if (
        p3["price"]
        >= p1["price"]
    ):

        return False

    return True


# ============================================================
# RETRACEMENT
# ============================================================

def retracement_123(
    p1,
    p2,
    p3,
    direction,
):

    if direction == "LONG":

        rally = (
            p2["price"]
            - p1["price"]
        )

        if rally <= 0:
            return None

        correction = (
            p2["price"]
            - p3["price"]
        )

        return correction / rally

    rally = (
        p1["price"]
        - p2["price"]
    )

    if rally <= 0:
        return None

    correction = (
        p3["price"]
        - p2["price"]
    )

    return correction / rally


# ============================================================
# RALLY
# ============================================================

def rally_strength(
    p1,
    p2,
):

    if (
        p1["type"] == "L"
        and
        p2["type"] == "H"
    ):

        return (
            p2["price"]
            - p1["price"]
        ) / p1["price"]

    if (
        p1["type"] == "H"
        and
        p2["type"] == "L"
    ):

        return (
            p1["price"]
            - p2["price"]
        ) / p1["price"]

    return 0.0


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


# ============================================================
# SYMMETRY
# ============================================================

def calculate_symmetry(
    rally_start,
    rally_end,
    hook1_p3,
    hook2_p3,
):

    rally_price = abs(
        rally_end["price"]
        - rally_start["price"]
    )

    second_leg_price = abs(
        hook2_p3["price"]
        - hook1_p3["price"]
    )

    if rally_price <= 0:

        price_symmetry = 999.0

    else:

        price_symmetry = (
            second_leg_price
            / rally_price
        )

    rally_time = abs(
        rally_end["index"]
        - rally_start["index"]
    )

    second_time = abs(
        hook2_p3["index"]
        - hook1_p3["index"]
    )

    if rally_time <= 0:

        time_symmetry = 999.0

    else:

        time_symmetry = (
            second_time
            / rally_time
        )

    return (
        price_symmetry,
        time_symmetry,
    )


def score_symmetry(
    value,
):

    if value is None:
        return 0.0

    if value <= 0:
        return 0.0

    if not (
        MIN_PRICE_SYMMETRY
        <= value
        <= MAX_PRICE_SYMMETRY
    ):

        return 0.0

    distance = abs(
        value - 1.0
    )

    return clamp(
        1.0 - distance
    )


# ============================================================
# 86.4 SCORE
# ============================================================

def score_864(
    retrace,
):

    if retrace is None:
        return 0.0

    distance = abs(
        retrace - NDS_86
    )

    return clamp(
        1.0
        - (
            distance
            / NDS_86_SOFT_TOLERANCE
        )
    )


# ============================================================
# HOOK PAIR SEARCH
# ============================================================

def find_hook_pair(
    pivots,
    direction,
):

    candidates = []

    # Need:
    #
    # Rally start
    # Rally end
    # Hook1 P1 P2 P3
    # Hook2 P1 P2 P3
    #
    # Total = 8 pivots

    if len(pivots) < 8:

        return candidates

    for i in range(
        len(pivots) - 7
    ):

        rally_start = pivots[i]

        rally_end = pivots[i + 1]

        if (
            rally_direction(
                rally_start,
                rally_end,
            )
            != direction
        ):

            continue

        rally_pct = rally_strength(
            rally_start,
            rally_end,
        )

        if rally_pct < MIN_RALLY_PCT:

            continue

        hook1 = pivots[
            i + 2:
            i + 5
        ]

        hook2 = pivots[
            i + 5:
            i + 8
        ]

        if (
            len(hook1) < 3
            or
            len(hook2) < 3
        ):

            continue

        if not valid_123(
            hook1[0],
            hook1[1],
            hook1[2],
            direction,
        ):

            continue

        if not valid_123(
            hook2[0],
            hook2[1],
            hook2[2],
            direction,
        ):

            continue

        hook1_retrace = retracement_123(
            hook1[0],
            hook1[1],
            hook1[2],
            direction,
        )

        hook2_retrace = retracement_123(
            hook2[0],
            hook2[1],
            hook2[2],
            direction,
        )

        if (
            hook1_retrace is None
            or
            hook2_retrace is None
        ):

            continue

        if not (
            MIN_HOOK_RETRACE
            <= hook1_retrace
            <= MAX_HOOK_RETRACE
        ):

            continue

        if not (
            MIN_HOOK_RETRACE
            <= hook2_retrace
            <= MAX_HOOK_RETRACE
        ):

            continue

        # ----------------------------------------------------
        # Hook 2 should preferably be weaker than Hook 1.
        # ----------------------------------------------------

        if (
            hook2_retrace
            <= hook1_retrace
        ):

            hook2_quality = 1.0

        else:

            difference = (
                hook2_retrace
                - hook1_retrace
            )

            hook2_quality = clamp(
                1.0 - difference
            )

        # ----------------------------------------------------
        # Symmetry
        # ----------------------------------------------------

        price_symmetry, time_symmetry = (
            calculate_symmetry(
                rally_start,
                rally_end,
                hook1[2],
                hook2[2],
            )
        )

        price_score = score_symmetry(
            price_symmetry
        )

        # score_symmetry uses the same normalized
        # 0.5-2.0 range for time as well.
        time_score = score_symmetry(
            time_symmetry
        )

        # ----------------------------------------------------
        # 86.4
        # ----------------------------------------------------

        hook1_86 = score_864(
            hook1_retrace
        )

        hook2_86 = score_864(
            hook2_retrace
        )

        score_86 = (
            hook2_86 * 0.65
            +
            hook1_86 * 0.35
        )

        # ----------------------------------------------------
        # Recency
        # ----------------------------------------------------

        latest_index = (
            hook2[2]["index"]
        )

        age_bars = (
            len(pivots)
            - 1
            - latest_index
        )

        recency_score = clamp(
            1.0
            -
            (
                age_bars
                / 24.0
            )
        )

        # ----------------------------------------------------
        # Pattern score
        # ----------------------------------------------------

        score = (
            0.20
            +
            0.15 * hook2_quality
            +
            0.20 * score_86
            +
            0.15 * price_score
            +
            0.15 * time_score
            +
            0.15 * recency_score
        )

        score = clamp(
            score
        ) * 100.0

        candidates.append(
            {
                "direction": direction,
                "rally_start": rally_start,
                "rally_end": rally_end,
                "hook1": hook1,
                "hook2": hook2,
                "rally_pct": rally_pct,
                "hook1_retrace": hook1_retrace,
                "hook2_retrace": hook2_retrace,
                "price_symmetry": price_symmetry,
                "time_symmetry": time_symmetry,
                "score_864": score_86,
                "score": score,
                "age_bars": age_bars,
                "recency_score": recency_score,
                "breakout": None,
                "levels": None,
                "fresh": False,
            }
        )

    return candidates


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

    hook2 = structure[
        "hook2"
    ]

    # Hook2 P2 = trigger
    trigger = hook2[1]

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

        candle = df.iloc[i]

        close = float(
            candle["close"]
        )

        if direction == "LONG":

            breakout_level = (
                trigger_price
                * (
                    1.0
                    + BREAK_BUFFER
                )
            )

            if close > breakout_level:

                return {
                    "index": i,
                    "price": close,
                    "trigger": trigger_price,
                }

        else:

            breakout_level = (
                trigger_price
                * (
                    1.0
                    - BREAK_BUFFER
                )
            )

            if close < breakout_level:

                return {
                    "index": i,
                    "price": close,
                    "trigger": trigger_price,
                }

    return None


# ============================================================
# STRUCTURAL LEVELS
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

    hook1 = structure[
        "hook1"
    ]

    hook2 = structure[
        "hook2"
    ]

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        structural_low = min(
            hook1[2]["price"],
            hook2[2]["price"],
        )

        sl = (
            structural_low
            * (
                1.0
                - SL_BUFFER
            )
        )

        # TP uses the nearest meaningful
        # previous high above entry.

        possible_highs = []

        for point in (
            structure["rally_end"],
            hook1[1],
            hook2[1],
        ):

            if point["price"] > entry:

                possible_highs.append(
                    point["price"]
                )

        if possible_highs:

            tp = min(
                possible_highs
            )

        else:

            rally_size = abs(
                structure["rally_end"]["price"]
                -
                structure["rally_start"]["price"]
            )

            tp = (
                entry
                + rally_size
            )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        structural_high = max(
            hook1[2]["price"],
            hook2[2]["price"],
        )

        sl = (
            structural_high
            * (
                1.0
                + SL_BUFFER
            )
        )

        possible_lows = []

        for point in (
            structure["rally_end"],
            hook1[1],
            hook2[1],
        ):

            if point["price"] < entry:

                possible_lows.append(
                    point["price"]
                )

        if possible_lows:

            tp = max(
                possible_lows
            )

        else:

            rally_size = abs(
                structure["rally_end"]["price"]
                -
                structure["rally_start"]["price"]
            )

            tp = (
                entry
                - rally_size
            )

    # --------------------------------------------------------
    # Distance
    # --------------------------------------------------------

    if direction == "LONG":

        sl_distance = (
            entry - sl
        ) / entry

        tp_distance = (
            tp - entry
        ) / entry

    else:

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
# ANALYZE ASSET
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

        found = find_hook_pair(
            pivots,
            direction,
        )

        structures.extend(
            found
        )

    if not structures:

        return {
            "best": None,
            "signals": [],
            "pivots": pivots,
            "all": [],
        }

    # --------------------------------------------------------
    # Analyze each structure
    # --------------------------------------------------------

    for structure in structures:

        breakout = find_breakout(
            df,
            structure,
        )

        structure[
            "breakout"
        ] = breakout

        if breakout is None:

            continue

        candle_time = float(
            df.iloc[
                breakout["index"]
            ]["timestamp"]
        )

        age_minutes = (
            time.time()
            - candle_time
        ) / 60.0

        structure[
            "age_minutes"
        ] = age_minutes

        levels = calculate_levels(
            structure,
            breakout,
        )

        structure[
            "levels"
        ] = levels

        structure[
            "fresh"
        ] = (
            levels is not None
            and
            age_minutes
            <= MAX_SIGNAL_AGE_MINUTES
        )

    # --------------------------------------------------------
    # Sort by score
    # --------------------------------------------------------

    structures.sort(
        key=lambda item: (
            item["score"],
            item["recency_score"],
        ),
        reverse=True,
    )

    signals = [
        item
        for item in structures
        if item.get("fresh", False)
        and item.get("levels") is not None
    ]

    for item in structures:

        item["symbol"] = symbol

    return {
        "best": structures[0],
        "signals": signals,
        "pivots": pivots,
        "all": structures,
    }


# ============================================================
# SIGNAL OBJECT
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

    direction = structure[
        "direction"
    ]

    created_at = (
        now_utc().isoformat()
    )

    signal_key = (
        f"{symbol}|"
        f"{direction}|"
        f"{breakout['index']}|"
        f"{breakout['trigger']:.12f}"
    )

    return {
        "symbol": symbol,
        "direction": direction,
        "created_at": created_at,
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
# MONITOR OPEN TRADES
# ============================================================

def monitor_open_trades(
    data_map,
):

    conn = db_connect()

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

        symbol = row["symbol"]

        df = data_map.get(
            symbol
        )

        if (
            df is None
            or
            len(df) == 0
        ):

            continue

        candle = df.iloc[-1]

        high = float(
            candle["high"]
        )

        low = float(
            candle["low"]
        )

        direction = row[
            "direction"
        ]

        entry = float(
            row["entry"]
        )

        sl = float(
            row["sl"]
        )

        tp = float(
            row["tp"]
        )

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
                    - entry
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
                    - close_price
                ) / entry

        if reason:

            close_time = (
                now_utc().isoformat()
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
    # Telegram close alerts
    # --------------------------------------------------------

    for item in closed:

        row = item["row"]

        if row["direction"] == "LONG":
            emoji = "🟢"
        else:
            emoji = "🔴"

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


# ============================================================
# CHART
# ============================================================

def create_chart(
    symbol,
    df,
    structure,
    title_prefix,
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

        for point in structure[
            "hook1"
        ]:

            indices.append(
                point["index"]
            )

        for point in structure[
            "hook2"
        ]:

            indices.append(
                point["index"]
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
        ].copy()

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

        def plot_point(
            point,
            label,
            marker,
        ):

            local_x = (
                point["index"]
                - left
            )

            if (
                0
                <= local_x
                <
                len(view)
            ):

                ax.scatter(
                    local_x,
                    point["price"],
                    s=70,
                    marker=marker,
                    zorder=5,
                )

                ax.annotate(
                    label,
                    (
                        local_x,
                        point["price"],
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

        # ----------------------------------------------------
        # Rally
        # ----------------------------------------------------

        plot_point(
            structure[
                "rally_start"
            ],
            "R1",
            "o",
        )

        plot_point(
            structure[
                "rally_end"
            ],
            "R2",
            "o",
        )

        # ----------------------------------------------------
        # Hook 1
        # ----------------------------------------------------

        for number, point in enumerate(
            structure["hook1"],
            start=1,
        ):

            plot_point(
                point,
                f"H1-{number}",
                "^",
            )

        # ----------------------------------------------------
        # Hook 2
        # ----------------------------------------------------

        for number, point in enumerate(
            structure["hook2"],
            start=1,
        ):

            plot_point(
                point,
                f"H2-{number}",
                "v",
            )

        # ----------------------------------------------------
        # Trigger
        # ----------------------------------------------------

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
        # 86.4%
        # ----------------------------------------------------

        if structure["direction"] == "LONG":

            hook2_rally = (
                structure[
                    "hook2"
                ][1]["price"]
                -
                structure[
                    "hook2"
                ][0]["price"]
            )

            level_864 = (
                structure[
                    "hook2"
                ][1]["price"]
                -
                hook2_rally
                * NDS_86
            )

        else:

            hook2_rally = (
                structure[
                    "hook2"
                ][0]["price"]
                -
                structure[
                    "hook2"
                ][1]["price"]
            )

            level_864 = (
                structure[
                    "hook2"
                ][1]["price"]
                +
                hook2_rally
                * NDS_86
            )

        ax.axhline(
            level_864,
            linestyle=":",
            linewidth=1,
            label="86.4%",
        )

        # ----------------------------------------------------
        # Entry / SL / TP
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
                - left
            )

            by = (
                breakout["price"]
            )

            ax.scatter(
                bx,
                by,
                s=130,
                marker="*",
                zorder=10,
                label="Breakout",
            )

        ax.set_title(
            f"{title_prefix} | "
            f"{symbol_name(symbol)} | "
            f"{structure['direction']} | "
            f"Score {structure['score']:.1f}"
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
            loc="best",
            fontsize=8,
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
            dpi=150,
            bbox_inches="tight",
        )

        buffer = io.BytesIO()

        fig.savefig(
            buffer,
            format="png",
            dpi=150,
            bbox_inches="tight",
        )

        buffer.seek(0)

        plt.close(
            fig
        )

        return (
            path,
            buffer.getvalue(),
        )

    except Exception as exc:

        print(
            f"[CHART ERROR] "
            f"{symbol}: {exc}"
        )

        try:
            plt.close(
                "all"
            )
        except Exception:
            pass

        return (
            None,
            None,
        )


# ============================================================
# SIGNAL TELEGRAM
# ============================================================

def send_signal(
    signal,
    structure,
    chart_bytes,
):

    if signal["direction"] == "LONG":
        emoji = "🟢"
    else:
        emoji = "🔴"

    message = (
        f"{emoji} "
        f"<b>NDS 5M NEW SIGNAL</b>\n\n"
        f"<b>{symbol_name(signal['symbol'])}</b> "
        f"{signal['direction']}\n\n"
        f"Entry: "
        f"<b>{fmt_price(signal['entry'])}</b>\n"
        f"SL: "
        f"{fmt_price(signal['sl'])}\n"
        f"TP: "
        f"{fmt_price(signal['tp'])}\n"
        f"RR: "
        f"<b>{signal['rr']:.2f}</b>\n\n"
        f"Rally: "
        f"{pct(signal['rally_pct'])}\n"
        f"Hook 1: "
        f"{pct(signal['hook1_retrace'])}\n"
        f"Hook 2: "
        f"{pct(signal['hook2_retrace'])}\n"
        f"86.4 score: "
        f"{structure['score_864'] * 100:.0f}/100\n"
        f"Price symmetry: "
        f"{structure['price_symmetry']:.2f}\n"
        f"Time symmetry: "
        f"{structure['time_symmetry']:.2f}\n"
        f"Pattern score: "
        f"<b>{structure['score']:.1f}/100</b>\n\n"
        f"⏱ 5M\n"
        f"🧪 PAPER ONLY"
    )

    if chart_bytes:

        telegram_send_photo(
            chart_bytes,
            message,
        )

    else:

        telegram_send(
            message
        )


# ============================================================
# BEST CANDIDATE TELEGRAM
# ============================================================

def send_best_candidate(
    structure,
    chart_bytes,
):

    symbol = structure[
        "symbol"
    ]

    direction = structure[
        "direction"
    ]

    if direction == "LONG":
        emoji = "🟢"
    else:
        emoji = "🔴"

    breakout = structure.get(
        "breakout"
    )

    if breakout:

        status = "BREAKOUT FOUND"

        breakout_text = fmt_price(
            breakout["price"]
        )

    else:

        status = "WAITING FOR BREAK"

        breakout_text = "-"

    message = (
        f"⭐ "
        f"<b>NDS 5M BEST CANDIDATE</b>\n\n"
        f"{emoji} "
        f"<b>{symbol_name(symbol)}</b> "
        f"{direction}\n"
        f"Status: "
        f"<b>{status}</b>\n\n"
        f"Rally: "
        f"{pct(structure['rally_pct'])}\n"
        f"Hook 1: "
        f"{pct(structure['hook1_retrace'])}\n"
        f"Hook 2: "
        f"{pct(structure['hook2_retrace'])}\n"
        f"86.4 score: "
        f"{structure['score_864'] * 100:.0f}/100\n"
        f"Price symmetry: "
        f"{structure['price_symmetry']:.2f}\n"
        f"Time symmetry: "
        f"{structure['time_symmetry']:.2f}\n"
        f"Pattern score: "
        f"<b>{structure['score']:.1f}/100</b>\n"
        f"Breakout: "
        f"{breakout_text}\n\n"
        f"⚠️ Candidate only\n"
        f"❌ No automatic trade\n"
        f"⏱ 5M"
    )

    if chart_bytes:

        telegram_send_photo(
            chart_bytes,
            message,
        )

    else:

        telegram_send(
            message
        )


# ============================================================
# TELEGRAM REPORT
# ============================================================

def send_report(
    scanned,
    new_signals,
    best,
    errors,
):

    open_trades = count_open_trades()

    conn = db_connect()

    stats = conn.execute(
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
        stats["closed"] or 0
    )

    wins = int(
        stats["wins"] or 0
    )

    losses = int(
        stats["losses"] or 0
    )

    total_pnl = float(
        stats["pnl"] or 0
    )

    if closed > 0:

        win_rate = (
            wins
            /
            closed
            *
            100.0
        )

    else:

        win_rate = 0.0

    lines = [
        "📊 <b>NDS 5M REPORT</b>",
        "",
        f"Assets scanned: <b>{scanned}</b>",
        f"New signals: <b>{len(new_signals)}</b>",
        f"Open trades: <b>{open_trades}</b>",
        "",
        f"Closed: {closed}",
        f"Wins: {wins}",
        f"Losses: {losses}",
        f"Win rate: {win_rate:.2f}%",
        f"Total P/L: <b>{total_pnl * 100:+.2f}%</b>",
        f"Errors: {errors}",
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

    telegram_send(
        "\n".join(lines)
    )


# ============================================================
# SAVE RUN
# ============================================================

def save_run(
    scanned,
    signals,
    candidates,
):

    conn = db_connect()

    conn.execute(
        """
        INSERT INTO scanner_runs (
            run_time,
            assets,
            signals,
            candidates
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            now_utc().isoformat(),
            scanned,
            signals,
            candidates,
        ),
    )

    conn.commit()

    conn.close()


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 65)

    print(
        f"NDS 5M LIVE SCANNER v{VERSION}"
    )

    print("=" * 65)

    print(
        f"PAPER ONLY: "
        f"{REAL_TRADING is False}"
    )

    print(
        f"TIMEFRAME: {TIMEFRAME}"
    )

    print(
        "MODEL: "
        "HOOK → HOOK → RALLY"
    )

    print(
        f"ASSETS: {len(ASSETS)}"
    )

    print("=" * 65)

    # --------------------------------------------------------
    # Safety
    # --------------------------------------------------------

    if REAL_TRADING:

        raise RuntimeError(
            "REAL_TRADING MUST REMAIN FALSE"
        )

    # --------------------------------------------------------
    # Database
    # --------------------------------------------------------

    init_db()

    # --------------------------------------------------------
    # Storage
    # --------------------------------------------------------

    data_map = {}

    analyses = {}

    best_candidates = []

    all_new_signals = []

    errors = 0

    # --------------------------------------------------------
    # Fetch + Analyze
    # --------------------------------------------------------

    for symbol in ASSETS:

        print(
            f"[SCAN] {symbol}"
        )

        df = fetch_candles(
            symbol
        )

        if df is None:

            errors += 1

            continue

        if len(df) < 100:

            print(
                f"[SKIP] {symbol}: "
                f"not enough candles"
            )

            continue

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

            best = result[
                "best"
            ]

            if best:

                best_candidates.append(
                    best
                )

                print(
                    f"[CANDIDATE] "
                    f"{symbol} "
                    f"{best['direction']} "
                    f"score="
                    f"{best['score']:.1f} "
                    f"H1="
                    f"{pct(best['hook1_retrace'])} "
                    f"H2="
                    f"{pct(best['hook2_retrace'])}"
                )

                if best.get(
                    "breakout"
                ):

                    age = best.get(
                        "age_minutes"
                    )

                    if age is not None:

                        print(
                            f"[BREAKOUT] "
                            f"{symbol} "
                            f"{best['direction']} "
                            f"age="
                            f"{age:.1f}m"
                        )

            else:

                print(
                    f"[NO STRUCTURE] "
                    f"{symbol}"
                )

        except Exception as exc:

            errors += 1

            print(
                f"[ANALYSIS ERROR] "
                f"{symbol}: {exc}"
            )

            traceback.print_exc()

    # --------------------------------------------------------
    # Monitor existing trades
    # --------------------------------------------------------

    monitor_open_trades(
        data_map
    )

    # --------------------------------------------------------
    # Current open trade count
    # --------------------------------------------------------

    open_count = count_open_trades()

    slots = max(
        0,
        MAX_OPEN_TRADES
        - open_count,
    )

    # --------------------------------------------------------
    # Collect fresh signals
    # --------------------------------------------------------

    signal_candidates = []

    for symbol, result in (
        analyses.items()
    ):

        for structure in result.get(
            "signals",
            [],
        ):

            signal_candidates.append(
                structure
            )

    signal_candidates.sort(
        key=lambda item: item[
            "score"
        ],
        reverse=True,
    )

    selected = (
        signal_candidates[:slots]
        if slots > 0
        else []
    )

    # --------------------------------------------------------
    # Create new signals
    # --------------------------------------------------------

    for structure in selected:

        signal = make_signal(
            structure["symbol"],
            structure,
        )

        if signal_exists(
            signal["signal_key"]
        ):

            print(
                f"[DUPLICATE] "
                f"{signal['symbol']} "
                f"{signal['direction']}"
            )

            continue

        inserted = insert_signal(
            signal
        )

        if not inserted:

            continue

        all_new_signals.append(
            signal
        )

        print(
            f"[NEW SIGNAL] "
            f"{signal['symbol']} "
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

        # ----------------------------------------------------
        # Signal chart
        # ----------------------------------------------------

        df = data_map[
            signal["symbol"]
        ]

        _, chart_bytes = create_chart(
            signal["symbol"],
            df,
            structure,
            "NDS 5M SIGNAL",
        )

        send_signal(
            signal,
            structure,
            chart_bytes,
        )

    # --------------------------------------------------------
    # Best candidate
    # --------------------------------------------------------

    best = None

    if best_candidates:

        best_candidates.sort(
            key=lambda item: (
                item["score"],
                item["recency_score"],
            ),
            reverse=True,
        )

        best = best_candidates[0]

    # --------------------------------------------------------
    # If no new signal:
    # send Best Candidate
    # --------------------------------------------------------

    if len(all_new_signals) == 0:

        if best:

            print(
                f"[BEST CANDIDATE] "
                f"{best['symbol']} "
                f"{best['direction']} "
                f"score="
                f"{best['score']:.1f}"
            )

            df = data_map[
                best["symbol"]
            ]

            _, chart_bytes = create_chart(
                best["symbol"],
                df,
                best,
                "NDS 5M BEST CANDIDATE",
            )

            send_best_candidate(
                best,
                chart_bytes,
            )

        else:

            print(
                "[BEST CANDIDATE] NONE"
            )

    # --------------------------------------------------------
    # Save scanner run
    # --------------------------------------------------------

    save_run(
        len(data_map),
        len(all_new_signals),
        len(best_candidates),
    )

    # --------------------------------------------------------
    # Telegram report
    # --------------------------------------------------------

    send_report(
        len(data_map),
        all_new_signals,
        best,
        errors,
    )

    # --------------------------------------------------------
    # Console final report
    # --------------------------------------------------------

    print()

    print("=" * 65)

    print(
        "NDS 5M LIVE REPORT"
    )

    print("=" * 65)

    print(
        f"Version: {VERSION}"
    )

    print(
        f"Assets: {len(data_map)}"
    )

    print(
        f"Total signals: "
        f"{len(all_new_signals)}"
    )

    print(
        f"Open trades: "
        f"{count_open_trades()}"
    )

    conn = db_connect()

    stats = conn.execute(
        """
        SELECT
            COUNT(*) AS closed,
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
        stats["closed"] or 0
    )

    wins = int(
        stats["wins"] or 0
    )

    losses = int(
        stats["losses"] or 0
    )

    pnl = float(
        stats["pnl"] or 0
    )

    if closed > 0:

        win_rate = (
            wins
            /
            closed
            *
            100.0
        )

    else:

        win_rate = 0.0

    print(
        f"Closed trades: {closed}"
    )

    print(
        f"Wins: {wins}"
    )

    print(
        f"Losses: {losses}"
    )

    print(
        f"Win rate: {win_rate:.2f}%"
    )

    print(
        f"Total P/L: "
        f"{pnl * 100:+.2f}%"
    )

    if best:

        print(
            f"Best Candidate: "
            f"{symbol_name(best['symbol'])} "
            f"{best['direction']} "
            f"score="
            f"{best['score']:.1f}"
        )

    else:

        print(
            "Best Candidate: NONE"
        )

    print("=" * 65)

    print(
        f"[DONE] "
        f"New signals: "
        f"{len(all_new_signals)}"
    )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    main()
