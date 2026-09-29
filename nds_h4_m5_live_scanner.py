# ============================================================
# NDS H4 -> M5 LIVE SCANNER
# VERSION 5.3.6
# ============================================================
#
# PAPER TRADING ONLY
#
# LOGIC
# ------------------------------------------------------------
# H4:
#   Determines allowed direction only.
#
# M5 SHORT HOOK:
#   START -> H1 -> L1 -> H2 -> L2 -> H3
#
#   H2 > H1
#   L2 < L1
#   H3 > H2
#
#   Entry = H3
#   TP    = 86.4% retracement from H3 toward START
#
# M5 LONG HOOK:
#   START -> L1 -> H1 -> L2 -> H2 -> L3
#
#   L2 < L1
#   H2 > H1
#   L3 < L2
#
#   Entry = L3
#   TP    = 86.4% retracement from L3 toward START
#
# IMPORTANT:
#   A "fresh" hook is NOT defined by an arbitrary age such as
#   20 minutes.
#
#   A fresh hook must terminate at the LATEST CONFIRMED M5
#   pivot of the required type:
#
#       SHORT -> latest confirmed HIGH = H3
#       LONG  -> latest confirmed LOW  = L3
#
#   Older hooks are considered superseded.
#
#   A hook is also rejected if price has already reached TP
#   after the hook confirmation.
#
# No real exchange orders are sent.
# ============================================================

import os
import sqlite3
import time
import math
import traceback
from datetime import datetime, timezone

import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "5.3.6"

KRAKEN_FUTURES_URL = "https://futures.kraken.com/derivatives/api/v3"

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

DB_FILE = "nds_h4_m5_live_v536.db"
CHART_DIR = "nds_h4_m5_charts"

TARGET_ASSETS = 100

H4_INTERVAL = 240
M5_INTERVAL = 5

H4_CANDLES = 220
M5_CANDLES = 1500

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

NDS_RETRACE = 0.864

# Minimum hook range.
MIN_HOOK_RANGE_PCT = 0.20

# Paper SL.
SL_DISTANCE_MULTIPLIER = 0.50

MAX_OPEN_TRADES = 3

HTTP_TIMEOUT = 20

SCAN_SLEEP_SECONDS = 0

# No arbitrary 20-minute freshness filter.
# Structural freshness is used instead.


# ============================================================
# DIAGNOSTICS
# ============================================================

DIAG = {
    "h4_requests": 0,
    "h4_ok": 0,
    "h4_error": 0,
    "h4_no_hook": 0,
    "h4_filtered": 0,
    "h4_short": 0,
    "h4_long": 0,

    "m5_requests": 0,
    "m5_ok": 0,
    "m5_error": 0,
    "m5_short_data": 0,

    "matching_short": 0,
    "matching_long": 0,
    "no_matching_hook": 0,

    "superseded": 0,
    "tp_touched": 0,
    "duplicate": 0,
    "open_trade_blocked": 0,
    "max_open_blocked": 0,

    "signals": 0,
    "charts": 0,
    "telegram": 0,

    "symbols_scanned": 0,
}


# ============================================================
# GENERAL HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_now_str():
    return utc_now().strftime("%Y-%m-%d %H:%M:%S UTC")


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


def fmt_price(value):
    x = safe_float(value, 0)

    if x == 0:
        return "0"

    if abs(x) >= 1000:
        return f"{x:,.2f}"

    if abs(x) >= 100:
        return f"{x:.3f}"

    if abs(x) >= 1:
        return f"{x:.4f}"

    if abs(x) >= 0.01:
        return f"{x:.6f}"

    return f"{x:.8f}"


def fmt_pct(value):
    return f"{safe_float(value, 0):+.2f}%"


def iso_from_timestamp(ts):
    try:
        return datetime.fromtimestamp(
            float(ts),
            tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ts)


def timestamp_now():
    return int(time.time())


# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": f"NDS-H4-M5/{VERSION}"
})


def http_get(path, params=None):
    url = f"{KRAKEN_FUTURES_URL}{path}"

    response = SESSION.get(
        url,
        params=params,
        timeout=HTTP_TIMEOUT
    )

    response.raise_for_status()

    data = response.json()

    return data


# ============================================================
# KRAKEN SYMBOLS
# ============================================================

def get_futures_instruments():
    try:
        data = http_get("/instruments")

        instruments = data.get("instruments", [])

        symbols = []

        for item in instruments:

            symbol = item.get("symbol", "")

            if not symbol:
                continue

            if not symbol.startswith("PF_"):
                continue

            # Exclude obvious non-standard instruments.
            if "USD" not in symbol:
                continue

            symbols.append(symbol)

        symbols = sorted(set(symbols))

        return symbols[:TARGET_ASSETS]

    except Exception as e:
        print(f"Instrument error: {e}")

        return []


# ============================================================
# CANDLES
# ============================================================

