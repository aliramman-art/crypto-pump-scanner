# ============================================================
# NDS M30 LIVE SCANNER
# VERSION 4.5.0
# ============================================================
#
# PAPER ONLY
#
# M30 ONLY
# NO M1
# NO 123F
#
# POSITIVE HOOK / SHORT:
#
# START -> H1 -> L1 -> H2 -> L2 -> H3
#
# START = LOWEST POINT OF THE WHOLE HOOK
# H2 > H1
# L2 < L1
# H3 > H2
#
# NEGATIVE HOOK / LONG:
#
# START -> L1 -> H1 -> L2 -> H2 -> L3
#
# START = HIGHEST POINT OF THE WHOLE HOOK
# L2 < L1
# H2 > H1
# L3 < L2
#
# CONFIRMATION:
# SHORT = H3
# LONG  = L3
#
# TP:
# 86.4% RETRACEMENT FROM CONFIRMATION TOWARD START
#
# SHORT:
# TP = H3 - 0.864 * (H3 - START)
#
# LONG:
# TP = L3 + 0.864 * (START - L3)
#
# IMPORTANT:
# 86.4% IS TP.
# 86.4% IS NOT START.
#
# ============================================================

import os
import time
import math
import sqlite3
import traceback
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "4.5.0"

DB_FILE = os.getenv(
    "NDS_DB_FILE",
    "nds_m30_m1_v44.db"
)

CHART_DIR = os.getenv(
    "NDS_CHART_DIR",
    "nds_charts"
)

BASE_URL = "https://futures.kraken.com/api/charts/v1/trade"

INTERVAL = "30m"

M30_COUNT = int(os.getenv("M30_COUNT", "320"))

PIVOT_LEFT = int(os.getenv("PIVOT_LEFT", "2"))
PIVOT_RIGHT = int(os.getenv("PIVOT_RIGHT", "2"))

# Minimum distance between opposite pivots.
# 0.0005 = 0.05%
MIN_SWING_PCT = float(
    os.getenv("MIN_SWING_PCT", "0.0005")
)

# Hook must be recent enough to matter.
# Six hours gives the scanner enough history without
# keeping ancient hooks alive forever.
MAX_HOOK_AGE_MIN = int(
    os.getenv("MAX_HOOK_AGE_MIN", "360")
)

REQUEST_TIMEOUT = int(
    os.getenv("REQUEST_TIMEOUT", "20")
)

TP_RETRACE = 0.864

SL_BUFFER_PCT = float(
    os.getenv("SL_BUFFER_PCT", "0.0015")
)

CHART_CANDLES = int(
    os.getenv("CHART_CANDLES", "180")
)

SCAN_SLEEP_SEC = int(
    os.getenv("SCAN_SLEEP_SEC", "60")
)

MAX_CHARTS_PER_SCAN = int(
    os.getenv("MAX_CHARTS_PER_SCAN", "10")
)


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
# SYMBOLS
# ============================================================

DEFAULT_SYMBOLS = [
    "PI_XBTUSD",
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
    "PF_TRXUSD",
    "PF_ATOMUSD",
    "PF_NEARUSD",
    "PF_FILUSD",
    "PF_ALGOUSD",
    "PF_APTUSD",
    "PF_ARBUSD",
    "PF_OPUSD",
    "PF_SUIUSD",
    "PF_INJUSD",
    "PF_SEIUSD",
    "PF_TIAUSD",
    "PF_PEPEUSD",
    "PF_WIFUSD",
    "PF_FLOKIUSD",
    "PF_BONKUSD",
    "PF_DOGEUSD",
    "PF_SHIBUSD",
    "PF_LDOUSD",
    "PF_AAVEUSD",
    "PF_UNIUSD",
    "PF_MKRUSD",
    "PF_ETCUSD",
    "PF_XLMUSD",
    "PF_XTZUSD",
    "PF_HBARUSD",
    "PF_EOSUSD",
    "PF_RUNEUSD",
]

_env_symbols = os.getenv("NDS_SYMBOLS", "").strip()

if _env_symbols:
    SYMBOLS = [
        x.strip()
        for x in _env_symbols.split(",")
        if x.strip()
    ]
else:
    SYMBOLS = list(dict.fromkeys(DEFAULT_SYMBOLS))


# ============================================================
# GLOBAL DIAGNOSTICS
# ============================================================

