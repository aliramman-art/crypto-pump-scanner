# ============================================================
# NDS 5M HOOK-HOOK-RALLY LIVE SCANNER
# VERSION 2.0.0
# ============================================================
#
# BASED ON NDS STRATEGY V1.2
#
# CORE:
#
#       RALLY
#          ↓
#       HOOK 1
#          ↓
#       HOOK 2
#          ↓
#       123 / FLAG
#          ↓
#       86.4% + SYMMETRY
#          ↓
#       RALLY
#          ↓
#       ENTRY
#
# TIMEFRAME:
#       5 MINUTES
#
# PAPER ONLY
# REAL_TRADING = False
#
# CLOSED CANDLES ONLY
# NO LOOKAHEAD
#
# ============================================================

import os
import io
import time
import sqlite3
import requests
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

VERSION = "2.0.0"

BASE_URL = "https://futures.kraken.com"

INTERVAL = "5m"

DB_FILE = "nds_5m_v20.db"

CHART_DIR = "charts"

REAL_TRADING = False

LOOKBACK = 1000

REQUEST_TIMEOUT = 30

MAX_OPEN_TRADES = 3

MAX_SIGNAL_AGE_MINUTES = 15

# ============================================================
# NDS PARAMETERS
# ============================================================

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

# Minimum initial Rally
MIN_RALLY_PCT = 0.003

# Hook retracement
MIN_HOOK_RETRACE = 0.30
MAX_HOOK_RETRACE = 0.90

# NDS reference level
NDS_86 = 0.864

# Allowed deviation from 86.4%
NDS_86_TOLERANCE = 0.10

# Price symmetry
MIN_PRICE_SYMMETRY = 0.50
MAX_PRICE_SYMMETRY = 2.00

# Time symmetry
MIN_TIME_SYMMETRY = 0.50
MAX_TIME_SYMMETRY = 2.00

# Entry confirmation
BREAK_BUFFER = 0.0005

# Flag requirements
FLAG_MAX_BARS = 12

# Minimum RR
MIN_RR = 1.0

# SL buffer
SL_BUFFER = 0.0015

# Minimum / maximum stop distance
MIN_SL_DISTANCE = 0.002
MAX_SL_DISTANCE = 0.08

# Chart
CHART_LEFT = 80
CHART_RIGHT = 20


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
# KRAKEN ASSETS
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
]


# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "NDS-5M-Live-Scanner/2.0"
    }
)


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat()