def get_candles(symbol, interval, count):
    try:

        data = http_get(
            "/charts",
            params={
                "symbol": symbol,
                "resolution": interval
            }
        )

        candles = data.get("candles", [])

        if not candles:
            return pd.DataFrame()

        rows = []

        for c in candles[-count:]:

            try:

                ts = (
                    c.get("time")
                    or c.get("timestamp")
                    or c.get("ts")
                )

                op = c.get("open")
                hi = c.get("high")
                lo = c.get("low")
                cl = c.get("close")
                vol = c.get("volume", 0)

                if ts is None:
                    continue

                rows.append({
                    "time": int(float(ts)) / 1000
                    if float(ts) > 10_000_000_000
                    else int(float(ts)),

                    "open": float(op),
                    "high": float(hi),
                    "low": float(lo),
                    "close": float(cl),
                    "volume": float(vol or 0)
                })

            except Exception:
                continue

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)

        df = df.drop_duplicates("time")

        df = df.sort_values("time")

        df = df.reset_index(drop=True)

        return df

    except Exception:
        return pd.DataFrame()


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(df, left=2, right=2):

    highs = []
    lows = []

    if df is None or len(df) < left + right + 5:
        return highs, lows

    for i in range(left, len(df) - right):

        high = safe_float(df.iloc[i]["high"])
        low = safe_float(df.iloc[i]["low"])

        if high is None or low is None:
            continue

        left_highs = [
            safe_float(df.iloc[j]["high"])
            for j in range(i - left, i)
        ]

        right_highs = [
            safe_float(df.iloc[j]["high"])
            for j in range(i + 1, i + right + 1)
        ]

        left_lows = [
            safe_float(df.iloc[j]["low"])
            for j in range(i - left, i)
        ]

        right_lows = [
            safe_float(df.iloc[j]["low"])
            for j in range(i + 1, i + right + 1)
        ]

        if (
            all(high > x for x in left_highs)
            and all(high >= x for x in right_highs)
        ):
            highs.append({
                "type": "H",
                "index": i,
                "time": int(df.iloc[i]["time"]),
                "price": high
            })

        if (
            all(low < x for x in left_lows)
            and all(low <= x for x in right_lows)
        ):
            lows.append({
                "type": "L",
                "index": i,
                "time": int(df.iloc[i]["time"]),
                "price": low
            })

    return highs, lows


def build_ordered_pivots(highs, lows):

    all_pivots = highs + lows

    all_pivots.sort(
        key=lambda x: (
            x["time"],
            x["index"]
        )
    )

    if not all_pivots:
        return []

    result = []

    for p in all_pivots:

        if not result:
            result.append(p)
            continue

        previous = result[-1]

        # Same pivot type consecutively.
        if previous["type"] == p["type"]:

            if p["type"] == "H":

                # Keep the higher high.
                if p["price"] >= previous["price"]:
                    result[-1] = p

            else:

                # Keep the lower low.
                if p["price"] <= previous["price"]:
                    result[-1] = p

        else:
            result.append(p)

    return result


# ============================================================
# HOOK CALCULATION
# ============================================================

def calculate_hook(points, direction, interval_minutes):

    if len(points) != 6:
        return None

    p0, p1, p2, p3, p4, p5 = points

    start = safe_float(p0["price"])
    entry = safe_float(p5["price"])

    if start is None or entry is None:
        return None

    if start <= 0 or entry <= 0:
        return None

    if direction == "SHORT":

        # START -> H1 -> L1 -> H2 -> L2 -> H3

        if [
            p0["type"],
            p1["type"],
            p2["type"],
            p3["type"],
            p4["type"],
            p5["type"],
        ] != ["L", "H", "L", "H", "L", "H"]:
            return None

        h1 = p1["price"]
        l1 = p2["price"]
        h2 = p3["price"]
        l2 = p4["price"]
        h3 = p5["price"]

        if not (h2 > h1):
            return None

        if not (l2 < l1):
            return None

        if not (h3 > h2):
            return None

        # START must be the original low.
        if not (start < l1 and start < l2):
            return None

        if h3 <= start:
            return None

        range_pct = (
            abs(h3 - start)
            / start
            * 100.0
        )

        if range_pct < MIN_HOOK_RANGE_PCT:
            return None

        # 86.4% retracement from H3 toward START.
        tp = h3 - (
            NDS_RETRACE
            * (h3 - start)
        )

        # Paper SL.
        sl = h3 + (
            SL_DISTANCE_MULTIPLIER
            * abs(h3 - tp)
        )

        confirmation_time = (
            int(p5["time"])
            + interval_minutes * 60 * PIVOT_RIGHT
        )

        return {
            "direction": "SHORT",
            "start": start,
            "entry_price": h3,
            "tp": tp,
            "sl": sl,

            "start_time": int(p0["time"]),
            "h1_time": int(p1["time"]),
            "l1_time": int(p2["time"]),
            "h2_time": int(p3["time"]),
            "l2_time": int(p4["time"]),
            "h3_time": int(p5["time"]),

            "confirmation_time": confirmation_time,

            "start_index": int(p0["index"]),
            "h1_index": int(p1["index"]),
            "l1_index": int(p2["index"]),
            "h2_index": int(p3["index"]),
            "l2_index": int(p4["index"]),
            "h3_index": int(p5["index"]),

            "range_pct": range_pct,
        }

    # --------------------------------------------------------

    if direction == "LONG":

        # START -> L1 -> H1 -> L2 -> H2 -> L3

        if [
            p0["type"],
            p1["type"],
            p2["type"],
            p3["type"],
            p4["type"],
            p5["type"],
        ] != ["H", "L", "H", "L", "H", "L"]:
            return None

        l1 = p1["price"]
        h1 = p2["price"]
        l2 = p3["price"]
        h2 = p4["price"]
        l3 = p5["price"]

        if not (l2 < l1):
            return None

        if not (h2 > h1):
            return None

        if not (l3 < l2):
            return None

        # START must be the original high.
        if not (start > h1 and start > h2):
            return None

        if l3 >= start:
            return None

        range_pct = (
            abs(start - l3)
            / start
            * 100.0
        )

        if range_pct < MIN_HOOK_RANGE_PCT:
            return None

        # 86.4% retracement from L3 toward START.
        tp = l3 + (
            NDS_RETRACE
            * (start - l3)
        )

        # Paper SL.
        sl = l3 - (
            SL_DISTANCE_MULTIPLIER
            * abs(tp - l3)
        )

        confirmation_time = (
            int(p5["time"])
            + interval_minutes * 60 * PIVOT_RIGHT
        )

        return {
            "direction": "LONG",
            "start": start,
            "entry_price": l3,
            "tp": tp,
            "sl": sl,

            "start_time": int(p0["time"]),
            "l1_time": int(p1["time"]),
            "h1_time": int(p2["time"]),
            "l2_time": int(p3["time"]),
            "h2_time": int(p4["time"]),
            "l3_time": int(p5["time"]),

            "confirmation_time": confirmation_time,

            "start_index": int(p0["index"]),
            "l1_index": int(p1["index"]),
            "h1_index": int(p2["index"]),
            "l2_index": int(p3["index"]),
            "h2_index": int(p4["index"]),
            "l3_index": int(p5["index"]),

            "range_pct": range_pct,
        }

    return None