STATS = {
    "requests": 0,
    "data_ok": 0,
    "empty": 0,
    "short_data": 0,
    "errors": 0,

    "pivot_highs": 0,
    "pivot_lows": 0,
    "alternating_pivots": 0,

    "six_point_sequences": 0,

    "positive_candidates": 0,
    "negative_candidates": 0,

    "positive_structure_valid": 0,
    "negative_structure_valid": 0,

    "start_fail_positive": 0,
    "start_fail_negative": 0,

    "h2_fail": 0,
    "l2_fail": 0,
    "h3_fail": 0,
    "l3_fail": 0,

    "age_expired": 0,

    "confirmed_hooks": 0,
    "new_signals": 0,

    "charts": 0,
}


REJECTION = {
    "positive_start": 0,
    "positive_h2": 0,
    "positive_l2": 0,
    "positive_h3": 0,

    "negative_start": 0,
    "negative_l2": 0,
    "negative_h2": 0,
    "negative_l3": 0,
}


# ============================================================
# HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().isoformat()


def safe_float(value, default=None):
    try:
        if value is None:
            return default
        x = float(value)
        if not math.isfinite(x):
            return default
        return x
    except Exception:
        return default


def pct(a, b):
    if b == 0:
        return 0.0
    return abs(a - b) / abs(b) * 100.0


def fmt_price(x):
    if x is None:
        return "N/A"

    x = float(x)

    if x >= 1000:
        return f"{x:,.2f}"
    if x >= 100:
        return f"{x:,.3f}"
    if x >= 1:
        return f"{x:.5f}"
    return f"{x:.8f}"