def format_time(ts):
    try:
        return datetime.fromtimestamp(
            int(ts),
            tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(ts)


# ============================================================
# HELPERS
# ============================================================

def safe_float(value, default=None):
    try:
        x = float(value)

        if np.isnan(x) or np.isinf(x):
            return default

        return x

    except Exception:
        return default


def pct_change(a, b):
    if b == 0:
        return 0.0

    return (a / b - 1.0) * 100.0


def abs_pct(a, b):
    if b == 0:
        return 0.0

    return abs(a - b) / abs(b)


def clamp(value, low, high):
    return max(low, min(high, value))


def fmt_price(price):
    price = float(price)

    if price >= 1000:
        return f"{price:.2f}"

    if price >= 100:
        return f"{price:.3f}"

    if price >= 1:
        return f"{price:.4f}"

    if price >= 0.01:
        return f"{price:.6f}"

    return f"{price:.8f}"


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send_message(text, image_bytes=None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    try:

        if image_bytes:

            url = (
                f"https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            )

            files = {
                "photo": (
                    "nds.png",
                    image_bytes,
                    "image/png"
                )
            }

            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": text
            }

            response = SESSION.post(
                url,
                data=data,
                files=files,
                timeout=REQUEST_TIMEOUT
            )

        else:

            url = (
                f"https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            )

            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text
            }

            response = SESSION.post(
                url,
                data=data,
                timeout=REQUEST_TIMEOUT
            )

        return response.ok

    except Exception as e:

        print(
            f"[TELEGRAM ERROR] {e}"
        )

        return False


# ============================================================
# API
# ============================================================

def api_get(path, params=None):

    url = BASE_URL + path

    last_error = None

    for attempt in range(3):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            return response.json()

        except Exception as e:

            last_error = e

            time.sleep(
                1.0 * (attempt + 1)
            )

    raise RuntimeError(
        f"API failed: {last_error}"
    )


# ============================================================
# FETCH 5M CANDLES
# ============================================================

def fetch_5m(symbol):

    data = api_get(
        f"/api/charts/v1/trade/{symbol}/5m"
    )

    rows = None

    if isinstance(data, dict):

        rows = (
            data.get("candles")
            or data.get("data")
            or data.get("result")
        )

    elif isinstance(data, list):

        rows = data

    if not rows:
        return None

    parsed = []

    for row in rows:

        try:

            if isinstance(row, dict):

                ts = (
                    row.get("time")
                    or row.get("timestamp")
                    or row.get("ts")
                )

                op = (
                    row.get("open")
                    or row.get("o")
                )

                hi = (
                    row.get("high")
                    or row.get("h")
                )

                lo = (
                    row.get("low")
                    or row.get("l")
                )

                cl = (
                    row.get("close")
                    or row.get("c")
                )

                vol = (
                    row.get("volume")
                    or row.get("v")
                    or 0
                )

            else:

                if len(row) < 5:
                    continue

                ts = row[0]
                op = row[1]
                hi = row[2]
                lo = row[3]
                cl = row[4]

                vol = (
                    row[5]
                    if len(row) > 5
                    else 0
                )

            ts = float(ts)

            # Handle milliseconds
            if ts > 10_000_000_000:
                ts /= 1000.0

            parsed.append(
                {
                    "time": int(ts),
                    "open": float(op),
                    "high": float(hi),
                    "low": float(lo),
                    "close": float(cl),
                    "volume": float(vol or 0),
                }
            )

        except Exception:
            continue

    if not parsed:
        return None

    df = pd.DataFrame(parsed)

    df = (
        df
        .drop_duplicates("time")
        .sort_values("time")
        .reset_index(drop=True)
    )

    # --------------------------------------------------------
    # REMOVE CURRENT UNFINISHED CANDLE
    # --------------------------------------------------------

    now = int(time.time())

    if len(df) > 0:

        last_time = int(
            df.iloc[-1]["time"]
        )

        if last_time + 300 > now:

            df = df.iloc[:-1].copy()

    if len(df) < 100:
        return None

    return df.tail(LOOKBACK).reset_index(drop=True)


# ============================================================
# PIVOTS
# ============================================================

def find_pivot_highs(df):

    result = []

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT
    ):

        price = df.iloc[i]["high"]

        left = df.iloc[
            i - PIVOT_LEFT:i
        ]["high"]

        right = df.iloc[
            i + 1:i + 1 + PIVOT_RIGHT
        ]["high"]

        if (
            price >= left.max()
            and price >= right.max()
        ):

            result.append(
                {
                    "index": i,
                    "time": int(
                        df.iloc[i]["time"]
                    ),
                    "price": float(price),
                    "type": "HIGH",
                }
            )

    return result


def find_pivot_lows(df):

    result = []

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT
    ):

        price = df.iloc[i]["low"]

        left = df.iloc[
            i - PIVOT_LEFT:i
        ]["low"]

        right = df.iloc[
            i + 1:i + 1 + PIVOT_RIGHT
        ]["low"]

        if (
            price <= left.min()
            and price <= right.min()
        ):

            result.append(
                {
                    "index": i,
                    "time": int(
                        df.iloc[i]["time"]
                    ),
                    "price": float(price),
                    "type": "LOW",
                }
            )

    return result


# ============================================================
# BUILD ALTERNATING PIVOTS
# ============================================================

def build_pivots(df):

    highs = find_pivot_highs(df)
    lows = find_pivot_lows(df)

    pivots = sorted(
        highs + lows,
        key=lambda x: x["index"]
    )

    cleaned = []

    for p in pivots:

        if not cleaned:

            cleaned.append(p)
            continue

        last = cleaned[-1]

        if p["type"] != last["type"]:

            cleaned.append(p)

        else:

            if p["type"] == "HIGH":

                if p["price"] > last["price"]:
                    cleaned[-1] = p

            else:

                if p["price"] < last["price"]:
                    cleaned[-1] = p

    return cleaned


# ============================================================
# 123 STRUCTURE
# ============================================================

def valid_123(points, direction):

    if len(points) != 3:
        return False

    p1, p2, p3 = points

    if direction == "LONG":

        # Low -> High -> Low
        if [
            p1["type"],
            p2["type"],
            p3["type"]
        ] != ["LOW", "HIGH", "LOW"]:
            return False

        # Higher low
        if p3["price"] <= p1["price"]:
            return False

        return True

    else:

        # High -> Low -> High
        if [
            p1["type"],
            p2["type"],
            p3["type"]
        ] != ["HIGH", "LOW", "HIGH"]:
            return False

        # Lower high
        if p3["price"] >= p1["price"]:
            return False

        return True


# ============================================================
# HOOK RETRACEMENT
# ============================================================

def hook_retrace(direction, p1, p2, p3):

    if direction == "LONG":

        rally = p2["price"] - p1["price"]

        if rally <= 0:
            return None

        correction = (
            p2["price"] - p3["price"]
        )

    else:

        rally = p1["price"] - p2["price"]

        if rally <= 0:
            return None

        correction = (
            p3["price"] - p2["price"]
        )

    return correction / rally


# ============================================================
# 86.4% HOOK TEST
# ============================================================

