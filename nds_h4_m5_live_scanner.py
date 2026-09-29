# ============================================================
# NDS H4 -> M5 LIVE SCANNER
# VERSION 5.3.4
# ============================================================
#
# PAPER TRADING ONLY
#
# H4:
#   Determines allowed direction.
#
# M5:
#   Detects NDS 6-point Hook.
#
# SHORT:
#   START -> H1 -> L1 -> H2 -> L2 -> H3
#
# LONG:
#   START -> L1 -> H1 -> L2 -> H2 -> L3
#
# ENTRY:
#   Confirmed H3 / L3
#
# TP:
#   86.4% retracement
#
# SELECTION:
#   1. Remove duplicate hooks.
#   2. Ignore STALE as a blocking condition.
#   3. TP-touch is checked ONLY after H3/L3 confirmation.
#   4. Ignore TP touches that happened before confirmation.
#   5. Calculate Entry -> TP distance for every eligible hook.
#   6. Select the hook with the greatest distance.
#
# IMPORTANT:
#   86.4% is TP.
#   It is NOT the trade start.
#
# ============================================================

import os
import time
import sqlite3
import traceback
from datetime import datetime, timezone

import requests
import pandas as pd
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "5.3.4"

DB_FILE = "nds_h4_m5_live_v51.db"
CHART_DIR = "nds_h4_m5_charts"

KRAKEN_BASE = "https://futures.kraken.com/api/charts/v1/trade"

H4_TIMEFRAME = "4h"
M5_TIMEFRAME = "5m"

H4_COUNT = 320
M5_COUNT = 500

CHART_CANDLES = 180

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

H4_MIN_SWING_PCT = 0.20
M5_MIN_SWING_PCT = 0.07

MAX_NEW_SIGNAL_AGE_SECONDS = 20 * 60

MAX_OPEN_TRADES = 3

REQUEST_TIMEOUT = 20


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
# ASSETS
# ============================================================

ASSETS = [
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


# ============================================================
# DIAGNOSTIC
# ============================================================

DIAG = {
    "h4_requests": 0,
    "h4_ok": 0,
    "h4_errors": 0,
    "h4_no_valid": 0,
    "h4_filtered": 0,
    "h4_short": 0,
    "h4_long": 0,

    "m5_requests": 0,
    "m5_ok": 0,
    "m5_errors": 0,
    "m5_short": 0,

    "matching_short": 0,
    "matching_long": 0,
    "no_matching": 0,

    "stale": 0,
    "tp_touched": 0,
    "duplicate": 0,

    "open_blocked": 0,
    "max_open_blocked": 0,

    "signals": 0,

    "last_h4_error": "",
    "last_m5_error": "",
}


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db_connect()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS hooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            direction TEXT,
            start_time INTEGER,
            h1_time INTEGER,
            l1_time INTEGER,
            h2_time INTEGER,
            l2_time INTEGER,
            entry_time INTEGER,
            start_price REAL,
            h1_price REAL,
            l1_price REAL,
            h2_price REAL,
            l2_price REAL,
            entry_price REAL,
            tp REAL,
            sl REAL,
            confirmation_time INTEGER,
            timeframe TEXT,
            created_at INTEGER
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            direction TEXT,
            entry_time INTEGER,
            entry_price REAL,
            tp REAL,
            sl REAL,
            status TEXT,
            exit_time INTEGER,
            exit_price REAL,
            pnl_pct REAL,
            created_at INTEGER
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# TIME
# ============================================================

def now_ts():
    return int(time.time())


def interval_minutes(timeframe):
    return {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "4h": 240,
        "12h": 720,
        "1d": 1440,
        "1w": 10080,
    }.get(timeframe, 1)


def hook_confirmation_time(hook, timeframe):
    return int(
        hook["entry_time"]
        + PIVOT_RIGHT * interval_minutes(timeframe) * 60
    )


def hook_age_seconds(hook):
    confirmation_time = hook.get(
        "confirmation_time",
        hook["entry_time"]
    )

    return max(
        0,
        now_ts() - int(confirmation_time)
    )


def ts_to_dt(ts):
    return datetime.fromtimestamp(
        int(ts),
        tz=timezone.utc
    )


# ============================================================
# TELEGRAM
# ============================================================

def telegram_enabled():
    return bool(
        TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    )


def telegram_send(message):
    if not telegram_enabled():
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
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=REQUEST_TIMEOUT,
        )

        return response.ok

    except Exception:
        return False