def short_symbol(symbol):
    return symbol.replace("PF_", "").replace("PI_", "")


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            start_time TEXT NOT NULL,
            confirm_time TEXT NOT NULL,

            start_price REAL NOT NULL,

            p1_time TEXT NOT NULL,
            p1_price REAL NOT NULL,

            p2_time TEXT NOT NULL,
            p2_price REAL NOT NULL,

            p3_time TEXT NOT NULL,
            p3_price REAL NOT NULL,

            p4_time TEXT NOT NULL,
            p4_price REAL NOT NULL,

            confirm_price REAL NOT NULL,

            tp REAL NOT NULL,
            sl REAL NOT NULL,

            created_at TEXT NOT NULL,

            UNIQUE(
                symbol,
                direction,
                start_time,
                confirm_time
            )
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry REAL NOT NULL,
            tp REAL NOT NULL,
            sl REAL NOT NULL,
            entry_time TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            exit REAL,
            exit_time TEXT,
            pnl_pct REAL
        )
    """)

    conn.commit()
    return conn


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    try:
        url = (
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}/sendMessage"
        )

        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=REQUEST_TIMEOUT,
        )

        return response.ok

    except Exception:
        return False


def telegram_send_photo(path, caption=""):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    try:
        url = (
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
        )

        with open(path, "rb") as f:
            response = requests.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption,
                    "parse_mode": "HTML",
                },
                files={
                    "photo": f
                },
                timeout=REQUEST_TIMEOUT,
            )

        return response.ok

    except Exception:
        return False


# ============================================================
# KRAKEN M30 DATA
# ============================================================

def fetch_m30(symbol):
    STATS["requests"] += 1

    url = f"{BASE_URL}/{symbol}/{INTERVAL}"

    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        # Kraken response can differ slightly between endpoints.
        candles = None

        if isinstance(data, dict):
            candles = data.get("candles")

            if candles is None:
                candles = data.get("data")

        elif isinstance(data, list):
            candles = data

        if not candles:
            STATS["empty"] += 1
            return None

        rows = []

        for c in candles:

            if isinstance(c, dict):

                ts = (
                    c.get("time")
                    or c.get("timestamp")
                    or c.get("t")
                )

                op = (
                    c.get("open")
                    or c.get("o")
                )

                hi = (
                    c.get("high")
                    or c.get("h")
                )

                lo = (
                    c.get("low")
                    or c.get("l")
                )

                cl = (
                    c.get("close")
                    or c.get("c")
                )

                volume = (
                    c.get("volume")
                    or c.get("v")
                    or 0
                )

            else:
                # Kraken array fallback:
                # [time, open, high, low, close, ...]
                if len(c) < 5:
                    continue

                ts = c[0]
                op = c[1]
                hi = c[2]
                lo = c[3]
                cl = c[4]
                volume = c[5] if len(c) > 5 else 0

            ts = safe_float(ts)
            op = safe_float(op)
            hi = safe_float(hi)
            lo = safe_float(lo)
            cl = safe_float(cl)
            volume = safe_float(volume, 0)

            if None in (
                ts,
                op,
                hi,
                lo,
                cl,
            ):
                continue

            # Kraken timestamps may be milliseconds.
            if ts > 10_000_000_000:
                ts = ts / 1000.0

            rows.append(
                (
                    datetime.fromtimestamp(
                        ts,
                        tz=timezone.utc
                    ),
                    op,
                    hi,
                    lo,
                    cl,
                    volume,
                )
            )

        if not rows:
            STATS["empty"] += 1
            return None

        df = pd.DataFrame(
            rows,
            columns=[
                "time",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ],
        )

        df = (
            df.drop_duplicates("time")
            .sort_values("time")
            .reset_index(drop=True)
        )

        if len(df) < 50:
            STATS["short_data"] += 1
            return None

        # Remove currently forming candle.
        if len(df) > 1:
            df = df.iloc[:-1].copy()

        df = df.tail(M30_COUNT).reset_index(drop=True)

        STATS["data_ok"] += 1

        return df

    except Exception:
        STATS["errors"] += 1
        return None


# ============================================================
# PIVOTS
# ============================================================

def find_raw_pivots(df):
    highs = []
    lows = []

    n = len(df)

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT
    ):

        h = float(df.iloc[i]["high"])
        l = float(df.iloc[i]["low"])

        left_highs = df.iloc[
            i - PIVOT_LEFT:i
        ]["high"]

        right_highs = df.iloc[
            i + 1:i + 1 + PIVOT_RIGHT
        ]["high"]

        left_lows = df.iloc[
            i - PIVOT_LEFT:i
        ]["low"]

        right_lows = df.iloc[
            i + 1:i + 1 + PIVOT_RIGHT
        ]["low"]

        if (
            h >= float(left_highs.max())
            and h >= float(right_highs.max())
        ):
            highs.append({
                "index": i,
                "time": df.iloc[i]["time"],
                "price": h,
                "kind": "H",
            })

        if (
            l <= float(left_lows.min())
            and l <= float(right_lows.min())
        ):
            lows.append({
                "index": i,
                "time": df.iloc[i]["time"],
                "price": l,
                "kind": "L",
            })

    STATS["pivot_highs"] += len(highs)
    STATS["pivot_lows"] += len(lows)

    return highs, lows


# ============================================================
# ALTERNATING PIVOT REDUCTION
# ============================================================

def build_alternating_pivots(raw_highs, raw_lows):
    """
    Create a clean alternating pivot sequence.

    Same-type consecutive pivots:
        H -> H : keep higher H
        L -> L : keep lower L

    Opposite-type pivot:
        accepted only if swing >= MIN_SWING_PCT

    This does NOT redefine START.

    START remains the first pivot of the six-point hook.
    """

    all_pivots = raw_highs + raw_lows

    all_pivots.sort(
        key=lambda x: x["index"]
    )

    if not all_pivots:
        return []

    result = []

    for p in all_pivots:

        if not result:
            result.append(p)
            continue

        last = result[-1]

        # Same pivot type.
        if p["kind"] == last["kind"]:

            if p["kind"] == "H":

                # Keep the higher high.
                if p["price"] >= last["price"]:
                    result[-1] = p

            else:

                # Keep the lower low.
                if p["price"] <= last["price"]:
                    result[-1] = p

            continue

        # Opposite type.
        swing_pct = pct(
            p["price"],
            last["price"]
        )

        if swing_pct / 100.0 >= MIN_SWING_PCT:
            result.append(p)

    STATS["alternating_pivots"] += len(result)

    return result


# ============================================================
# HOOK STRUCTURE
# ============================================================

def calculate_short_tp(start, h3):
    """
    START = 0%
    H3    = 100%

    TP = 86.4% retracement from H3 toward START.
    """
    return h3 - TP_RETRACE * (h3 - start)


def calculate_long_tp(start, l3):
    """
    START = 0%
    L3    = 100%

    TP = 86.4% retracement from L3 toward START.
    """
    return l3 + TP_RETRACE * (start - l3)


def find_previous_valid_high(
    df,
    confirmation_index,
    confirmation_price,
):
    highs = df.iloc[
        :confirmation_index
    ]["high"]

    valid = highs[
        highs > confirmation_price
    ]

    if len(valid) == 0:
        return confirmation_price * (
            1.0 + SL_BUFFER_PCT
        )

    # Closest valid previous high above confirmation.
    return float(valid.min())


def find_previous_valid_low(
    df,
    confirmation_index,
    confirmation_price,
):
    lows = df.iloc[
        :confirmation_index
    ]["low"]

    valid = lows[
        lows < confirmation_price
    ]

    if len(valid) == 0:
        return confirmation_price * (
            1.0 - SL_BUFFER_PCT
        )

    # Closest valid previous low below confirmation.
    return float(valid.max())


def hook_age_minutes(confirm_time):
    now = utc_now()

    if confirm_time.tzinfo is None:
        confirm_time = confirm_time.replace(
            tzinfo=timezone.utc
        )

    return (
        now - confirm_time
    ).total_seconds() / 60.0


def build_hooks(
    symbol,
    df,
    alternating_pivots,
):
    """
    Detect EXACT six-point hooks.

    SHORT:
        START(L)
        H1(H)
        L1(L)
        H2(H)
        L2(L)
        H3(H)

    LONG:
        START(H)
        L1(L)
        H1(H)
        L2(L)
        H2(H)
        L3(L)

    START must be the absolute extreme of the
    complete six-point structure.
    """

    hooks = []

    if len(alternating_pivots) < 6:
        return hooks

    for i in range(
        len(alternating_pivots) - 5
    ):

        q = alternating_pivots[
            i:i + 6
        ]

        STATS["six_point_sequences"] += 1

        kinds = "".join(
            x["kind"] for x in q
        )

        # ====================================================
        # POSITIVE HOOK / SHORT
        # ====================================================

        if kinds == "LHLHLH":

            start = q[0]
            h1 = q[1]
            l1 = q[2]
            h2 = q[3]
            l2 = q[4]
            h3 = q[5]

            # START MUST BE THE LOWEST POINT.
            if start["price"] >= min(
                h1["price"],
                l1["price"],
                h2["price"],
                l2["price"],
                h3["price"],
            ):
                REJECTION["positive_start"] += 1
                STATS["start_fail_positive"] += 1
                continue

            # H2 > H1
            if h2["price"] <= h1["price"]:
                REJECTION["positive_h2"] += 1
                STATS["h2_fail"] += 1
                continue

            # L2 < L1
            if l2["price"] >= l1["price"]:
                REJECTION["positive_l2"] += 1
                STATS["l2_fail"] += 1
                continue

            # H3 > H2
            if h3["price"] <= h2["price"]:
                REJECTION["positive_h3"] += 1
                STATS["h3_fail"] += 1
                continue

            STATS["positive_structure_valid"] += 1
            STATS["positive_candidates"] += 1

            age = hook_age_minutes(
                h3["time"]
            )

            if age > MAX_HOOK_AGE_MIN:
                STATS["age_expired"] += 1
                continue

            start_price = float(
                start["price"]
            )

            h3_price = float(
                h3["price"]
            )

            tp = calculate_short_tp(
                start_price,
                h3_price
            )

            sl = find_previous_valid_high(
                df,
                h3["index"],
                h3_price
            )

            hooks.append({
                "symbol": symbol,
                "direction": "SHORT",

                "start": start,
                "h1": h1,
                "l1": l1,
                "h2": h2,
                "l2": l2,
                "confirm": h3,

                "start_price": start_price,
                "confirm_price": h3_price,

                "tp": tp,
                "sl": sl,

                "structure":
                    "START-H1-L1-H2-L2-H3",
            })

        # ====================================================
        # NEGATIVE HOOK / LONG
        # ====================================================

        elif kinds == "HLHLHL":

            start = q[0]
            l1 = q[1]
            h1 = q[2]
            l2 = q[3]
            h2 = q[4]
            l3 = q[5]

            # START MUST BE THE HIGHEST POINT.
            if start["price"] <= max(
                l1["price"],
                h1["price"],
                l2["price"],
                h2["price"],
                l3["price"],
            ):
                REJECTION["negative_start"] += 1
                STATS["start_fail_negative"] += 1
                continue

            # L2 < L1
            if l2["price"] >= l1["price"]:
                REJECTION["negative_l2"] += 1
                STATS["l2_fail"] += 1
                continue

            # H2 > H1
            if h2["price"] <= h1["price"]:
                REJECTION["negative_h2"] += 1
                STATS["h2_fail"] += 1
                continue

            # L3 < L2
            if l3["price"] >= l2["price"]:
                REJECTION["negative_l3"] += 1
                STATS["l3_fail"] += 1
                continue

            STATS["negative_structure_valid"] += 1
            STATS["negative_candidates"] += 1

            age = hook_age_minutes(
                l3["time"]
            )

            if age > MAX_HOOK_AGE_MIN:
                STATS["age_expired"] += 1
                continue

            start_price = float(
                start["price"]
            )

            l3_price = float(
                l3["price"]
            )

            tp = calculate_long_tp(
                start_price,
                l3_price
            )

            sl = find_previous_valid_low(
                df,
                l3["index"],
                l3_price
            )

            hooks.append({
                "symbol": symbol,
                "direction": "LONG",

                "start": start,
                "l1": l1,
                "h1": h1,
                "l2": l2,
                "h2": h2,
                "confirm": l3,

                "start_price": start_price,
                "confirm_price": l3_price,

                "tp": tp,
                "sl": sl,

                "structure":
                    "START-L1-H1-L2-H2-L3",
            })

    # Deduplicate same confirmation.
    unique = {}

    for h in hooks:

        key = (
            h["symbol"],
            h["direction"],
            h["start"]["time"].isoformat(),
            h["confirm"]["time"].isoformat(),
        )

        unique[key] = h

    return list(unique.values())


# ============================================================
# DATABASE HOOK INSERT
# ============================================================

def save_hook(conn, hook):

    try:

        if hook["direction"] == "SHORT":

            p1 = hook["h1"]
            p2 = hook["l1"]
            p3 = hook["h2"]
            p4 = hook["l2"]

        else:

            p1 = hook["l1"]
            p2 = hook["h1"]
            p3 = hook["l2"]
            p4 = hook["h2"]

        conn.execute("""
            INSERT OR IGNORE INTO hooks (
                symbol,
                direction,
                start_time,
                confirm_time,
                start_price,
                p1_time,
                p1_price,
                p2_time,
                p2_price,
                p3_time,
                p3_price,
                p4_time,
                p4_price,
                confirm_price,
                tp,
                sl,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            hook["symbol"],
            hook["direction"],

            hook["start"]["time"].isoformat(),
            hook["confirm"]["time"].isoformat(),

            hook["start_price"],

            p1["time"].isoformat(),
            p1["price"],

            p2["time"].isoformat(),
            p2["price"],

            p3["time"].isoformat(),
            p3["price"],

            p4["time"].isoformat(),
            p4["price"],

            hook["confirm_price"],

            hook["tp"],
            hook["sl"],

            utc_iso(),
        ))

        conn.commit()

        return conn.total_changes > 0

    except Exception:
        return False