def hook_86_score(direction, p1, p2, p3):

    retrace = hook_retrace(
        direction,
        p1,
        p2,
        p3
    )

    if retrace is None:
        return 0.0

    distance = abs(
        retrace - NDS_86
    )

    score = 1.0 - (
        distance / NDS_86_TOLERANCE
    )

    return clamp(
        score,
        0.0,
        1.0
    )


# ============================================================
# PRICE SYMMETRY
# ============================================================

def price_symmetry(
    direction,
    p1,
    p2,
    p3,
    p4
):

    if direction == "LONG":

        leg1 = abs(
            p2["price"] - p1["price"]
        )

        leg2 = abs(
            p4["price"] - p3["price"]
        )

    else:

        leg1 = abs(
            p1["price"] - p2["price"]
        )

        leg2 = abs(
            p3["price"] - p4["price"]
        )

    if leg1 <= 0:
        return None

    return leg2 / leg1


# ============================================================
# TIME SYMMETRY
# ============================================================

def time_symmetry(
    hook1,
    hook2
):

    h1_start = hook1[0]["index"]
    h1_end = hook1[2]["index"]

    h2_start = hook2[0]["index"]
    h2_end = hook2[2]["index"]

    t1 = h1_end - h1_start
    t2 = h2_end - h2_start

    if t1 <= 0:
        return None

    return t2 / t1


# ============================================================
# RALLY STRENGTH
# ============================================================

def rally_pct(
    p1,
    p2
):

    if p1["price"] == 0:
        return 0.0

    return abs(
        p2["price"] - p1["price"]
    ) / p1["price"]


# ============================================================
# FIND HOOK PAIRS
# ============================================================

def find_hook_pair(
    pivots,
    direction
):

    if len(pivots) < 7:
        return None

    candidates = []

    # Need:
    #
    # Rally start
    # Hook1 123
    # Hook2 123

    for i in range(
        0,
        len(pivots) - 6
    ):

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if direction == "LONG":

            r1 = pivots[i]
            r2 = pivots[i + 1]

            if (
                r1["type"] != "LOW"
                or r2["type"] != "HIGH"
            ):
                continue

            initial_rally = rally_pct(
                r1,
                r2
            )

            if initial_rally < MIN_RALLY_PCT:
                continue

            h1 = pivots[
                i + 1:i + 4
            ]

            h2 = pivots[
                i + 3:i + 6
            ]

            if not valid_123(
                h1,
                "LONG"
            ):
                continue

            if not valid_123(
                h2,
                "LONG"
            ):
                continue

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        else:

            r1 = pivots[i]
            r2 = pivots[i + 1]

            if (
                r1["type"] != "HIGH"
                or r2["type"] != "LOW"
            ):
                continue

            initial_rally = rally_pct(
                r1,
                r2
            )

            if initial_rally < MIN_RALLY_PCT:
                continue

            h1 = pivots[
                i + 1:i + 4
            ]

            h2 = pivots[
                i + 3:i + 6
            ]

            if not valid_123(
                h1,
                "SHORT"
            ):
                continue

            if not valid_123(
                h2,
                "SHORT"
            ):
                continue

        # ----------------------------------------------------
        # Hook retracements
        # ----------------------------------------------------

        hook1_retrace = hook_retrace(
            direction,
            h1[0],
            h1[1],
            h1[2]
        )

        hook2_retrace = hook_retrace(
            direction,
            h2[0],
            h2[1],
            h2[2]
        )

        if hook1_retrace is None:
            continue

        if hook2_retrace is None:
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
        # Price symmetry
        # ----------------------------------------------------

        ps = price_symmetry(
            direction,
            h1[0],
            h1[1],
            h1[2],
            h2[2]
        )

        if ps is None:
            continue

        if not (
            MIN_PRICE_SYMMETRY
            <= ps
            <= MAX_PRICE_SYMMETRY
        ):
            continue

        # ----------------------------------------------------
        # Time symmetry
        # ----------------------------------------------------

        ts = time_symmetry(
            h1,
            h2
        )

        if ts is None:
            continue

        if not (
            MIN_TIME_SYMMETRY
            <= ts
            <= MAX_TIME_SYMMETRY
        ):
            continue

        # ----------------------------------------------------
        # 86.4 score
        # ----------------------------------------------------

        s1 = hook_86_score(
            direction,
            h1[0],
            h1[1],
            h1[2]
        )

        s2 = hook_86_score(
            direction,
            h2[0],
            h2[1],
            h2[2]
        )

        symmetry_score = (
            1.0
            - abs(
                np.log(max(ps, 1e-9))
            ) / np.log(2.0)
        )

        time_score = (
            1.0
            - abs(
                np.log(max(ts, 1e-9))
            ) / np.log(2.0)
        )

        score = (
            s1 * 0.25
            + s2 * 0.25
            + clamp(symmetry_score, 0, 1) * 0.25
            + clamp(time_score, 0, 1) * 0.25
        )

        candidates.append(
            {
                "direction": direction,
                "rally_start": r1,
                "rally_end": r2,

                "hook1": h1,
                "hook2": h2,

                "hook1_retrace": hook1_retrace,
                "hook2_retrace": hook2_retrace,

                "price_symmetry": ps,
                "time_symmetry": ts,

                "hook1_86_score": s1,
                "hook2_86_score": s2,

                "score": score,

                "last_index": h2[2]["index"],
            }
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (
            x["last_index"],
            x["score"]
        ),
        reverse=True
    )

    return candidates[0]