def telegram_photo(path, caption=""):
    if not telegram_enabled():
        return False

    if not os.path.exists(path):
        return False

    try:
        url = (
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
        )

        with open(path, "rb") as image_file:
            response = requests.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption,
                },
                files={
                    "photo": image_file,
                },
                timeout=REQUEST_TIMEOUT,
            )

        return response.ok

    except Exception:
        return False


# ============================================================
# KRAKEN DATA
# ============================================================

def fetch_candles(symbol, timeframe, count):
    if timeframe == H4_TIMEFRAME:
        DIAG["h4_requests"] += 1
    else:
        DIAG["m5_requests"] += 1

    url = (
        f"{KRAKEN_BASE}/"
        f"{symbol}/"
        f"{timeframe}"
    )

    try:
        response = requests.get(
            url,
            params={"count": count},
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        payload = response.json()

        candles = None

        if isinstance(payload, dict):
            if isinstance(payload.get("candles"), list):
                candles = payload["candles"]

            elif isinstance(payload.get("data"), list):
                candles = payload["data"]

            elif isinstance(
                payload.get("result"),
                dict
            ):
                result = payload["result"]

                if isinstance(
                    result.get("candles"),
                    list
                ):
                    candles = result["candles"]

        if not candles:
            raise ValueError(
                "No candles returned"
            )

        rows = []

        for row in candles:

            if isinstance(row, dict):
                ts = (
                    row.get("time")
                    or row.get("timestamp")
                    or row.get("ts")
                )

                o = (
                    row.get("open")
                    or row.get("o")
                )

                h = (
                    row.get("high")
                    or row.get("h")
                )

                l = (
                    row.get("low")
                    or row.get("l")
                )

                c = (
                    row.get("close")
                    or row.get("c")
                )

            elif isinstance(row, (list, tuple)):

                if len(row) < 5:
                    continue

                ts = row[0]
                o = row[1]
                h = row[2]
                l = row[3]
                c = row[4]

            else:
                continue

            if (
                ts is None
                or o is None
                or h is None
                or l is None
                or c is None
            ):
                continue

            try:
                ts = float(ts)

                if ts > 10_000_000_000:
                    ts /= 1000.0

                rows.append({
                    "time": int(ts),
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                })

            except Exception:
                continue

        if len(rows) < 20:
            raise ValueError(
                f"Short data: {len(rows)} candles"
            )

        df = pd.DataFrame(rows)

        df = (
            df.drop_duplicates("time")
            .sort_values("time")
            .reset_index(drop=True)
        )

        if timeframe == H4_TIMEFRAME:
            DIAG["h4_ok"] += 1
        else:
            DIAG["m5_ok"] += 1

        return df

    except Exception as exc:

        error_text = str(exc)

        if timeframe == H4_TIMEFRAME:
            DIAG["h4_errors"] += 1
            DIAG["last_h4_error"] = error_text
        else:
            DIAG["m5_errors"] += 1
            DIAG["last_m5_error"] = error_text

        return None


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(df):
    highs = []
    lows = []

    left = PIVOT_LEFT
    right = PIVOT_RIGHT

    for i in range(
        left,
        len(df) - right
    ):

        current_high = float(
            df.iloc[i]["high"]
        )

        current_low = float(
            df.iloc[i]["low"]
        )

        left_highs = df.iloc[
            i - left:i
        ]["high"]

        right_highs = df.iloc[
            i + 1:i + 1 + right
        ]["high"]

        left_lows = df.iloc[
            i - left:i
        ]["low"]

        right_lows = df.iloc[
            i + 1:i + 1 + right
        ]["low"]

        if (
            current_high >= left_highs.max()
            and current_high >= right_highs.max()
        ):
            highs.append({
                "type": "H",
                "time": int(df.iloc[i]["time"]),
                "price": current_high,
                "index": i,
            })

        if (
            current_low <= left_lows.min()
            and current_low <= right_lows.min()
        ):
            lows.append({
                "type": "L",
                "time": int(df.iloc[i]["time"]),
                "price": current_low,
                "index": i,
            })

    return highs, lows


def build_ordered_pivots(highs, lows):
    pivots = highs + lows

    pivots.sort(
        key=lambda x: x["time"]
    )

    if not pivots:
        return []

    result = []

    for pivot in pivots:

        if not result:
            result.append(pivot)
            continue

        previous = result[-1]

        if pivot["type"] != previous["type"]:
            result.append(pivot)
            continue

        if pivot["type"] == "H":

            if pivot["price"] > previous["price"]:
                result[-1] = pivot

        else:

            if pivot["price"] < previous["price"]:
                result[-1] = pivot

    return result


# ============================================================
# HOOK CALCULATION
# ============================================================

def calculate_hook(
    pivots,
    i,
    timeframe
):

    if i < 5:
        return None

    p = pivots[i - 5:i + 1]

    min_swing_pct = (
        M5_MIN_SWING_PCT
        if timeframe == M5_TIMEFRAME
        else H4_MIN_SWING_PCT
    )

    # --------------------------------------------------------
    # SHORT
    #
    # START -> H1 -> L1 -> H2 -> L2 -> H3
    #
    # H2 > H1
    # L2 < L1
    # H3 > H2
    # START below L1/L2
    # --------------------------------------------------------

    if (
        p[0]["type"] == "L"
        and p[1]["type"] == "H"
        and p[2]["type"] == "L"
        and p[3]["type"] == "H"
        and p[4]["type"] == "L"
        and p[5]["type"] == "H"
    ):

        start = p[0]["price"]
        h1 = p[1]["price"]
        l1 = p[2]["price"]
        h2 = p[3]["price"]
        l2 = p[4]["price"]
        h3 = p[5]["price"]

        if not (
            h2 > h1
            and l2 < l1
            and h3 > h2
        ):
            return None

        if not (
            start < l1
            and start < l2
        ):
            return None

        if start <= 0:
            return None

        hook_range_pct = (
            abs(h3 - start)
            / start
            * 100.0
        )

        if hook_range_pct < min_swing_pct:
            return None

        entry = h3

        tp = (
            entry
            - 0.864 * (entry - start)
        )

        sl = (
            entry
            + 0.50 * abs(entry - tp)
        )

        confirmation_time = (
            int(p[5]["time"])
            + PIVOT_RIGHT
            * interval_minutes(timeframe)
            * 60
        )

        return {
            "direction": "SHORT",

            "start_time": int(p[0]["time"]),
            "h1_time": int(p[1]["time"]),
            "l1_time": int(p[2]["time"]),
            "h2_time": int(p[3]["time"]),
            "l2_time": int(p[4]["time"]),
            "entry_time": int(p[5]["time"]),

            "start_price": float(start),
            "h1_price": float(h1),
            "l1_price": float(l1),
            "h2_price": float(h2),
            "l2_price": float(l2),
            "entry_price": float(entry),

            "tp": float(tp),
            "sl": float(sl),

            "confirmation_time": int(
                confirmation_time
            ),

            "timeframe": timeframe,
        }

    # --------------------------------------------------------
    # LONG
    #
    # START -> L1 -> H1 -> L2 -> H2 -> L3
    #
    # L2 < L1
    # H2 > H1
    # L3 < L2
    # START above H1/H2
    # --------------------------------------------------------

    if (
        p[0]["type"] == "H"
        and p[1]["type"] == "L"
        and p[2]["type"] == "H"
        and p[3]["type"] == "L"
        and p[4]["type"] == "H"
        and p[5]["type"] == "L"
    ):

        start = p[0]["price"]
        l1 = p[1]["price"]
        h1 = p[2]["price"]
        l2 = p[3]["price"]
        h2 = p[4]["price"]
        l3 = p[5]["price"]

        if not (
            l2 < l1
            and h2 > h1
            and l3 < l2
        ):
            return None

        if not (
            start > h1
            and start > h2
        ):
            return None

        if start <= 0:
            return None

        hook_range_pct = (
            abs(start - l3)
            / start
            * 100.0
        )

        if hook_range_pct < min_swing_pct:
            return None

        entry = l3

        tp = (
            entry
            + 0.864 * (start - entry)
        )

        sl = (
            entry
            - 0.50 * abs(entry - tp)
        )

        confirmation_time = (
            int(p[5]["time"])
            + PIVOT_RIGHT
            * interval_minutes(timeframe)
            * 60
        )

        return {
            "direction": "LONG",

            "start_time": int(p[0]["time"]),
            "h1_time": int(p[2]["time"]),
            "l1_time": int(p[1]["time"]),
            "h2_time": int(p[4]["time"]),
            "l2_time": int(p[3]["time"]),
            "entry_time": int(p[5]["time"]),

            "start_price": float(start),
            "h1_price": float(h1),
            "l1_price": float(l1),
            "h2_price": float(h2),
            "l2_price": float(l2),
            "entry_price": float(entry),

            "tp": float(tp),
            "sl": float(sl),

            "confirmation_time": int(
                confirmation_time
            ),

            "timeframe": timeframe,
        }

    return None


# ============================================================
# HOOK DETECTION
# ============================================================

def detect_hooks(df, timeframe):
    highs, lows = find_pivots(df)

    pivots = build_ordered_pivots(
        highs,
        lows
    )

    hooks = []

    for i in range(
        5,
        len(pivots)
    ):

        hook = calculate_hook(
            pivots,
            i,
            timeframe
        )

        if hook:
            hooks.append(hook)

    hooks.sort(
        key=lambda x: x["confirmation_time"],
        reverse=True
    )

    return hooks


# ============================================================
# H4 DIRECTION
# ============================================================

def get_h4_direction(symbol):
    df = fetch_candles(
        symbol,
        H4_TIMEFRAME,
        H4_COUNT
    )

    if df is None:
        return None

    hooks = detect_hooks(
        df,
        H4_TIMEFRAME
    )

    if not hooks:
        DIAG["h4_no_valid"] += 1
        return None

    short_hooks = [
        h for h in hooks
        if h["direction"] == "SHORT"
    ]

    long_hooks = [
        h for h in hooks
        if h["direction"] == "LONG"
    ]

    DIAG["h4_short"] += len(short_hooks)
    DIAG["h4_long"] += len(long_hooks)

    latest = max(
        hooks,
        key=lambda x: x["confirmation_time"]
    )

    DIAG["h4_filtered"] += 1

    return latest["direction"]


# ============================================================
# DATABASE CHECKS
# ============================================================

def hook_exists(symbol, hook):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT 1
        FROM hooks
        WHERE symbol = ?
          AND direction = ?
          AND start_time = ?
          AND entry_time = ?
        LIMIT 1
        """,
        (
            symbol,
            hook["direction"],
            hook["start_time"],
            hook["entry_time"],
        ),
    ).fetchone()

    conn.close()

    return row is not None


def trade_exists(symbol, hook):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT 1
        FROM trades
        WHERE symbol = ?
          AND direction = ?
          AND entry_time = ?
        LIMIT 1
        """,
        (
            symbol,
            hook["direction"],
            hook["entry_time"],
        ),
    ).fetchone()

    conn.close()

    return row is not None