# ============================================================
# CHECK DUPLICATE HOOK
# ============================================================

def hook_exists(
    conn,
    hook,
):
    row = conn.execute("""
        SELECT id
        FROM hooks
        WHERE symbol = ?
          AND direction = ?
          AND start_time = ?
          AND confirm_time = ?
        LIMIT 1
    """, (
        hook["symbol"],
        hook["direction"],
        hook["start"]["time"].isoformat(),
        hook["confirm"]["time"].isoformat(),
    )).fetchone()

    return row is not None


# ============================================================
# PAPER EVENT
# ============================================================

def create_paper_event(
    conn,
    hook,
):
    """
    M30-only mode.

    This is NOT the original NDS M1/F entry.

    It records the M30 confirmation as a paper event
    so the scanner can still report the confirmed hook.
    """

    try:

        row = conn.execute("""
            SELECT id
            FROM paper_trades
            WHERE symbol = ?
              AND direction = ?
              AND entry_time = ?
            LIMIT 1
        """, (
            hook["symbol"],
            hook["direction"],
            hook["confirm"]["time"].isoformat(),
        )).fetchone()

        if row:
            return False

        conn.execute("""
            INSERT INTO paper_trades (
                symbol,
                direction,
                entry,
                tp,
                sl,
                entry_time,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, 'OPEN')
        """, (
            hook["symbol"],
            hook["direction"],
            hook["confirm_price"],
            hook["tp"],
            hook["sl"],
            hook["confirm"]["time"].isoformat(),
        ))

        conn.commit()

        return True

    except Exception:
        return False