def detect_hooks(df):

    highs, lows = find_pivots(
        df,
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    pivots = build_ordered_pivots(
        highs,
        lows
    )

    hooks = []

    if len(pivots) < 6:
        return hooks, pivots, highs, lows

    for i in range(len(pivots) - 5):

        points = pivots[i:i + 6]

        pattern = "".join(
            p["type"]
            for p in points
        )

        if pattern == "LHLHLH":

            hook = calculate_hook(
                points,
                "SHORT",
                M5_INTERVAL
            )

            if hook:
                hooks.append(hook)

        elif pattern == "HLHLHL":

            hook = calculate_hook(
                points,
                "LONG",
                M5_INTERVAL
            )

            if hook:
                hooks.append(hook)

    return hooks, pivots, highs, lows


# ============================================================
# LATEST STRUCTURAL HOOK
# ============================================================

def latest_final_pivot_time(pivots, direction):

    required_type = (
        "H"
        if direction == "SHORT"
        else "L"
    )

    matching = [
        p for p in pivots
        if p["type"] == required_type
    ]

    if not matching:
        return None

    matching.sort(
        key=lambda x: (
            x["time"],
            x["index"]
        )
    )

    return matching[-1]["time"]


def filter_fresh_structural_hooks(
    hooks,
    pivots,
    direction
):

    latest_time = latest_final_pivot_time(
        pivots,
        direction
    )

    if latest_time is None:
        return []

    fresh = []

    for hook in hooks:

        final_time = (
            hook["h3_time"]
            if direction == "SHORT"
            else hook["l3_time"]
        )

        if final_time == latest_time:
            fresh.append(hook)
        else:
            DIAG["superseded"] += 1

    return fresh


# ============================================================
# TP CHECK
# ============================================================

def tp_already_touched_after_confirmation(
    df,
    hook
):

    confirmation = int(
        hook["confirmation_time"]
    )

    tp = safe_float(
        hook["tp"]
    )

    if tp is None:
        return True

    future = df[
        df["time"] > confirmation
    ]

    if future.empty:
        return False

    if hook["direction"] == "SHORT":

        lows = pd.to_numeric(
            future["low"],
            errors="coerce"
        )

        return bool(
            (lows <= tp).any()
        )

    highs = pd.to_numeric(
        future["high"],
        errors="coerce"
    )

    return bool(
        (highs >= tp).any()
    )


# ============================================================
# DISTANCE TO TP
# ============================================================

def tp_distance_pct(hook):

    entry = safe_float(
        hook["entry_price"]
    )

    tp = safe_float(
        hook["tp"]
    )

    if entry is None or tp is None:
        return 0.0

    if entry == 0:
        return 0.0

    return (
        abs(entry - tp)
        / abs(entry)
        * 100.0
    )


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

    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS hooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            start_time INTEGER NOT NULL,
            entry_time INTEGER NOT NULL,
            confirmation_time INTEGER NOT NULL,
            start_price REAL NOT NULL,
            entry_price REAL NOT NULL,
            tp REAL NOT NULL,
            sl REAL NOT NULL,
            distance_pct REAL NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE(
                symbol,
                direction,
                start_time,
                entry_time
            )
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_time INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            tp REAL NOT NULL,
            sl REAL NOT NULL,
            distance_pct REAL NOT NULL,
            status TEXT NOT NULL,
            opened_at INTEGER NOT NULL,
            closed_at INTEGER,
            exit_price REAL,
            pnl_pct REAL,
            result TEXT
        )
    """)

    conn.commit()

    conn.close()


def hook_exists(symbol, hook):

    conn = db_connect()

    cur = conn.cursor()

    row = cur.execute("""
        SELECT 1
        FROM hooks
        WHERE symbol = ?
          AND direction = ?
          AND start_time = ?
          AND entry_time = ?
        LIMIT 1
    """, (
        symbol,
        hook["direction"],
        hook["start_time"],
        (
            hook["h3_time"]
            if hook["direction"] == "SHORT"
            else hook["l3_time"]
        )
    )).fetchone()

    conn.close()

    return row is not None


def trade_exists(symbol, hook):

    entry_time = (
        hook["h3_time"]
        if hook["direction"] == "SHORT"
        else hook["l3_time"]
    )

    conn = db_connect()

    cur = conn.cursor()

    row = cur.execute("""
        SELECT 1
        FROM trades
        WHERE symbol = ?
          AND direction = ?
          AND entry_time = ?
        LIMIT 1
    """, (
        symbol,
        hook["direction"],
        entry_time
    )).fetchone()

    conn.close()

    return row is not None


def save_hook(symbol, hook):

    entry_time = (
        hook["h3_time"]
        if hook["direction"] == "SHORT"
        else hook["l3_time"]
    )

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        INSERT OR IGNORE INTO hooks (
            symbol,
            direction,
            start_time,
            entry_time,
            confirmation_time,
            start_price,
            entry_price,
            tp,
            sl,
            distance_pct,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        symbol,
        hook["direction"],
        hook["start_time"],
        entry_time,
        hook["confirmation_time"],
        hook["start"],
        hook["entry_price"],
        hook["tp"],
        hook["sl"],
        tp_distance_pct(hook),
        timestamp_now()
    ))

    conn.commit()

    conn.close()


# ============================================================
# TRADES
# ============================================================

def count_open_trades():

    conn = db_connect()

    row = conn.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'OPEN'
    """).fetchone()

    conn.close()

    return int(row[0] or 0)