def has_open_trade(symbol):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT 1
        FROM trades
        WHERE symbol = ?
          AND status = 'OPEN'
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()

    conn.close()

    return row is not None


def count_open_trades():
    conn = db_connect()

    row = conn.execute(
        """
        SELECT COUNT(*)
        AS c
        FROM trades
        WHERE status = 'OPEN'
        """
    ).fetchone()

    conn.close()

    return int(row["c"])


# ============================================================
# TP TOUCH CHECK
#
# IMPORTANT:
# Only candles AFTER confirmation are considered.
#
# The confirmation candle itself is NOT considered a TP hit.
# This prevents the newly confirmed H3/L3 candle from
# incorrectly invalidating its own signal.
# ============================================================

def tp_already_touched_after_confirmation(
    df,
    hook
):

    confirmation_time = int(
        hook["confirmation_time"]
    )

    tp = float(hook["tp"])

    direction = hook["direction"]

    after = df[
        df["time"] > confirmation_time
    ]

    if after.empty:
        return False

    if direction == "SHORT":

        touched = (
            after["low"] <= tp
        )

    else:

        touched = (
            after["high"] >= tp
        )

    return bool(
        touched.any()
    )


# ============================================================
# SELECT BEST HOOK
#
# IMPORTANT:
# STALE DOES NOT BLOCK.
#
# TP is checked only AFTER confirmation.
#
# The hook with the greatest Entry -> TP distance
# is selected.
# ============================================================