# ============================================================
# CHART
# ============================================================

def label_offset(df):
    price_range = (
        float(df["high"].max())
        - float(df["low"].min())
    )

    if price_range <= 0:
        return 1.0

    return price_range * 0.025


def plot_hook_chart(
    df,
    hook,
    output_path,
):
    """
    M30 chart with exact six hook points.

    Labels are deliberately separated so they don't sit
    on top of one another.
    """

    chart_df = df.tail(
        CHART_CANDLES
    ).copy()

    if chart_df.empty:
        return False

    fig, ax = plt.subplots(
        figsize=(16, 9)
    )

    x = np.arange(
        len(chart_df)
    )

    # --------------------------------------------------------
    # Candles
    # --------------------------------------------------------

    for i, row in chart_df.iterrows():

        local_i = i

        op = float(row["open"])
        hi = float(row["high"])
        lo = float(row["low"])
        cl = float(row["close"])

        if cl >= op:
            face = "white"
        else:
            face = "black"

        ax.vlines(
            local_i,
            lo,
            hi,
            linewidth=1.0,
        )

        body_low = min(op, cl)
        body_high = max(op, cl)
        body_height = body_high - body_low

        if body_height == 0:
            body_height = (
                max(abs(cl) * 0.00001, 1e-8)
            )

        ax.add_patch(
            plt.Rectangle(
                (
                    local_i - 0.30,
                    body_low,
                ),
                0.60,
                body_height,
                facecolor=face,
                edgecolor="black",
                linewidth=0.8,
            )
        )

    # --------------------------------------------------------
    # Map timestamp to chart index
    # --------------------------------------------------------

    time_to_x = {
        t: i
        for i, t in enumerate(
            chart_df["time"]
        )
    }

    def get_x(point):
        t = point["time"]

        if t in time_to_x:
            return time_to_x[t]

        # nearest timestamp
        diffs = (
            chart_df["time"] - t
        ).abs()

        return int(
            diffs.argmin()
        )

    # --------------------------------------------------------
    # Build ordered points
    # --------------------------------------------------------

    if hook["direction"] == "SHORT":

        points = [
            ("START", hook["start"]),
            ("H1", hook["h1"]),
            ("L1", hook["l1"]),
            ("H2", hook["h2"]),
            ("L2", hook["l2"]),
            ("H3", hook["confirm"]),
        ]

    else:

        points = [
            ("START", hook["start"]),
            ("L1", hook["l1"]),
            ("H1", hook["h1"]),
            ("L2", hook["l2"]),
            ("H2", hook["h2"]),
            ("L3", hook["confirm"]),
        ]

    # --------------------------------------------------------
    # Hook connecting line
    # --------------------------------------------------------

    px = [
        get_x(p)
        for _, p in points
    ]

    py = [
        p["price"]
        for _, p in points
    ]

    ax.plot(
        px,
        py,
        linewidth=2.0,
        marker="o",
        markersize=5,
    )

    # --------------------------------------------------------
    # Labels
    # --------------------------------------------------------

    offset = label_offset(
        chart_df
    )

    for idx, (name, p) in enumerate(points):

        xx = get_x(p)
        yy = float(p["price"])

        if idx % 2 == 0:
            text_y = yy + offset
            va = "bottom"
        else:
            text_y = yy - offset
            va = "top"

        fontsize = 11

        if name in ("H3", "L3"):
            fontsize = 14

        if name == "START":
            fontsize = 13

        ax.annotate(
            f"{name}\n{fmt_price(yy)}",
            xy=(xx, yy),
            xytext=(0, 0),
            textcoords="offset points",
            ha="center",
            va=va,
            fontsize=fontsize,
            fontweight="bold",
        )

    # --------------------------------------------------------
    # Confirmation marker
    # --------------------------------------------------------

    confirm_x = get_x(
        hook["confirm"]
    )

    confirm_y = hook["confirm_price"]

    ax.scatter(
        [confirm_x],
        [confirm_y],
        s=100,
        marker="*",
        zorder=10,
    )

    # --------------------------------------------------------
    # TP 86.4%
    # --------------------------------------------------------

    tp = hook["tp"]

    ax.axhline(
        tp,
        linestyle="--",
        linewidth=1.5,
    )

    ax.annotate(
        f"TP 86.4%\n{fmt_price(tp)}",
        xy=(
            len(chart_df) - 1,
            tp,
        ),
        xytext=(-10, 0),
        textcoords="offset points",
        ha="right",
        va="bottom",
        fontsize=11,
        fontweight="bold",
    )

    # --------------------------------------------------------
    # SL
    # --------------------------------------------------------

    sl = hook["sl"]

    ax.axhline(
        sl,
        linestyle=":",
        linewidth=1.5,
    )

    ax.annotate(
        f"SL\n{fmt_price(sl)}",
        xy=(
            len(chart_df) - 1,
            sl,
        ),
        xytext=(-10, 0),
        textcoords="offset points",
        ha="right",
        va="top",
        fontsize=11,
        fontweight="bold",
    )

    # --------------------------------------------------------
    # Current price
    # --------------------------------------------------------

    current_price = float(
        chart_df.iloc[-1]["close"]
    )

    ax.axhline(
        current_price,
        linestyle="-.",
        linewidth=1.0,
    )

    ax.annotate(
        f"CURRENT\n{fmt_price(current_price)}",
        xy=(
            len(chart_df) - 1,
            current_price,
        ),
        xytext=(-10, 0),
        textcoords="offset points",
        ha="right",
        va="center",
        fontsize=10,
    )

    # --------------------------------------------------------
    # Title
    # --------------------------------------------------------

    direction_text = (
        "SHORT" if hook["direction"] == "SHORT"
        else "LONG"
    )

    ax.set_title(
        (
            f"NDS M30 | "
            f"{short_symbol(hook['symbol'])} | "
            f"{direction_text} | "
            f"{hook['structure']}"
        ),
        fontsize=15,
        fontweight="bold",
    )

    ax.set_ylabel(
        "Price"
    )

    ax.grid(
        alpha=0.20
    )

    # x-axis labels
    step = max(
        1,
        len(chart_df) // 10
    )

    tick_positions = list(
        range(
            0,
            len(chart_df),
            step
        )
    )

    tick_labels = [
        chart_df.iloc[i]["time"].strftime(
            "%m-%d %H:%M"
        )
        for i in tick_positions
    ]

    ax.set_xticks(
        tick_positions
    )

    ax.set_xticklabels(
        tick_labels,
        rotation=35,
        ha="right",
    )

    plt.tight_layout()

    os.makedirs(
        os.path.dirname(output_path),
        exist_ok=True,
    )

    plt.savefig(
        output_path,
        dpi=150,
        bbox_inches="tight",
    )

    plt.close(fig)

    return True