# ============================================================
# FLAG / ENTRY CONFIRMATION
# ============================================================

def find_entry_confirmation(
    df,
    pattern
):

    direction = pattern["direction"]

    hook2 = pattern["hook2"]

    p1 = hook2[0]
    p2 = hook2[1]
    p3 = hook2[2]

    start = p3["index"] + 1

    if start >= len(df):
        return None

    end = min(
        len(df),
        start + FLAG_MAX_BARS
    )

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        # Flag resistance = Hook2 point 2
        resistance = p2["price"]

        for i in range(start, end):

            candle = df.iloc[i]

            close = float(
                candle["close"]
            )

            threshold = (
                resistance
                * (1.0 + BREAK_BUFFER)
            )

            if close > threshold:

                return {
                    "index": i,
                    "time": int(
                        candle["time"]
                    ),
                    "price": close,
                    "level": resistance,
                    "direction": "LONG",
                }

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        support = p2["price"]

        for i in range(start, end):

            candle = df.iloc[i]

            close = float(
                candle["close"]
            )

            threshold = (
                support
                * (1.0 - BREAK_BUFFER)
            )

            if close < threshold:

                return {
                    "index": i,
                    "time": int(
                        candle["time"]
                    ),
                    "price": close,
                    "level": support,
                    "direction": "SHORT",
                }

    return None


# ============================================================
# STRUCTURAL LEVELS
# ============================================================

def calculate_levels(
    df,
    pattern,
    confirmation
):

    direction = pattern["direction"]

    entry = float(
        confirmation["price"]
    )

    h2 = pattern["hook2"]

    p1 = h2[0]
    p2 = h2[1]
    p3 = h2[2]

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        structural_sl = (
            p3["price"]
            * (1.0 - SL_BUFFER)
        )

        risk = entry - structural_sl

        if risk <= 0:
            return None

        risk_pct = (
            risk / entry
        )

        if risk_pct < MIN_SL_DISTANCE:
            return None

        if risk_pct > MAX_SL_DISTANCE:
            return None

        # Expected Rally projection
        rally_size = abs(
            p2["price"] - p1["price"]
        )

        tp = entry + rally_size

        reward = tp - entry

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        structural_sl = (
            p3["price"]
            * (1.0 + SL_BUFFER)
        )

        risk = structural_sl - entry

        if risk <= 0:
            return None

        risk_pct = (
            risk / entry
        )

        if risk_pct < MIN_SL_DISTANCE:
            return None

        if risk_pct > MAX_SL_DISTANCE:
            return None

        rally_size = abs(
            p2["price"] - p1["price"]
        )

        tp = entry - rally_size

        reward = entry - tp

    if reward <= 0:
        return None

    rr = reward / risk

    if rr < MIN_RR:
        return None

    return {
        "entry": entry,
        "sl": float(structural_sl),
        "tp": float(tp),
        "risk": float(risk),
        "reward": float(reward),
        "rr": float(rr),
    }


# ============================================================
# COMPLETE ANALYSIS
# ============================================================

def analyze_asset(
    symbol,
    df
):

    pivots = build_pivots(df)

    if len(pivots) < 7:
        return None

    long_pattern = find_hook_pair(
        pivots,
        "LONG"
    )

    short_pattern = find_hook_pair(
        pivots,
        "SHORT"
    )

    patterns = [
        p for p in
        [
            long_pattern,
            short_pattern
        ]
        if p is not None
    ]

    if not patterns:
        return None

    # Latest completed structure
    patterns.sort(
        key=lambda x: x["last_index"],
        reverse=True
    )

    pattern = patterns[0]

    # --------------------------------------------------------
    # Require structure to be near current market
    # --------------------------------------------------------

    last_index = len(df) - 1

    bars_since_hook = (
        last_index
        - pattern["last_index"]
    )

    if bars_since_hook > 30:
        return None

    # --------------------------------------------------------
    # Entry confirmation
    # --------------------------------------------------------

    confirmation = find_entry_confirmation(
        df,
        pattern
    )

    if confirmation is None:
        return None

    # --------------------------------------------------------
    # Signal freshness
    # --------------------------------------------------------

    age_bars = (
        last_index
        - confirmation["index"]
    )

    age_minutes = (
        age_bars * 5
    )

    if age_minutes > MAX_SIGNAL_AGE_MINUTES:
        return None

    # --------------------------------------------------------
    # Levels
    # --------------------------------------------------------

    levels = calculate_levels(
        df,
        pattern,
        confirmation
    )

    if levels is None:
        return None

    return {
        "symbol": symbol,

        "side": pattern["direction"],

        "rally_start": pattern["rally_start"],
        "rally_end": pattern["rally_end"],

        "hook1": pattern["hook1"],
        "hook2": pattern["hook2"],

        "hook1_retrace":
            pattern["hook1_retrace"],

        "hook2_retrace":
            pattern["hook2_retrace"],

        "price_symmetry":
            pattern["price_symmetry"],

        "time_symmetry":
            pattern["time_symmetry"],

        "hook1_86_score":
            pattern["hook1_86_score"],

        "hook2_86_score":
            pattern["hook2_86_score"],

        "pattern_score":
            pattern["score"],

        "confirmation":
            confirmation,

        "entry":
            levels["entry"],

        "sl":
            levels["sl"],

        "tp":
            levels["tp"],

        "rr":
            levels["rr"],

        "signal_time":
            confirmation["time"],
    }