def symbol_has_open_trade(symbol):

    conn = db_connect()

    row = conn.execute("""
        SELECT 1
        FROM trades
        WHERE symbol = ?
          AND status = 'OPEN'
        LIMIT 1
    """, (
        symbol,
    )).fetchone()

    conn.close()

    return row is not None


def create_paper_trade(symbol, hook):

    entry_time = (
        hook["h3_time"]
        if hook["direction"] == "SHORT"
        else hook["l3_time"]
    )

    distance = tp_distance_pct(hook)

    conn = db_connect()

    conn.execute("""
        INSERT INTO trades (
            symbol,
            direction,
            entry_time,
            entry_price,
            tp,
            sl,
            distance_pct,
            status,
            opened_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
    """, (
        symbol,
        hook["direction"],
        entry_time,
        hook["entry_price"],
        hook["tp"],
        hook["sl"],
        distance,
        timestamp_now()
    ))

    conn.commit()

    conn.close()


def get_open_trades():

    conn = db_connect()

    rows = conn.execute("""
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY opened_at ASC
    """).fetchall()

    conn.close()

    return rows


# ============================================================
# PAPER TRADE MONITOR
# ============================================================

def calculate_trade_pnl(direction, entry, current):

    if entry == 0:
        return 0.0

    if direction == "LONG":

        return (
            current - entry
        ) / entry * 100.0

    return (
        entry - current
    ) / entry * 100.0


def close_trade(
    trade_id,
    exit_price,
    result,
    pnl_pct
):

    conn = db_connect()

    conn.execute("""
        UPDATE trades
        SET
            status = 'CLOSED',
            closed_at = ?,
            exit_price = ?,
            pnl_pct = ?,
            result = ?
        WHERE id = ?
    """, (
        timestamp_now(),
        exit_price,
        pnl_pct,
        result,
        trade_id
    ))

    conn.commit()

    conn.close()


def monitor_open_trades():

    trades = get_open_trades()

    if not trades:
        return

    for trade in trades:

        symbol = trade["symbol"]

        df = get_candles(
            symbol,
            M5_INTERVAL,
            20
        )

        if df.empty:
            continue

        current = safe_float(
            df.iloc[-1]["close"]
        )

        if current is None:
            continue

        direction = trade["direction"]

        entry = float(
            trade["entry_price"]
        )

        tp = float(
            trade["tp"]
        )

        sl = float(
            trade["sl"]
        )

        result = None
        exit_price = None

        # ----------------------------------------------------
        # Conservative check:
        # SL is checked before TP when both could occur inside
        # the same candle.
        # ----------------------------------------------------

        last_candle = df.iloc[-1]

        high = float(last_candle["high"])
        low = float(last_candle["low"])

        if direction == "LONG":

            if low <= sl:
                result = "LOSS"
                exit_price = sl

            elif high >= tp:
                result = "WIN"
                exit_price = tp

        else:

            if high >= sl:
                result = "LOSS"
                exit_price = sl

            elif low <= tp:
                result = "WIN"
                exit_price = tp

        if result is not None:

            pnl = calculate_trade_pnl(
                direction,
                entry,
                exit_price
            )

            close_trade(
                trade["id"],
                exit_price,
                result,
                pnl
            )

            emoji = (
                "🟢"
                if result == "WIN"
                else "🔴"
            )

            text_msg = (
                f"{emoji} PAPER TRADE CLOSED\n\n"
                f"Symbol: {symbol}\n"
                f"Direction: {direction}\n"
                f"Entry: {fmt_price(entry)}\n"
                f"Exit: {fmt_price(exit_price)}\n"
                f"Result: {result}\n"
                f"PnL: {fmt_pct(pnl)}"
            )

            send_telegram_message(
                text_msg
            )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_message(text):

    if not TELEGRAM_BOT_TOKEN:
        return False

    if not TELEGRAM_CHAT_ID:
        return False

    try:

        url = (
            "https://api.telegram.org/"
            f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        )

        response = SESSION.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=HTTP_TIMEOUT
        )

        if response.ok:

            DIAG["telegram"] += 1

            return True

    except Exception as e:

        print(
            f"Telegram message error: {e}"
        )

    return False


def send_telegram_photo(
    image_path,
    caption
):

    if not TELEGRAM_BOT_TOKEN:
        return False

    if not TELEGRAM_CHAT_ID:
        return False

    if not os.path.exists(image_path):
        return False

    try:

        url = (
            "https://api.telegram.org/"
            f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        )

        with open(
            image_path,
            "rb"
        ) as photo:

            response = SESSION.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption,
                    "parse_mode": "HTML"
                },
                files={
                    "photo": photo
                },
                timeout=HTTP_TIMEOUT
            )

        if response.ok:

            DIAG["telegram"] += 1

            return True

    except Exception as e:

        print(
            f"Telegram photo error: {e}"
        )

    return False


# ============================================================
# CHART
# ============================================================