def select_tradeable_hook(
    symbol,
    hooks,
    df
):

    candidates = []

    for hook in hooks:

        # ----------------------------------------------------
        # DUPLICATE
        # ----------------------------------------------------

        if (
            hook_exists(symbol, hook)
            or trade_exists(symbol, hook)
        ):

            DIAG["duplicate"] += 1

            continue

        # ----------------------------------------------------
        # TP ALREADY TOUCHED
        #
        # ONLY AFTER CONFIRMATION
        # ----------------------------------------------------

        if tp_already_touched_after_confirmation(
            df,
            hook
        ):

            DIAG["tp_touched"] += 1

            continue

        # ----------------------------------------------------
        # DISTANCE
        # ----------------------------------------------------

        entry = float(
            hook["entry_price"]
        )

        tp = float(
            hook["tp"]
        )

        if entry <= 0:
            continue

        distance_pct = (
            abs(entry - tp)
            / entry
            * 100.0
        )

        hook["_tp_distance_pct"] = (
            distance_pct
        )

        # ----------------------------------------------------
        # STALE IS INFORMATION ONLY
        # ----------------------------------------------------

        if (
            hook_age_seconds(hook)
            > MAX_NEW_SIGNAL_AGE_SECONDS
        ):
            DIAG["stale"] += 1

        candidates.append(hook)

    if not candidates:
        return None

    # --------------------------------------------------------
    # MAXIMUM DISTANCE TO 86.4%
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x: x["_tp_distance_pct"],
        reverse=True
    )

    return candidates[0]