# ============================================================
# TELEGRAM HOOK REPORT
# ============================================================

def build_hook_message(hook):

    direction = hook["direction"]

    if direction == "SHORT":

        structure = (
            "START → H1 → L1 → H2 → L2 → H3"
        )

        confirmation = "H3"

    else:

        structure = (
            "START → L1 → H1 → L2 → H2 → L3"
        )

        confirmation = "L3"

    start = hook["start_price"]
    confirm = hook["confirm_price"]
    tp = hook["tp"]
    sl = hook["sl"]

    if direction == "SHORT":
        tp_distance = (
            (confirm - tp)
            / confirm
            * 100
        )
    else:
        tp_distance = (
            (tp - confirm)
            / confirm
            * 100
        )

    return (
        f"🔔 <b>NDS M30 HOOK CONFIRMED</b>\n\n"
        f"<b>{short_symbol(hook['symbol'])}</b>\n"
        f"Direction: <b>{direction}</b>\n\n"

        f"<b>Structure</b>\n"
        f"{structure}\n\n"

        f"START: <b>{fmt_price(start)}</b>\n"
        f"{confirmation}: "
        f"<b>{fmt_price(confirm)}</b>\n\n"

        f"TP 86.4%: <b>{fmt_price(tp)}</b>\n"
        f"SL: <b>{fmt_price(sl)}</b>\n\n"

        f"TP distance: "
        f"<b>{tp_distance:.2f}%</b>\n\n"

        f"⚠️ M30 confirmation only\n"
        f"M1 / 123F is disabled."
    )