def make_hook_chart(
    symbol,
    df,
    hook
):

    os.makedirs(
        CHART_DIR,
        exist_ok=True
    )

    direction = hook["direction"]

    entry = hook["entry_price"]
    tp = hook["tp"]
    sl = hook["sl"]

    start_time = hook["start_time"]

    if direction == "SHORT":

        point_names = [
            ("START", hook["start_time"], hook["start"]),
            ("H1", hook["h1_time"], df.loc[
                df["time"] == hook["h1_time"],
                "high"
            ].iloc[0]),
            ("L1", hook["l1_time"], df.loc[
                df["time"] == hook["l1_time"],
                "low"
            ].iloc[0]),
            ("H2", hook["h2_time"], df.loc[
                df["time"] == hook["h2_time"],
                "high"
            ].iloc[0]),
            ("L2", hook["l2_time"], df.loc[
                df["time"] == hook["l2_time"],
                "low"
            ].iloc[0]),
            ("H3", hook["h3_time"], hook["entry_price"]),
        ]

    else:

        point_names = [
            ("START", hook["start_time"], hook["start"]),
            ("L1", hook["l1_time"], df.loc[
                df["time"] == hook["l1_time"],
                "low"
            ].iloc[0]),
            ("H1", hook["h1_time"], df.loc[
                df["time"] == hook["h1_time"],
                "high"
            ].iloc[0]),
            ("L2", hook["l2_time"], df.loc[
                df["time"] == hook["l2_time"],
                "low"
            ].iloc[0]),
            ("H2", hook["h2_time"], df.loc[
                df["time"] == hook["h2_time"],
                "high"
            ].iloc[0]),
            ("L3", hook["l3_time"], hook["entry_price"]),
        ]

    # --------------------------------------------------------
    # Chart window
    # --------------------------------------------------------

    relevant_times = [
        p[1]
        for p in point_names
    ]

    min_time = min(relevant_times)
    max_time = max(relevant_times)

    padding = 30 * 60

    chart_df = df[
        (df["time"] >= min_time - padding)
        &
        (df["time"] <= max_time + padding)
    ].copy()

    if chart_df.empty:
        chart_df = df.tail(240).copy()

    fig, ax = plt.subplots(
        figsize=(15, 8)
    )

    x = pd.to_datetime(
        chart_df["time"],
        unit="s",
        utc=True
    )

    ax.plot(
        x,
        chart_df["close"],
        linewidth=1.2,
        label="M5 Close"
    )

    # --------------------------------------------------------
    # Hook connecting line
    # --------------------------------------------------------

    px = [
        pd.to_datetime(t, unit="s", utc=True)
        for _, t, _ in point_names
    ]

    py = [
        p
        for _, _, p in point_names
    ]

    ax.plot(
        px,
        py,
        linewidth=2.0,
        marker="o",
        label="NDS Hook"
    )

    # --------------------------------------------------------
    # Labels
    # --------------------------------------------------------

    offsets = [
        (0, 15),
        (0, -20),
        (0, 15),
        (0, -20),
        (0, 15),
        (0, -20)
    ]

    for idx, (
        name,
        ts,
        price
    ) in enumerate(point_names):

        xt = pd.to_datetime(
            ts,
            unit="s",
            utc=True
        )

        ox, oy = offsets[
            idx % len(offsets)
        ]

        ax.annotate(
            name,
            xy=(xt, price),
            xytext=(ox, oy),
            textcoords="offset points",
            fontsize=10,
            fontweight="bold",
            ha="center",
            arrowprops={
                "arrowstyle": "->",
                "linewidth": 0.8
            }
        )

    # --------------------------------------------------------
    # Entry
    # --------------------------------------------------------

    ax.axhline(
        entry,
        linestyle="--",
        linewidth=1.3,
        label=f"ENTRY {fmt_price(entry)}"
    )

    # --------------------------------------------------------
    # TP 86.4
    # --------------------------------------------------------

    ax.axhline(
        tp,
        linestyle="-.",
        linewidth=1.8,
        label=f"TP 86.4% {fmt_price(tp)}"
    )

    # --------------------------------------------------------
    # SL
    # --------------------------------------------------------

    ax.axhline(
        sl,
        linestyle=":",
        linewidth=1.5,
        label=f"SL {fmt_price(sl)}"
    )

    # --------------------------------------------------------
    # Confirmation
    # --------------------------------------------------------

    confirmation_dt = pd.to_datetime(
        hook["confirmation_time"],
        unit="s",
        utc=True
    )

    ax.axvline(
        confirmation_dt,
        linestyle="--",
        linewidth=1.0,
        label="Hook Confirmed"
    )

    ax.set_title(
        f"NDS M5 {symbol} | {direction} | "
        f"86.4% Retracement"
    )

    ax.set_xlabel(
        "Time UTC"
    )

    ax.set_ylabel(
        "Price"
    )

    ax.grid(
        True,
        alpha=0.25
    )

    ax.legend(
        loc="best"
    )

    fig.autofmt_xdate()

    filename = (
        f"{symbol}_"
        f"{direction}_"
        f"{hook['start_time']}_"
        f"{hook['h3_time'] if direction == 'SHORT' else hook['l3_time']}.png"
    )

    filename = filename.replace(
        "/",
        "_"
    )

    path = os.path.join(
        CHART_DIR,
        filename
    )

    fig.tight_layout()

    fig.savefig(
        path,
        dpi=140
    )

    plt.close(fig)

    DIAG["charts"] += 1

    return path


# ============================================================
# H4 DIRECTION
# ============================================================