# ============================================================
# SAVE HOOK
# ============================================================

def save_hook(symbol, hook):

    conn = db_connect()

    conn.execute(
        """
        INSERT INTO hooks (
            symbol,
            direction,
            start_time,
            h1_time,
            l1_time,
            h2_time,
            l2_time,
            entry_time,
            start_price,
            h1_price,
            l1_price,
            h2_price,
            l2_price,
            entry_price,
            tp,
            sl,
            confirmation_time,
            timeframe,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            symbol,
            hook["direction"],
            hook["start_time"],
            hook["h1_time"],
            hook["l1_time"],
            hook["h2_time"],
            hook["l2_time"],
            hook["entry_time"],
            hook["start_price"],
            hook["h1_price"],
            hook["l1_price"],
            hook["h2_price"],
            hook["l2_price"],
            hook["entry_price"],
            hook["tp"],
            hook["sl"],
            hook["confirmation_time"],
            hook["timeframe"],
            now_ts(),
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# PAPER TRADE
# ============================================================

def create_paper_trade(symbol, hook):

    conn = db_connect()

    conn.execute(
        """
        INSERT INTO trades (
            symbol,
            direction,
            entry_time,
            entry_price,
            tp,
            sl,
            status,
            exit_time,
            exit_price,
            pnl_pct,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'OPEN', NULL, NULL, NULL, ?)
        """,
        (
            symbol,
            hook["direction"],
            hook["confirmation_time"],
            hook["entry_price"],
            hook["tp"],
            hook["sl"],
            now_ts(),
        ),
    )

    conn.commit()
    conn.close()


def close_trade(
    trade_id,
    exit_time,
    exit_price,
    pnl_pct
):

    conn = db_connect()

    conn.execute(
        """
        UPDATE trades
        SET status = 'CLOSED',
            exit_time = ?,
            exit_price = ?,
            pnl_pct = ?
        WHERE id = ?
        """,
        (
            exit_time,
            exit_price,
            pnl_pct,
            trade_id,
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# MONITOR OPEN TRADES
# ============================================================

def monitor_open_trades():

    conn = db_connect()

    trades = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY id
        """
    ).fetchall()

    conn.close()

    for trade in trades:

        symbol = trade["symbol"]

        df = fetch_candles(
            symbol,
            M5_TIMEFRAME,
            10
        )

        if df is None:
            continue

        entry = float(
            trade["entry_price"]
        )

        tp = float(
            trade["tp"]
        )

        sl = float(
            trade["sl"]
        )

        direction = trade["direction"]

        candles = df[
            df["time"] >= int(
                trade["entry_time"]
            )
        ]

        if candles.empty:
            continue

        exit_price = None
        reason = None

        for _, candle in candles.iterrows():

            high = float(
                candle["high"]
            )

            low = float(
                candle["low"]
            )

            if direction == "SHORT":

                sl_hit = high >= sl
                tp_hit = low <= tp

                # Conservative:
                # if both happen in same candle,
                # SL gets priority.

                if sl_hit:
                    exit_price = sl
                    reason = "SL"

                elif tp_hit:
                    exit_price = tp
                    reason = "TP"

            else:

                sl_hit = low <= sl
                tp_hit = high >= tp

                if sl_hit:
                    exit_price = sl
                    reason = "SL"

                elif tp_hit:
                    exit_price = tp
                    reason = "TP"

            if exit_price is not None:
                exit_time = int(
                    candle["time"]
                )
                break

        if exit_price is None:
            continue

        if direction == "SHORT":

            pnl_pct = (
                (entry - exit_price)
                / entry
                * 100.0
            )

        else:

            pnl_pct = (
                (exit_price - entry)
                / entry
                * 100.0
            )

        close_trade(
            trade["id"],
            exit_time,
            exit_price,
            pnl_pct
        )

        emoji = (
            "✅"
            if pnl_pct > 0
            else "❌"
        )

        message = (
            f"{emoji} *NDS TRADE CLOSED*\n\n"
            f"*{symbol}* | {direction}\n"
            f"Entry: `{entry:.8f}`\n"
            f"Exit: `{exit_price:.8f}`\n"
            f"Reason: *{reason}*\n"
            f"PnL: *{pnl_pct:+.2f}%*"
        )

        telegram_send(message)