# ============================================================
# DATABASE
# ============================================================

def db_connect():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = db_connect()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT NOT NULL,

            side TEXT NOT NULL,

            rally_start_time INTEGER,
            rally_end_time INTEGER,

            hook1_p1_time INTEGER,
            hook1_p2_time INTEGER,
            hook1_p3_time INTEGER,

            hook2_p1_time INTEGER,
            hook2_p2_time INTEGER,
            hook2_p3_time INTEGER,

            hook1_retrace REAL,
            hook2_retrace REAL,

            price_symmetry REAL,
            time_symmetry REAL,

            hook1_86_score REAL,
            hook2_86_score REAL,

            pattern_score REAL,

            confirmation_time INTEGER,

            entry REAL,
            sl REAL,
            tp REAL,
            rr REAL,

            status TEXT DEFAULT 'OPEN',

            exit_price REAL,
            exit_time INTEGER,
            exit_reason TEXT,
            pnl_pct REAL,

            created_at TEXT

        )
        """
    )

    conn.commit()

    conn.close()

    print("[DB] Ready")


# ============================================================
# DUPLICATE CHECK
# ============================================================

def signal_exists(
    symbol,
    side,
    confirmation_time
):

    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
        FROM signals
        WHERE symbol = ?
          AND side = ?
          AND confirmation_time = ?
        LIMIT 1
        """,
        (
            symbol,
            side,
            confirmation_time
        )
    ).fetchone()

    conn.close()

    return row is not None


# ============================================================
# OPEN TRADE COUNT
# ============================================================

def open_trade_count():

    conn = db_connect()

    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM signals
        WHERE status = 'OPEN'
        """
    ).fetchone()

    conn.close()

    return int(row[0])


# ============================================================
# INSERT SIGNAL
# ============================================================

def insert_signal(signal):

    conn = db_connect()

    h1 = signal["hook1"]
    h2 = signal["hook2"]

    conn.execute(
        """
        INSERT INTO signals (

            symbol,
            side,

            rally_start_time,
            rally_end_time,

            hook1_p1_time,
            hook1_p2_time,
            hook1_p3_time,

            hook2_p1_time,
            hook2_p2_time,
            hook2_p3_time,

            hook1_retrace,
            hook2_retrace,

            price_symmetry,
            time_symmetry,

            hook1_86_score,
            hook2_86_score,

            pattern_score,

            confirmation_time,

            entry,
            sl,
            tp,
            rr,

            status,
            created_at

        )
        VALUES (
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?,
            ?,
            ?, ?, ?, ?,
            'OPEN',
            ?
        )
        """,
        (

            signal["symbol"],
            signal["side"],

            signal["rally_start"]["time"],
            signal["rally_end"]["time"],

            h1[0]["time"],
            h1[1]["time"],
            h1[2]["time"],

            h2[0]["time"],
            h2[1]["time"],
            h2[2]["time"],

            signal["hook1_retrace"],
            signal["hook2_retrace"],

            signal["price_symmetry"],
            signal["time_symmetry"],

            signal["hook1_86_score"],
            signal["hook2_86_score"],

            signal["pattern_score"],

            signal["confirmation"]["time"],

            signal["entry"],
            signal["sl"],
            signal["tp"],
            signal["rr"],

            iso_now(),
        )
    )

    conn.commit()

    conn.close()


# ============================================================
# MONITOR OPEN TRADES
# ============================================================

def monitor_open_trades():

    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
        """
    ).fetchall()

    closed = []

    for row in rows:

        symbol = row["symbol"]

        try:
            df = fetch_5m(symbol)

            if df is None or len(df) == 0:
                continue

            candle = df.iloc[-1]

            high = float(
                candle["high"]
            )

            low = float(
                candle["low"]
            )

            side = row["side"]

            entry = float(
                row["entry"]
            )

            sl = float(
                row["sl"]
            )

            tp = float(
                row["tp"]
            )

            exit_price = None
            reason = None

            # ------------------------------------------------
            # LONG
            # ------------------------------------------------

            if side == "LONG":

                if low <= sl:

                    exit_price = sl
                    reason = "SL"

                elif high >= tp:

                    exit_price = tp
                    reason = "TP"

                if exit_price is not None:

                    pnl = (
                        exit_price
                        / entry
                        - 1.0
                    ) * 100.0

            # ------------------------------------------------
            # SHORT
            # ------------------------------------------------

            else:

                if high >= sl:

                    exit_price = sl
                    reason = "SL"

                elif low <= tp:

                    exit_price = tp
                    reason = "TP"

                if exit_price is not None:

                    pnl = (
                        1.0
                        - exit_price
                        / entry
                    ) * 100.0

            if exit_price is None:
                continue

            exit_time = int(
                candle["time"]
            )

            conn.execute(
                """
                UPDATE signals
                SET
                    status = 'CLOSED',
                    exit_price = ?,
                    exit_time = ?,
                    exit_reason = ?,
                    pnl_pct = ?
                WHERE id = ?
                """,
                (
                    exit_price,
                    exit_time,
                    reason,
                    pnl,
                    row["id"]
                )
            )

            closed.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "entry": entry,
                    "exit": exit_price,
                    "reason": reason,
                    "pnl": pnl,
                }
            )

        except Exception as e:

            print(
                f"[MONITOR ERROR] "
                f"{symbol}: {e}"
            )

    conn.commit()

    conn.close()

    return closed