def get_h4_direction(symbol):

    DIAG["h4_requests"] += 1

    df = get_candles(
        symbol,
        H4_INTERVAL,
        H4_CANDLES
    )

    if df.empty or len(df) < 30:

        DIAG["h4_error"] += 1

        return None

    DIAG["h4_ok"] += 1

    hooks, pivots, highs, lows = detect_hooks(
        df
    )

    if not hooks:

        DIAG["h4_no_hook"] += 1

        return None

    hooks.sort(
        key=lambda h: h["confirmation_time"]
    )

    latest = hooks[-1]

    if latest["direction"] == "SHORT":

        DIAG["h4_short"] += 1

        return "SHORT"

    DIAG["h4_long"] += 1

    return "LONG"


# ============================================================
# SELECT TRADEABLE HOOK
# ============================================================

def select_tradeable_hook(
    symbol,
    hooks,
    pivots,
    df,
    direction
):

    if not hooks:
        return None

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # Do NOT use age.
    #
    # Only hooks ending at the latest structural H3/L3
    # are considered fresh.
    # --------------------------------------------------------

    fresh_hooks = filter_fresh_structural_hooks(
        hooks,
        pivots,
        direction
    )

    if not fresh_hooks:
        return None

    candidates = []

    for hook in fresh_hooks:

        # ----------------------------------------------------
        # Duplicate hook
        # ----------------------------------------------------

        if hook_exists(
            symbol,
            hook
        ):

            DIAG["duplicate"] += 1

            continue

        if trade_exists(
            symbol,
            hook
        ):

            DIAG["duplicate"] += 1

            continue

        # ----------------------------------------------------
        # TP already reached
        # ----------------------------------------------------

        if tp_already_touched_after_confirmation(
            df,
            hook
        ):

            DIAG["tp_touched"] += 1

            continue

        # ----------------------------------------------------
        # Distance
        # ----------------------------------------------------

        distance = tp_distance_pct(
            hook
        )

        hook["_tp_distance_pct"] = distance

        candidates.append(
            hook
        )

    if not candidates:
        return None

    # --------------------------------------------------------
    # MAX DISTANCE TO 86.4%
    # --------------------------------------------------------

    candidates.sort(
        key=lambda h: (
            h["_tp_distance_pct"],
            h["confirmation_time"]
        ),
        reverse=True
    )

    return candidates[0]


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def build_signal_message(
    symbol,
    hook
):

    direction = hook["direction"]

    entry = hook["entry_price"]
    tp = hook["tp"]
    sl = hook["sl"]

    distance = tp_distance_pct(
        hook
    )

    confirmation = iso_from_timestamp(
        hook["confirmation_time"]
    )

    if direction == "SHORT":

        points = (
            f"START {fmt_price(hook['start'])}\n"
            f"H1 {fmt_price(hook['entry_price'])}\n"
        )

        point_text = (
            f"START: {fmt_price(hook['start'])}\n"
            f"H1: {fmt_price("
            f"get_point_price_from_hook(hook, 'h1')"
            f")}\n"
            f"L1: {fmt_price("
            f"get_point_price_from_hook(hook, 'l1')"
            f")}\n"
            f"H2: {fmt_price("
            f"get_point_price_from_hook(hook, 'h2')"
            f")}\n"
            f"L2: {fmt_price("
            f"get_point_price_from_hook(hook, 'l2')"
            f")}\n"
            f"H3: {fmt_price(entry)}"
        )

    else:

        point_text = (
            f"START: {fmt_price(hook['start'])}\n"
            f"L1: {fmt_price("
            f"get_point_price_from_hook(hook, 'l1')"
            f")}\n"
            f"H1: {fmt_price("
            f"get_point_price_from_hook(hook, 'h1')"
            f")}\n"
            f"L2: {fmt_price("
            f"get_point_price_from_hook(hook, 'l2')"
            f")}\n"
            f"H2: {fmt_price("
            f"get_point_price_from_hook(hook, 'h2')"
            f")}\n"
            f"L3: {fmt_price(entry)}"
        )

    emoji = (
        "🔴"
        if direction == "SHORT"
        else "🟢"
    )

    return (
        f"⭐ <b>FRESH NDS HOOK SELECTED</b>\n\n"
        f"{emoji} <b>{direction}</b>\n"
        f"<b>{symbol}</b>\n\n"

        f"<b>ENTRY:</b> {fmt_price(entry)}\n"
        f"<b>TP 86.4%:</b> {fmt_price(tp)}\n"
        f"<b>SL:</b> {fmt_price(sl)}\n\n"

        f"<b>86.4% DISTANCE:</b> "
        f"{distance:.2f}%\n\n"

        f"<b>HOOK POINTS</b>\n"
        f"{point_text}\n\n"

        f"<b>CONFIRMED:</b>\n"
        f"{confirmation}\n\n"

        f"🟡 <b>PAPER TRADE OPENED</b>\n"
        f"No real exchange order sent."
    )


def get_point_price_from_hook(
    hook,
    point
):
    # The hook itself stores only START and final point prices.
    # Intermediate prices are reconstructed from the dataframe
    # when the chart is made. For Telegram, return placeholders
    # if unavailable.
    #
    # process_symbol replaces these values before sending.
    return hook.get(
        f"{point}_price",
        0
    )


# ============================================================
# PERFORMANCE
# ============================================================

def get_performance():

    conn = db_connect()

    row = conn.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(
                CASE
                    WHEN result = 'WIN'
                    THEN 1
                    ELSE 0
                END
            ) AS wins,
            SUM(
                CASE
                    WHEN result = 'LOSS'
                    THEN 1
                    ELSE 0
                END
            ) AS losses,
            COALESCE(
                SUM(pnl_pct),
                0
            ) AS pnl
        FROM trades
        WHERE status = 'CLOSED'
    """).fetchone()

    open_count = conn.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'OPEN'
    """).fetchone()[0]

    conn.close()

    total = int(
        row["total"] or 0
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
        wins / total * 100
        if total > 0
        else 0
    )

    return {
        "closed": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "pnl": pnl,
        "open": int(open_count)
    }