# ============================================================
# CHART
# ============================================================

def save_hook_chart(
    symbol,
    hook,
    df
):

    os.makedirs(
        CHART_DIR,
        exist_ok=True
    )

    chart_df = df.tail(
        CHART_CANDLES
    ).copy()

    if chart_df.empty:
        return None

    fig, ax = plt.subplots(
        figsize=(15, 8)
    )

    ax.plot(
        chart_df["time"],
        chart_df["close"],
        linewidth=1.2,
        label="M5 Close"
    )

    point_specs = [
        (
            "START",
            hook["start_time"],
            hook["start_price"]
        ),
        (
            "H1",
            hook["h1_time"],
            hook["h1_price"]
        ),
        (
            "L1",
            hook["l1_time"],
            hook["l1_price"]
        ),
        (
            "H2",
            hook["h2_time"],
            hook["h2_price"]
        ),
        (
            "L2",
            hook["l2_time"],
            hook["l2_price"]
        ),
    ]

    final_label = (
        "H3"
        if hook["direction"] == "SHORT"
        else "L3"
    )

    point_specs.append(
        (
            final_label,
            hook["entry_time"],
            hook["entry_price"]
        )
    )

    for label, ts, price in point_specs:

        ax.scatter(
            [ts],
            [price],
            s=55,
            zorder=5
        )

        ax.annotate(
            label,
            (
                ts,
                price
            ),
            xytext=(0, 10),
            textcoords="offset points",
            ha="center",
            fontsize=10,
            fontweight="bold"
        )

    entry = float(
        hook["entry_price"]
    )

    tp = float(
        hook["tp"]
    )

    sl = float(
        hook["sl"]
    )

    distance = float(
        hook.get(
            "_tp_distance_pct",
            abs(entry - tp)
            / entry
            * 100.0
        )
    )

    ax.axhline(
        entry,
        linestyle="--",
        linewidth=1.2,
        label=f"ENTRY {entry:.8f}"
    )

    ax.axhline(
        tp,
        linestyle="--",
        linewidth=1.5,
        label=f"TP 86.4% {tp:.8f}"
    )

    ax.axhline(
        sl,
        linestyle="--",
        linewidth=1.2,
        label=f"SL {sl:.8f}"
    )

    ax.axvline(
        hook["confirmation_time"],
        linestyle=":",
        linewidth=1.2,
        label="CONFIRMED"
    )

    direction_text = (
        "SHORT"
        if hook["direction"] == "SHORT"
        else "LONG"
    )

    ax.set_title(
        (
            f"NDS H4 → M5 | {symbol} | "
            f"{direction_text}\n"
            f"⭐ MAX 86.4% DISTANCE: "
            f"{distance:.2f}%"
        ),
        fontsize=14,
        fontweight="bold"
    )

    ax.set_xlabel(
        "UTC Timestamp"
    )

    ax.set_ylabel(
        "Price"
    )

    ax.legend(
        loc="best"
    )

    ax.grid(
        alpha=0.25
    )

    plt.xticks(
        rotation=25
    )

    plt.tight_layout()

    filename = (
        f"{symbol}_"
        f"{hook['direction']}_"
        f"{hook['entry_time']}.png"
    )

    path = os.path.join(
        CHART_DIR,
        filename
    )

    fig.savefig(
        path,
        dpi=140
    )

    plt.close(fig)

    return path


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def build_signal_message(
    symbol,
    hook
):

    direction = hook["direction"]

    emoji = (
        "🔴"
        if direction == "SHORT"
        else "🟢"
    )

    final_point = (
        "H3"
        if direction == "SHORT"
        else "L3"
    )

    entry = float(
        hook["entry_price"]
    )

    tp = float(
        hook["tp"]
    )

    sl = float(
        hook["sl"]
    )

    distance = float(
        hook.get(
            "_tp_distance_pct",
            abs(entry - tp)
            / entry
            * 100.0
        )
    )

    sl_distance = (
        abs(entry - sl)
        / entry
        * 100.0
    )

    confirmation = ts_to_dt(
        hook["confirmation_time"]
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    message = (
        f"{emoji} *NDS H4 → M5 SIGNAL*\n\n"

        f"*{symbol}* | *{direction}*\n\n"

        f"⭐ *SELECTED MAX 86.4% DISTANCE*\n"
        f"Entry {final_point}: `{entry:.8f}`\n"
        f"TP 86.4%: `{tp:.8f}`\n"
        f"Distance to TP: *{distance:.2f}%*\n"
        f"SL: `{sl:.8f}` "
        f"({sl_distance:.2f}%)\n\n"

        f"*HOOK POINTS*\n"
        f"START: `{hook['start_price']:.8f}`\n"
        f"H1: `{hook['h1_price']:.8f}`\n"
        f"L1: `{hook['l1_price']:.8f}`\n"
        f"H2: `{hook['h2_price']:.8f}`\n"
        f"L2: `{hook['l2_price']:.8f}`\n"
        f"{final_point}: `{entry:.8f}`\n\n"

        f"Confirmation: `{confirmation}`\n\n"

        f"🟡 *PAPER TRADE OPENED*\n"
        f"86.4% is TP, not entry."
    )

    return message


# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(symbol):

    # --------------------------------------------------------
    # H4 DIRECTION
    # --------------------------------------------------------

    h4_direction = get_h4_direction(
        symbol
    )

    if h4_direction is None:
        return

    # --------------------------------------------------------
    # M5
    # --------------------------------------------------------

    df = fetch_candles(
        symbol,
        M5_TIMEFRAME,
        M5_COUNT
    )

    if df is None:
        return

    hooks = detect_hooks(
        df,
        M5_TIMEFRAME
    )

    matching_hooks = [
        h for h in hooks
        if h["direction"] == h4_direction
    ]

    if h4_direction == "SHORT":
        DIAG["matching_short"] += len(
            matching_hooks
        )
    else:
        DIAG["matching_long"] += len(
            matching_hooks
        )

    if not matching_hooks:
        DIAG["no_matching"] += 1
        return

    # --------------------------------------------------------
    # SELECT MAX DISTANCE HOOK
    # --------------------------------------------------------

    selected = select_tradeable_hook(
        symbol,
        matching_hooks,
        df
    )

    if selected is None:
        return

    # --------------------------------------------------------
    # EXISTING OPEN TRADE
    # --------------------------------------------------------

    if has_open_trade(symbol):

        DIAG["open_blocked"] += 1

        return

    # --------------------------------------------------------
    # MAX OPEN TRADES
    # --------------------------------------------------------

    if count_open_trades() >= MAX_OPEN_TRADES:

        DIAG["max_open_blocked"] += 1

        return

    # --------------------------------------------------------
    # SAVE HOOK
    # --------------------------------------------------------

    save_hook(
        symbol,
        selected
    )

    # --------------------------------------------------------
    # CREATE PAPER TRADE
    # --------------------------------------------------------

    create_paper_trade(
        symbol,
        selected
    )

    DIAG["signals"] += 1

    # --------------------------------------------------------
    # CHART
    # --------------------------------------------------------

    chart_path = save_hook_chart(
        symbol,
        selected,
        df
    )

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    message = build_signal_message(
        symbol,
        selected
    )

    telegram_send(
        message
    )

    if chart_path:
        telegram_photo(
            chart_path,
            caption=(
                f"{symbol} | "
                f"{selected['direction']} | "
                f"MAX 86.4% DISTANCE "
                f"{selected['_tp_distance_pct']:.2f}%"
            )
        )


# ============================================================
# PERFORMANCE
# ============================================================

def get_performance():

    conn = db_connect()

    closed = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'CLOSED'
        """
    ).fetchall()

    open_trades = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        """
    ).fetchall()

    conn.close()

    wins = 0
    losses = 0
    realized = 0.0

    for trade in closed:

        pnl = float(
            trade["pnl_pct"] or 0.0
        )

        realized += pnl

        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1

    total_closed = len(
        closed
    )

    win_rate = (
        wins / total_closed * 100.0
        if total_closed
        else 0.0
    )

    return {
        "closed": total_closed,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "realized": realized,
        "open": len(open_trades),
        "open_rows": open_trades,
    }