# ============================================================
# CHART
# ============================================================

def create_chart(
    df,
    signal
):

    try:

        confirmation = signal[
            "confirmation"
        ]

        all_points = []

        all_points.append(
            signal["rally_start"]
        )

        all_points.append(
            signal["rally_end"]
        )

        all_points.extend(
            signal["hook1"]
        )

        all_points.extend(
            signal["hook2"]
        )

        all_indices = [
            int(x["index"])
            for x in all_points
        ]

        bi = int(
            confirmation["index"]
        )

        left = max(
            0,
            min(all_indices + [bi])
            - CHART_LEFT
        )

        right = min(
            len(df),
            max(all_indices + [bi])
            + CHART_RIGHT
        )

        cd = df.iloc[
            left:right
        ].copy()

        fig, ax = plt.subplots(
            figsize=(16, 9)
        )

        x = np.arange(
            len(cd)
        )

        for j, (_, row) in enumerate(
            cd.iterrows()
        ):

            o = float(row["open"])
            h = float(row["high"])
            l = float(row["low"])
            c = float(row["close"])

            ax.plot(
                [j, j],
                [l, h],
                linewidth=1
            )

            if c >= o:

                ax.plot(
                    [j - 0.3, j + 0.3],
                    [o, o],
                    linewidth=4
                )

                ax.plot(
                    [j - 0.3, j + 0.3],
                    [c, c],
                    linewidth=4
                )

            else:

                ax.plot(
                    [j - 0.3, j + 0.3],
                    [o, o],
                    linewidth=4
                )

                ax.plot(
                    [j - 0.3, j + 0.3],
                    [c, c],
                    linewidth=4
                )

        # ----------------------------------------------------
        # POINTS
        # ----------------------------------------------------

        labels = []

        for n, p in enumerate(
            signal["hook1"],
            1
        ):

            i = int(
                p["index"]
            ) - left

            if 0 <= i < len(cd):

                ax.scatter(
                    i,
                    p["price"],
                    s=80,
                    zorder=5
                )

                ax.annotate(
                    f"H1-{n}",
                    (
                        i,
                        p["price"]
                    ),
                    xytext=(0, 12),
                    textcoords="offset points",
                    ha="center",
                    fontweight="bold"
                )

        for n, p in enumerate(
            signal["hook2"],
            1
        ):

            i = int(
                p["index"]
            ) - left

            if 0 <= i < len(cd):

                ax.scatter(
                    i,
                    p["price"],
                    s=100,
                    zorder=6
                )

                ax.annotate(
                    f"H2-{n}",
                    (
                        i,
                        p["price"]
                    ),
                    xytext=(0, 12),
                    textcoords="offset points",
                    ha="center",
                    fontweight="bold"
                )

        # Rally points

        for label, p in [
            ("R1", signal["rally_start"]),
            ("R2", signal["rally_end"]),
        ]:

            i = int(
                p["index"]
            ) - left

            if 0 <= i < len(cd):

                ax.scatter(
                    i,
                    p["price"],
                    s=120,
                    zorder=7
                )

                ax.annotate(
                    label,
                    (
                        i,
                        p["price"]
                    ),
                    xytext=(0, -20),
                    textcoords="offset points",
                    ha="center",
                    fontweight="bold"
                )

        # Confirmation

        ci = (
            confirmation["index"]
            - left
        )

        if 0 <= ci < len(cd):

            ax.scatter(
                ci,
                confirmation["price"],
                marker="*",
                s=300,
                zorder=10
            )

            ax.annotate(
                "ENTRY",
                (
                    ci,
                    confirmation["price"]
                ),
                xytext=(0, 20),
                textcoords="offset points",
                ha="center",
                fontweight="bold"
            )

        # ----------------------------------------------------
        # LEVELS
        # ----------------------------------------------------

        ax.axhline(
            signal["entry"],
            linestyle="--",
            linewidth=1.5,
            label=(
                f"Entry "
                f"{fmt_price(signal['entry'])}"
            )
        )

        ax.axhline(
            signal["sl"],
            linestyle="--",
            linewidth=1.5,
            label=(
                f"SL "
                f"{fmt_price(signal['sl'])}"
            )
        )

        ax.axhline(
            signal["tp"],
            linestyle="--",
            linewidth=1.5,
            label=(
                f"TP "
                f"{fmt_price(signal['tp'])}"
            )
        )

        # ----------------------------------------------------
        # 86.4% HOOK2 LEVEL
        # ----------------------------------------------------

        h2 = signal["hook2"]

        if signal["side"] == "LONG":

            hook_range = (
                h2[1]["price"]
                - h2[0]["price"]
            )

            level86 = (
                h2[1]["price"]
                - hook_range * NDS_86
            )

        else:

            hook_range = (
                h2[0]["price"]
                - h2[1]["price"]
            )

            level86 = (
                h2[1]["price"]
                + hook_range * NDS_86
            )

        ax.axhline(
            level86,
            linestyle=":",
            linewidth=1.2,
            label=(
                f"86.4% "
                f"{fmt_price(level86)}"
            )
        )

        # ----------------------------------------------------
        # TITLE
        # ----------------------------------------------------

        ax.set_title(
            (
                f"NDS 5M | "
                f"{signal['symbol']} | "
                f"{signal['side']} | "
                f"Hook-Hook-Rally | "
                f"RR {signal['rr']:.2f}"
            )
        )

        ax.set_xlabel(
            "5M Closed Candles"
        )

        ax.set_ylabel(
            "Price"
        )

        ax.grid(
            True,
            alpha=0.25
        )

        ax.legend(
            loc="upper left",
            bbox_to_anchor=(1.01, 1.0)
        )

        # ----------------------------------------------------
        # X LABELS
        # ----------------------------------------------------

        step = max(
            1,
            len(cd) // 12
        )

        ticks = list(
            range(
                0,
                len(cd),
                step
            )
        )

        labels = []

        for i in ticks:

            ts = int(
                cd.iloc[i]["time"]
            )

            labels.append(
                datetime.fromtimestamp(
                    ts,
                    timezone.utc
                ).strftime(
                    "%m-%d %H:%M"
                )
            )

        ax.set_xticks(ticks)

        ax.set_xticklabels(
            labels,
            rotation=35,
            ha="right"
        )

        plt.tight_layout()

        image = io.BytesIO()

        plt.savefig(
            image,
            format="png",
            dpi=150,
            bbox_inches="tight"
        )

        plt.close(fig)

        image.seek(0)

        return image.getvalue()

    except Exception as e:

        print(
            f"[CHART ERROR] {e}"
        )

        return None


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def signal_message(signal):

    side = signal["side"]

    emoji = (
        "🟢"
        if side == "LONG"
        else "🔴"
    )

    return (
        f"{emoji} NDS 5M {side}\n"
        f"{signal['symbol']}\n\n"

        f"Entry: "
        f"{fmt_price(signal['entry'])}\n"

        f"SL: "
        f"{fmt_price(signal['sl'])}\n"

        f"TP: "
        f"{fmt_price(signal['tp'])}\n"

        f"RR: "
        f"{signal['rr']:.2f}\n\n"

        f"Hook 1: "
        f"{signal['hook1_retrace']*100:.1f}%\n"

        f"Hook 2: "
        f"{signal['hook2_retrace']*100:.1f}%\n"

        f"Price Sym: "
        f"{signal['price_symmetry']:.2f}\n"

        f"Time Sym: "
        f"{signal['time_symmetry']:.2f}\n"

        f"86% H1: "
        f"{signal['hook1_86_score']:.2f}\n"

        f"86% H2: "
        f"{signal['hook2_86_score']:.2f}\n\n"

        f"Pattern Score: "
        f"{signal['pattern_score']:.2f}\n"

        f"Time: "
        f"{format_time(signal['signal_time'])}\n\n"

        f"📄 PAPER ONLY"
    )