# ============================================================
# OPEN TRADES TELEGRAM REPORT
# ============================================================

def build_open_trades_report():

    trades = get_open_trades()

    if not trades:

        return (
            "━━━ <b>OPEN TRADES</b> ━━━\n"
            "No open trades."
        )

    lines = [
        "━━━ <b>OPEN TRADES</b> ━━━"
    ]

    for trade in trades:

        symbol = trade["symbol"]

        direction = trade["direction"]

        entry = float(
            trade["entry_price"]
        )

        tp = float(
            trade["tp"]
        )

        sl = float(
            trade["sl"]
        )

        df = get_candles(
            symbol,
            M5_INTERVAL,
            10
        )

        if df.empty:

            current = entry

        else:

            current = float(
                df.iloc[-1]["close"]
            )

        pnl = calculate_trade_pnl(
            direction,
            entry,
            current
        )

        if direction == "LONG":

            to_tp = (
                (tp - current)
                / current
                * 100
            )

            to_sl = (
                (current - sl)
                / current
                * 100
            )

        else:

            to_tp = (
                (current - tp)
                / current
                * 100
            )

            to_sl = (
                (sl - current)
                / current
                * 100
            )

        lines.append(
            "\n"
            f"{'🟢' if direction == 'LONG' else '🔴'} "
            f"<b>{symbol} {direction}</b>\n"
            f"Entry: {fmt_price(entry)}\n"
            f"Current: {fmt_price(current)}\n"
            f"TP: {fmt_price(tp)} "
            f"({to_tp:+.2f}%)\n"
            f"SL: {fmt_price(sl)} "
            f"({to_sl:+.2f}%)\n"
            f"PnL: <b>{pnl:+.2f}%</b>"
        )

    return "\n".join(lines)


# ============================================================
# PROCESS ONE SYMBOL
# ============================================================

def process_symbol(symbol):

    DIAG["symbols_scanned"] += 1

    # --------------------------------------------------------
    # H4 direction
    # --------------------------------------------------------

    direction = get_h4_direction(
        symbol
    )

    if direction is None:

        DIAG["h4_filtered"] += 1

        return

    # --------------------------------------------------------
    # M5
    # --------------------------------------------------------

    DIAG["m5_requests"] += 1

    df = get_candles(
        symbol,
        M5_INTERVAL,
        M5_CANDLES
    )

    if df.empty or len(df) < 100:

        DIAG["m5_error"] += 1

        return

    DIAG["m5_ok"] += 1

    hooks, pivots, highs, lows = detect_hooks(
        df
    )

    matching_hooks = [
        h
        for h in hooks
        if h["direction"] == direction
    ]

    if direction == "SHORT":

        DIAG["matching_short"] += len(
            matching_hooks
        )

    else:

        DIAG["matching_long"] += len(
            matching_hooks
        )

    if not matching_hooks:

        DIAG["no_matching_hook"] += 1

        return

    # --------------------------------------------------------
    # Current structural Hook
    # --------------------------------------------------------

    hook = select_tradeable_hook(
        symbol,
        matching_hooks,
        pivots,
        df,
        direction
    )

    if hook is None:

        return

    # --------------------------------------------------------
    # One open trade per symbol
    # --------------------------------------------------------

    if symbol_has_open_trade(
        symbol
    ):

        DIAG["open_trade_blocked"] += 1

        return

    # --------------------------------------------------------
    # Maximum open trades
    # --------------------------------------------------------

    if count_open_trades() >= MAX_OPEN_TRADES:

        DIAG["max_open_blocked"] += 1

        return

    # --------------------------------------------------------
    # Reconstruct intermediate point prices for Telegram
    # --------------------------------------------------------

    point_time_map = {
        "h1": hook.get("h1_time"),
        "l1": hook.get("l1_time"),
        "h2": hook.get("h2_time"),
        "l2": hook.get("l2_time"),
    }

    for name, ts in point_time_map.items():

        if ts is None:
            continue

        row = df[
            df["time"] == ts
        ]

        if row.empty:
            continue

        if name.startswith("h"):

            hook[
                f"{name}_price"
            ] = float(
                row.iloc[0]["high"]
            )

        else:

            hook[
                f"{name}_price"
            ] = float(
                row.iloc[0]["low"]
            )

    # --------------------------------------------------------
    # Save Hook
    # --------------------------------------------------------

    save_hook(
        symbol,
        hook
    )

    # --------------------------------------------------------
    # Paper trade
    # --------------------------------------------------------

    create_paper_trade(
        symbol,
        hook
    )

    DIAG["signals"] += 1

    # --------------------------------------------------------
    # Chart
    # --------------------------------------------------------

    chart_path = make_hook_chart(
        symbol,
        df,
        hook
    )

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    message = build_signal_message(
        symbol,
        hook
    )

    send_telegram_message(
        message
    )

    if chart_path:

        send_telegram_photo(
            chart_path,
            (
                f"NDS M5 {symbol} | "
                f"{direction} | "
                f"TP 86.4% = "
                f"{fmt_price(hook['tp'])}"
            )
        )


# ============================================================
# DIAGNOSTIC REPORT
# ============================================================