# ============================================================
# DIAGNOSTIC
# ============================================================

def diagnostic_text():

    return (
        "\n"
        "🔎 NDS DIAGNOSTIC\n"
        f"Version: {VERSION}\n"
        f"Time: {utc_iso()}\n\n"

        "M30\n"
        f"Requests: {STATS['requests']}\n"
        f"Data OK: {STATS['data_ok']}\n"
        f"Empty: {STATS['empty']}\n"
        f"Short data: {STATS['short_data']}\n"
        f"Errors: {STATS['errors']}\n\n"

        "PIVOTS\n"
        f"Pivot Highs: {STATS['pivot_highs']}\n"
        f"Pivot Lows: {STATS['pivot_lows']}\n"
        f"Alternating Pivots: "
        f"{STATS['alternating_pivots']}\n"
        f"6-point Sequences: "
        f"{STATS['six_point_sequences']}\n\n"

        "POSITIVE / SHORT\n"
        f"Candidates: "
        f"{STATS['positive_candidates']}\n"
        f"Structure Valid: "
        f"{STATS['positive_structure_valid']}\n"
        f"START rejected: "
        f"{REJECTION['positive_start']}\n"
        f"H2 rejected: "
        f"{REJECTION['positive_h2']}\n"
        f"L2 rejected: "
        f"{REJECTION['positive_l2']}\n"
        f"H3 rejected: "
        f"{REJECTION['positive_h3']}\n\n"

        "NEGATIVE / LONG\n"
        f"Candidates: "
        f"{STATS['negative_candidates']}\n"
        f"Structure Valid: "
        f"{STATS['negative_structure_valid']}\n"
        f"START rejected: "
        f"{REJECTION['negative_start']}\n"
        f"L2 rejected: "
        f"{REJECTION['negative_l2']}\n"
        f"H2 rejected: "
        f"{REJECTION['negative_h2']}\n"
        f"L3 rejected: "
        f"{REJECTION['negative_l3']}\n\n"

        "HOOK STATUS\n"
        f"Age expired: "
        f"{STATS['age_expired']}\n"
        f"Confirmed Hooks: "
        f"{STATS['confirmed_hooks']}\n"
        f"New Signals: "
        f"{STATS['new_signals']}\n"
        f"Charts: "
        f"{STATS['charts']}\n\n"

        "M30 ONLY\n"
        "NO M1 / NO 123F\n"
        "PAPER ONLY"
    )