# ============================================================
# CLOSE MESSAGE
# ============================================================

def close_message(t):

    emoji = (
        "🟢"
        if t["side"] == "LONG"
        else "🔴"
    )

    return (
        f"🔔 NDS CLOSE\n"
        f"{emoji} {t['symbol']} "
        f"{t['side']}\n\n"
        f"Entry: {fmt_price(t['entry'])}\n"
        f"Exit: {fmt_price(t['exit'])}\n"
        f"Result: {t['pnl']:+.2f}%\n"
        f"Reason: {t['reason']}"
    )


# ============================================================
# REPORT
# ============================================================

def report():

    conn = db_connect()

    total = conn.execute(
        """
        SELECT COUNT(*)
        FROM signals
        """
    ).fetchone()[0]

    open_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM signals
        WHERE status='OPEN'
        """
    ).fetchone()[0]

    closed_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM signals
        WHERE status='CLOSED'
        """
    ).fetchone()[0]

    wins = conn.execute(
        """
        SELECT COUNT(*)
        FROM signals
        WHERE status='CLOSED'
          AND pnl_pct > 0
        """
    ).fetchone()[0]

    losses = conn.execute(
        """
        SELECT COUNT(*)
        FROM signals
        WHERE status='CLOSED'
          AND pnl_pct <= 0
        """
    ).fetchone()[0]

    pnl = conn.execute(
        """
        SELECT COALESCE(
            SUM(pnl_pct),
            0
        )
        FROM signals
        WHERE status='CLOSED'
        """
    ).fetchone()[0]

    conn.close()

    win_rate = (
        wins / closed_count * 100
        if closed_count
        else 0.0
    )

    print()
    print("=" * 65)
    print("NDS 5M LIVE REPORT")
    print("=" * 65)

    print(
        f"Version: {VERSION}"
    )

    print(
        f"Assets: {len(ASSETS)}"
    )

    print(
        f"Total signals: {total}"
    )

    print(
        f"Open trades: {open_count}"
    )

    print(
        f"Closed trades: {closed_count}"
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
        f"Total P/L: {pnl:+.2f}%"
    )

    print("=" * 65)