def print_diagnostic():

    print()
    print("🔎 NDS H4 → M5 DIAGNOSTIC")
    print(f"Version: {VERSION}")
    print(f"Time: {utc_now_str()}")
    print()

    print("━━━ H4 ━━━")
    print(
        f"Requests: {DIAG['h4_requests']}"
    )
    print(
        f"Data OK: {DIAG['h4_ok']}"
    )
    print(
        f"Data Error: {DIAG['h4_error']}"
    )
    print(
        f"No Valid Hook: {DIAG['h4_no_hook']}"
    )
    print(
        f"H4 Filtered: {DIAG['h4_filtered']}"
    )
    print(
        f"SHORT Hooks: {DIAG['h4_short']}"
    )
    print(
        f"LONG Hooks: {DIAG['h4_long']}"
    )

    print()

    print("━━━ M5 ━━━")
    print(
        f"Requests: {DIAG['m5_requests']}"
    )
    print(
        f"Data OK: {DIAG['m5_ok']}"
    )
    print(
        f"Data Error: {DIAG['m5_error']}"
    )
    print(
        f"Short Data: {DIAG['m5_short_data']}"
    )
    print(
        f"Matching SHORT Hooks: "
        f"{DIAG['matching_short']}"
    )
    print(
        f"Matching LONG Hooks: "
        f"{DIAG['matching_long']}"
    )
    print(
        f"No Matching Hook: "
        f"{DIAG['no_matching_hook']}"
    )

    print(
        f"Superseded / Old Hooks Removed: "
        f"{DIAG['superseded']}"
    )

    print(
        f"TP Already Touched: "
        f"{DIAG['tp_touched']}"
    )

    print(
        f"Duplicate: "
        f"{DIAG['duplicate']}"
    )

    print(
        f"Open Trade Blocked: "
        f"{DIAG['open_trade_blocked']}"
    )

    print(
        f"Max Open Blocked: "
        f"{DIAG['max_open_blocked']}"
    )

    print()

    print("━━━ SIGNALS ━━━")
    print(
        f"New Signals: "
        f"{DIAG['signals']}"
    )

    print(
        f"Charts: "
        f"{DIAG['charts']}"
    )

    print()

    # --------------------------------------------------------
    # Open trades
    # --------------------------------------------------------

    print(
        build_open_trades_report()
        .replace("<b>", "")
        .replace("</b>", "")
    )

    print()

    # --------------------------------------------------------
    # Performance
    # --------------------------------------------------------

    perf = get_performance()

    print("━━━ PAPER PERFORMANCE ━━━")

    print(
        f"Closed: {perf['closed']}"
    )

    print(
        f"Wins: {perf['wins']}"
    )

    print(
        f"Losses: {perf['losses']}"
    )

    print(
        f"Win Rate: "
        f"{perf['win_rate']:.2f}%"
    )

    print(
        f"Realized PnL: "
        f"{perf['pnl']:+.2f}%"
    )

    print(
        f"Open Trades: "
        f"{perf['open']}"
    )

    print()

    print(
        "🟡 PAPER ONLY"
    )

    print(
        "No real exchange orders are sent."
    )


# ============================================================
# TELEGRAM REPORT
# ============================================================

def send_report():

    perf = get_performance()

    message = (
        f"🔎 <b>NDS H4 → M5</b>\n"
        f"Version: <b>{VERSION}</b>\n"
        f"Time: {utc_now_str()}\n\n"

        f"━━━ <b>SCAN</b> ━━━\n"
        f"Assets: {DIAG['symbols_scanned']}\n"
        f"Signals: {DIAG['signals']}\n"
        f"Charts: {DIAG['charts']}\n\n"

        f"━━━ <b>FRESHNESS</b> ━━━\n"
        f"Superseded: "
        f"{DIAG['superseded']}\n"
        f"TP Already Touched: "
        f"{DIAG['tp_touched']}\n"
        f"Duplicates: "
        f"{DIAG['duplicate']}\n\n"

        f"━━━ <b>PAPER PERFORMANCE</b> ━━━\n"
        f"Closed: {perf['closed']}\n"
        f"Wins: {perf['wins']}\n"
        f"Losses: {perf['losses']}\n"
        f"Win Rate: {perf['win_rate']:.2f}%\n"
        f"Realized PnL: "
        f"{perf['pnl']:+.2f}%\n"
        f"Open Trades: {perf['open']}\n\n"

        f"🟡 <b>PAPER ONLY</b>\n"
        f"No real exchange orders."
    )

    send_telegram_message(
        message
    )

    open_report = build_open_trades_report()

    send_telegram_message(
        open_report
    )


# ============================================================
# MAIN
# ============================================================

def main():

    start = time.time()

    init_db()

    # --------------------------------------------------------
    # First monitor already-open paper trades.
    # --------------------------------------------------------

    try:

        monitor_open_trades()

    except Exception as e:

        print(
            f"Open trade monitor error: {e}"
        )

        traceback.print_exc()

    # --------------------------------------------------------
    # Instruments
    # --------------------------------------------------------

    symbols = get_futures_instruments()

    if not symbols:

        print(
            "No futures instruments found."
        )

        print_diagnostic()

        return

    print(
        f"Scanning {len(symbols)} Kraken Futures assets..."
    )

    # --------------------------------------------------------
    # Scan
    # --------------------------------------------------------

    for index, symbol in enumerate(
        symbols,
        start=1
    ):

        try:

            print(
                f"[{index}/{len(symbols)}] "
                f"{symbol}"
            )

            process_symbol(
                symbol
            )

        except Exception as e:

            print(
                f"ERROR {symbol}: {e}"
            )

            traceback.print_exc()

    # --------------------------------------------------------
    # Diagnostic
    # --------------------------------------------------------

    runtime = time.time() - start

    print()

    print_diagnostic()

    print()

    print(
        f"Runtime: {runtime:.1f}s"
    )

    # --------------------------------------------------------
    # Telegram summary
    # --------------------------------------------------------

    try:

        send_report()

    except Exception as e:

        print(
            f"Telegram report error: {e}"
        )


if __name__ == "__main__":

    main()