# ============================================================
# PROCESS ONE SYMBOL
# ============================================================

def process_symbol(
    symbol,
    conn,
    chart_counter,
):
    df = fetch_m30(symbol)

    if df is None:
        return chart_counter

    raw_highs, raw_lows = find_raw_pivots(
        df
    )

    alternating = build_alternating_pivots(
        raw_highs,
        raw_lows
    )

    hooks = build_hooks(
        symbol,
        df,
        alternating
    )

    if not hooks:
        return chart_counter

    for hook in hooks:

        STATS["confirmed_hooks"] += 1

        is_new = not hook_exists(
            conn,
            hook
        )

        if is_new:

            save_hook(
                conn,
                hook
            )

            created = create_paper_event(
                conn,
                hook
            )

            if created:
                STATS["new_signals"] += 1

            message = build_hook_message(
                hook
            )

            telegram_send_message(
                message
            )

        # ----------------------------------------------------
        # Chart only for new hooks.
        # ----------------------------------------------------

        if (
            is_new
            and chart_counter < MAX_CHARTS_PER_SCAN
        ):

            confirm_time = (
                hook["confirm"]["time"]
                .strftime(
                    "%Y%m%d_%H%M"
                )
            )

            filename = (
                f"{short_symbol(symbol)}_"
                f"{hook['direction']}_"
                f"{confirm_time}.png"
            )

            output_path = os.path.join(
                CHART_DIR,
                filename
            )

            try:

                if plot_hook_chart(
                    df,
                    hook,
                    output_path
                ):

                    caption = (
                        f"NDS M30 "
                        f"{hook['direction']} | "
                        f"{short_symbol(symbol)}"
                    )

                    telegram_send_photo(
                        output_path,
                        caption
                    )

                    STATS["charts"] += 1
                    chart_counter += 1

            except Exception:
                pass

    return chart_counter


# ============================================================
# RESET STATS
# ============================================================

def reset_stats():

    for key in STATS:
        STATS[key] = 0

    for key in REJECTION:
        REJECTION[key] = 0


# ============================================================
# MAIN SCAN
# ============================================================

def run_scan():

    reset_stats()

    conn = db_connect()

    chart_counter = 0

    print(
        f"\n"
        f"====================================================\n"
        f"NDS M30 LIVE SCANNER {VERSION}\n"
        f"===================================================="
    )

    print(
        f"Assets: {len(SYMBOLS)}"
    )

    print(
        "Mode: M30 ONLY / PAPER ONLY"
    )

    print(
        "M1: DISABLED"
    )

    print(
        "123F: DISABLED"
    )

    print(
        f"TP retracement: {TP_RETRACE * 100:.1f}%"
    )

    print(
        f"Hook age: {MAX_HOOK_AGE_MIN} min"
    )

    print()

    for index, symbol in enumerate(
        SYMBOLS,
        start=1
    ):

        try:

            print(
                f"[{index:03d}/{len(SYMBOLS):03d}] "
                f"{symbol}"
            )

            chart_counter = process_symbol(
                symbol,
                conn,
                chart_counter
            )

        except Exception as e:

            STATS["errors"] += 1

            print(
                f"ERROR {symbol}: {e}"
            )

    conn.close()

    report = diagnostic_text()

    print(report)

    telegram_send_message(
        report
    )

    return report


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        run_scan()

    except KeyboardInterrupt:

        print(
            "\nScanner stopped."
        )

    except Exception as e:

        print(
            "\nFATAL ERROR:"
        )

        print(
            traceback.format_exc()
        )

        try:
            telegram_send_message(
                "❌ NDS M30 scanner fatal error:\n"
                f"{type(e).__name__}: {e}"
            )
        except Exception:
            pass