# ============================================================
# MAIN SCAN
# ============================================================

def main():

    print("=" * 65)

    print(
        f"NDS 5M LIVE SCANNER v{VERSION}"
    )

    print("=" * 65)

    print(
        f"PAPER ONLY: "
        f"{not REAL_TRADING}"
    )

    print(
        f"TIMEFRAME: {INTERVAL}"
    )

    print(
        "MODEL: HOOK → HOOK → RALLY"
    )

    print("=" * 65)

    os.makedirs(
        CHART_DIR,
        exist_ok=True
    )

    init_db()

    # --------------------------------------------------------
    # Monitor
    # --------------------------------------------------------

    print(
        f"[MONITOR] "
        f"{open_trade_count()} open trades"
    )

    closed = monitor_open_trades()

    for trade in closed:

        print(
            f"[CLOSE] "
            f"{trade['symbol']} "
            f"{trade['side']} "
            f"{trade['pnl']:+.2f}%"
        )

        telegram_send_message(
            close_message(trade)
        )

    # --------------------------------------------------------
    # Scan
    # --------------------------------------------------------

    new_signals = []

    for symbol in ASSETS:

        print(
            f"[SCAN] {symbol}"
        )

        try:

            if (
                open_trade_count()
                >= MAX_OPEN_TRADES
            ):

                print(
                    "[LIMIT] "
                    "Maximum open trades reached"
                )

                break

            df = fetch_5m(symbol)

            if df is None:

                print(
                    "[DATA] "
                    "No data"
                )

                continue

            print(
                f"[DATA] "
                f"{len(df)} candles"
            )

            signal = analyze_asset(
                symbol,
                df
            )

            if signal is None:

                print(
                    "[NO SIGNAL]"
                )

                continue

            confirmation_time = (
                signal["confirmation"]["time"]
            )

            if signal_exists(
                symbol,
                signal["side"],
                confirmation_time
            ):

                print(
                    "[DUPLICATE]"
                )

                continue

            # ------------------------------------------------
            # Insert
            # ------------------------------------------------

            insert_signal(
                signal
            )

            new_signals.append(
                signal
            )

            print(
                f"[SIGNAL] "
                f"{symbol} "
                f"{signal['side']} "
                f"Entry="
                f"{fmt_price(signal['entry'])} "
                f"SL="
                f"{fmt_price(signal['sl'])} "
                f"TP="
                f"{fmt_price(signal['tp'])} "
                f"RR="
                f"{signal['rr']:.2f}"
            )

            # ------------------------------------------------
            # Chart
            # ------------------------------------------------

            chart = create_chart(
                df,
                signal
            )

            # ------------------------------------------------
            # Telegram
            # ------------------------------------------------

            telegram_send_message(
                signal_message(signal),
                chart
            )

        except Exception as e:

            print(
                f"[SCAN ERROR] "
                f"{symbol}: {e}"
            )

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    report()

    print(
        f"[DONE] "
        f"New signals: "
        f"{len(new_signals)}"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