# ============================================================
# DIAGNOSTIC
# ============================================================

def build_diagnostic(
    runtime
):

    perf = get_performance()

    lines = []

    lines.append(
        "🔎 *NDS H4 → M5 DIAGNOSTIC*"
    )

    lines.append(
        f"Version: *{VERSION}*"
    )

    lines.append(
        f"Time: "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    lines.append(
        f"Runtime: *{runtime:.1f}s*"
    )

    lines.append("")

    lines.append("━━━ *H4* ━━━")

    lines.append(
        f"Requests: {DIAG['h4_requests']}"
    )

    lines.append(
        f"Data OK: {DIAG['h4_ok']}"
    )

    lines.append(
        f"Data Error: {DIAG['h4_errors']}"
    )

    lines.append(
        f"No Valid Hook: {DIAG['h4_no_valid']}"
    )

    lines.append(
        f"H4 Filtered: {DIAG['h4_filtered']}"
    )

    lines.append(
        f"SHORT Hooks: {DIAG['h4_short']}"
    )

    lines.append(
        f"LONG Hooks: {DIAG['h4_long']}"
    )

    lines.append("")

    lines.append("━━━ *M5* ━━━")

    lines.append(
        f"Requests: {DIAG['m5_requests']}"
    )

    lines.append(
        f"Data OK: {DIAG['m5_ok']}"
    )

    lines.append(
        f"Data Error: {DIAG['m5_errors']}"
    )

    lines.append(
        f"Short Data: {DIAG['m5_short']}"
    )

    lines.append(
        f"Matching SHORT Hooks: "
        f"{DIAG['matching_short']}"
    )

    lines.append(
        f"Matching LONG Hooks: "
        f"{DIAG['matching_long']}"
    )

    lines.append(
        f"No Matching Hook: "
        f"{DIAG['no_matching']}"
    )

    lines.append(
        f"Stale: {DIAG['stale']}"
    )

    lines.append(
        f"TP Already Touched: "
        f"{DIAG['tp_touched']}"
    )

    lines.append(
        f"Duplicate: "
        f"{DIAG['duplicate']}"
    )

    lines.append(
        f"Open Trade Blocked: "
        f"{DIAG['open_blocked']}"
    )

    lines.append(
        f"Max Open Blocked: "
        f"{DIAG['max_open_blocked']}"
    )

    lines.append("")

    lines.append("━━━ *SIGNALS* ━━━")

    lines.append(
        f"New Signals: "
        f"{DIAG['signals']}"
    )

    lines.append("")

    lines.append("━━━ *OPEN TRADES* ━━━")

    if not perf["open_rows"]:

        lines.append(
            "No open trades."
        )

    else:

        for trade in perf["open_rows"]:

            entry = float(
                trade["entry_price"]
            )

            tp = float(
                trade["tp"]
            )

            sl = float(
                trade["sl"]
            )

            distance = (
                abs(entry - tp)
                / entry
                * 100.0
            )

            sl_distance = (
                abs(entry - sl)
                / entry
                * 100.0
            )

            lines.append("")

            lines.append(
                f"*{trade['symbol']}* "
                f"{trade['direction']}"
            )

            lines.append(
                f"Entry: `{entry:.8f}`"
            )

            lines.append(
                f"TP 86.4%: `{tp:.8f}` "
                f"({distance:.2f}%)"
            )

            lines.append(
                f"SL: `{sl:.8f}` "
                f"({sl_distance:.2f}%)"
            )

    lines.append("")

    lines.append("━━━ *PAPER PERFORMANCE* ━━━")

    lines.append(
        f"Closed: {perf['closed']}"
    )

    lines.append(
        f"Wins: {perf['wins']}"
    )

    lines.append(
        f"Losses: {perf['losses']}"
    )

    lines.append(
        f"Win Rate: {perf['win_rate']:.2f}%"
    )

    lines.append(
        f"Realized PnL: "
        f"{perf['realized']:+.2f}%"
    )

    lines.append(
        f"Open Trades: {perf['open']}"
    )

    lines.append("")

    lines.append(
        "🟡 *PAPER ONLY*"
    )

    lines.append(
        "No real exchange orders are sent."
    )

    if (
        DIAG["last_h4_error"]
        or DIAG["last_m5_error"]
    ):

        lines.append("")

        lines.append(
            "━━━ *API ERRORS* ━━━"
        )

        if DIAG["last_h4_error"]:

            lines.append(
                f"H4: `{DIAG['last_h4_error'][:300]}`"
            )

        if DIAG["last_m5_error"]:

            lines.append(
                f"M5: `{DIAG['last_m5_error'][:300]}`"
            )

    return "\n".join(lines)


# ============================================================
# MAIN SCAN
# ============================================================

def run_scan():

    started = time.time()

    init_db()

    # --------------------------------------------------------
    # FIRST MONITOR EXISTING PAPER TRADES
    # --------------------------------------------------------

    monitor_open_trades()

    # --------------------------------------------------------
    # SCAN ALL ASSETS
    # --------------------------------------------------------

    for symbol in ASSETS:

        try:

            process_symbol(
                symbol
            )

        except Exception as exc:

            print(
                f"[ERROR] {symbol}: "
                f"{exc}"
            )

            traceback.print_exc()

    runtime = (
        time.time()
        - started
    )

    diagnostic = build_diagnostic(
        runtime
    )

    print(
        diagnostic
    )

    telegram_send(
        diagnostic
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    run_scan() 
