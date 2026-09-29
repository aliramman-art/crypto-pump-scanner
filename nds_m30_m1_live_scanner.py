# ============================================================
# NDS H4 -> M5 LIVE SCANNER
# VERSION 5.1.0
# ============================================================
#
# PAPER ONLY
#
# LOGIC
#
# H4:
#   Detect valid NDS 6-point Hook
#   Positive Hook  -> SHORT permission
#   Negative Hook  -> LONG permission
#
# M5:
#   Only scan the SAME direction allowed by H4
#   Positive Hook  -> SHORT
#   Negative Hook  -> LONG
#
# ENTRY:
#   SHORT = H3
#   LONG  = L3
#
# TP:
#   86.4% retracement
#
# SL:
#   50% of TP distance beyond entry
#
# IMPORTANT:
#   86.4% is TP, NOT trade start.
#
# PAPER ONLY:
#   No exchange orders are ever sent.
# ============================================================

import os
import time
import math
import json
import hashlib
import sqlite3
from datetime import datetime, timezone

import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


# ============================================================
# CONFIG
# ============================================================

DB_FILE = "nds_h4_m5_live_v51.db"
CHART_DIR = "nds_h4_m5_charts"

KRAKEN_URL = "https://futures.kraken.com/api/charts/v1/trade"

INTERVAL_H4 = "4h"
INTERVAL_M5 = "5m"

H4_COUNT = 320
M5_COUNT = 500

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

MIN_SWING_PCT = 0.0005

TP_RETRACE = 0.864

MIN_TP_DISTANCE_PCT = 0.30
MAX_TP_DISTANCE_PCT = 5.00

SL_TP_MULTIPLIER = 0.50

CHART_CANDLES = 180

SCAN_SECONDS = 300
REPORT_SECONDS = 900

REQUEST_TIMEOUT = 20

# Prevent an old historical hook from becoming a new signal
# on the first run.
MAX_NEW_SIGNAL_AGE_SECONDS = 20 * 60

# Maximum open paper trades.
MAX_OPEN_TRADES = 3


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
# TELEGRAM
# ============================================================

TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("TELEGRAM_TOKEN")
)

TELEGRAM_CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or os.getenv("TELEGRAM_CHAT")
)


# ============================================================
# GLOBALS
# ============================================================

session = requests.Session()

os.makedirs(CHART_DIR, exist_ok=True)


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_now_str():
    return utc_now().strftime("%Y-%m-%d %H:%M:%S UTC")


def ts_to_str(ts):
    try:
        return pd.Timestamp(ts, tz="UTC").strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    except Exception:
        return str(ts)


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials not configured.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        r = session.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        return r.ok
    except Exception as e:
        print("Telegram error:", e)
        return False


def telegram_photo(path, caption):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_TOKEN}/sendPhoto"
    )

    try:
        with open(path, "rb") as f:
            files = {
                "photo": f
            }

            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
                "parse_mode": "HTML",
            }

            r = session.post(
                url,
                data=data,
                files=files,
                timeout=REQUEST_TIMEOUT,
            )

        return r.ok

    except Exception as e:
        print("Telegram photo error:", e)
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
        CREATE TABLE IF NOT EXISTS hooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hook_id TEXT UNIQUE,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,
            start_time TEXT NOT NULL,
            start_price REAL NOT NULL,
            entry_time TEXT NOT NULL,
            entry_price REAL NOT NULL,
            tp REAL NOT NULL,
            sl REAL NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hook_id TEXT UNIQUE,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_time TEXT NOT NULL,
            entry_price REAL NOT NULL,
            tp REAL NOT NULL,
            sl REAL NOT NULL,
            exit_time TEXT,
            exit_price REAL,
            result TEXT,
            pnl_pct REAL,
            created_at TEXT NOT NULL
        )
        """
    )

    conn.commit()
    conn.close()


# ============================================================
# KRAKEN DATA
# ============================================================

def fetch_candles(symbol, interval, count):

    url = (
        f"{KRAKEN_URL}/"
        f"{symbol}/"
        f"{interval}"
    )

    try:

        r = session.get(
            url,
            timeout=REQUEST_TIMEOUT,
        )

        if not r.ok:
            print(
                f"{symbol} {interval}: HTTP {r.status_code}"
            )
            return None

        data = r.json()

        candles = data.get("candles", [])

        if not candles:
            return None

        rows = []

        for c in candles[-count:]:

            try:
                if isinstance(c, dict):

                    ts = (
                        c.get("time")
                        or c.get("timestamp")
                        or c.get("ts")
                    )

                    o = c.get("open")
                    h = c.get("high")
                    l = c.get("low")
                    close = c.get("close")
                    volume = c.get("volume", 0)

                else:

                    if len(c) < 6:
                        continue

                    ts = c[0]
                    o = c[1]
                    h = c[2]
                    l = c[3]
                    close = c[4]
                    volume = c[5]

                rows.append(
                    [
                        pd.to_datetime(
                            ts,
                            unit="s",
                            utc=True
                        ),
                        float(o),
                        float(h),
                        float(l),
                        float(close),
                        float(volume),
                    ]
                )

            except Exception:
                continue

        if not rows:
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

        return df

    except Exception as e:

        print(
            f"{symbol} {interval} fetch error: {e}"
        )

        return None


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(df):

    highs = []
    lows = []

    n = len(df)

    if n < PIVOT_LEFT + PIVOT_RIGHT + 5:
        return highs, lows

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT
    ):

        value_high = float(df.iloc[i]["high"])
        value_low = float(df.iloc[i]["low"])

        left_highs = [
            float(df.iloc[j]["high"])
            for j in range(
                i - PIVOT_LEFT,
                i
            )
        ]

        right_highs = [
            float(df.iloc[j]["high"])
            for j in range(
                i + 1,
                i + PIVOT_RIGHT + 1
            )
        ]

        left_lows = [
            float(df.iloc[j]["low"])
            for j in range(
                i - PIVOT_LEFT,
                i
            )
        ]

        right_lows = [
            float(df.iloc[j]["low"])
            for j in range(
                i + 1,
                i + PIVOT_RIGHT + 1
            )
        ]

        if (
            value_high > max(left_highs)
            and value_high >= max(right_highs)
        ):
            highs.append(
                {
                    "kind": "H",
                    "index": i,
                    "time": df.iloc[i]["time"],
                    "price": value_high,
                }
            )

        if (
            value_low < min(left_lows)
            and value_low <= min(right_lows)
        ):
            lows.append(
                {
                    "kind": "L",
                    "index": i,
                    "time": df.iloc[i]["time"],
                    "price": value_low,
                }
            )

    return highs, lows


# ============================================================
# PIVOT SEQUENCE
# ============================================================

def combined_pivots(highs, lows):

    points = highs + lows

    points.sort(
        key=lambda x: x["time"]
    )

    return points


# ============================================================
# HOOK ID
# ============================================================

def make_hook_id(symbol, timeframe, hook):

    raw = "|".join(
        [
            symbol,
            timeframe,
            hook["direction"],
            str(hook["start_time"]),
            str(hook["start_price"]),
            str(hook["entry_time"]),
            str(hook["entry_price"]),
        ]
    )

    return hashlib.sha256(
        raw.encode()
    ).hexdigest()


# ============================================================
# TP / SL
# ============================================================

def calculate_levels(direction, start, entry):

    if direction == "SHORT":

        tp = (
            entry
            - TP_RETRACE * (entry - start)
        )

        sl = (
            entry
            + SL_TP_MULTIPLIER
            * abs(entry - tp)
        )

    else:

        tp = (
            entry
            + TP_RETRACE * (start - entry)
        )

        sl = (
            entry
            - SL_TP_MULTIPLIER
            * abs(entry - tp)
        )

    distance_pct = (
        abs(entry - tp)
        / entry
        * 100
    )

    return tp, sl, distance_pct


# ============================================================
# CHECK TP ALREADY TOUCHED AFTER ENTRY
# ============================================================

def tp_already_touched(
    df,
    direction,
    entry_index,
    tp
):

    future = df.iloc[
        entry_index + 1:
    ]

    if future.empty:
        return False

    if direction == "SHORT":

        return bool(
            (
                future["low"]
                <= tp
            ).any()
        )

    return bool(
        (
            future["high"]
            >= tp
        ).any()
    )


# ============================================================
# VALID POSITIVE HOOK
#
# START -> H1 -> L1 -> H2 -> L2 -> H3
#
# SHORT
#
# START = lowest
# H2 > H1
# L2 < L1
# H3 > H2
# ============================================================

def validate_positive_hook(
    points,
    df
):

    if len(points) < 6:
        return []

    results = []

    for i in range(
        len(points) - 5
    ):

        p = points[i:i + 6]

        kinds = [
            x["kind"]
            for x in p
        ]

        if kinds != [
            "L",
            "H",
            "L",
            "H",
            "L",
            "H",
        ]:
            continue

        start = p[0]
        h1 = p[1]
        l1 = p[2]
        h2 = p[3]
        l2 = p[4]
        h3 = p[5]

        # START must be the lowest point.
        if start["price"] != min(
            x["price"] for x in p
        ):
            continue

        # Required Hook structure.
        if not (
            h2["price"] > h1["price"]
            and l2["price"] < l1["price"]
            and h3["price"] > h2["price"]
        ):
            continue

        # Minimum overall movement.
        hook_range = (
            h3["price"] - start["price"]
        )

        if hook_range <= 0:
            continue

        range_pct = (
            hook_range
            / start["price"]
            * 100
        )

        if range_pct < (
            MIN_SWING_PCT * 100
        ):
            continue

        entry = h3["price"]

        tp, sl, distance_pct = (
            calculate_levels(
                "SHORT",
                start["price"],
                entry,
            )
        )

        if (
            distance_pct
            < MIN_TP_DISTANCE_PCT
        ):
            continue

        if (
            distance_pct
            > MAX_TP_DISTANCE_PCT
        ):
            continue

        if tp >= entry:
            continue

        if sl <= entry:
            continue

        if tp_already_touched(
            df,
            "SHORT",
            h3["index"],
            tp,
        ):
            continue

        results.append(
            {
                "direction": "SHORT",
                "start_time": start["time"],
                "start_price": start["price"],
                "h1_time": h1["time"],
                "h1_price": h1["price"],
                "l1_time": l1["time"],
                "l1_price": l1["price"],
                "h2_time": h2["time"],
                "h2_price": h2["price"],
                "l2_time": l2["time"],
                "l2_price": l2["price"],
                "entry_time": h3["time"],
                "entry_price": entry,
                "tp": tp,
                "sl": sl,
                "range_pct": range_pct,
                "tp_distance_pct": distance_pct,
            }
        )

    return results


# ============================================================
# VALID NEGATIVE HOOK
#
# START -> L1 -> H1 -> L2 -> H2 -> L3
#
# LONG
#
# START = highest
# L2 < L1
# H2 > H1
# L3 < L2
# ============================================================

def validate_negative_hook(
    points,
    df
):

    if len(points) < 6:
        return []

    results = []

    for i in range(
        len(points) - 5
    ):

        p = points[i:i + 6]

        kinds = [
            x["kind"]
            for x in p
        ]

        if kinds != [
            "H",
            "L",
            "H",
            "L",
            "H",
            "L",
        ]:
            continue

        start = p[0]
        l1 = p[1]
        h1 = p[2]
        l2 = p[3]
        h2 = p[4]
        l3 = p[5]

        # START must be the highest point.
        if start["price"] != max(
            x["price"] for x in p
        ):
            continue

        # Required Hook structure.
        if not (
            l2["price"] < l1["price"]
            and h2["price"] > h1["price"]
            and l3["price"] < l2["price"]
        ):
            continue

        hook_range = (
            start["price"] - l3["price"]
        )

        if hook_range <= 0:
            continue

        range_pct = (
            hook_range
            / start["price"]
            * 100
        )

        if range_pct < (
            MIN_SWING_PCT * 100
        ):
            continue

        entry = l3["price"]

        tp, sl, distance_pct = (
            calculate_levels(
                "LONG",
                start["price"],
                entry,
            )
        )

        if (
            distance_pct
            < MIN_TP_DISTANCE_PCT
        ):
            continue

        if (
            distance_pct
            > MAX_TP_DISTANCE_PCT
        ):
            continue

        if tp <= entry:
            continue

        if sl >= entry:
            continue

        if tp_already_touched(
            df,
            "LONG",
            l3["index"],
            tp,
        ):
            continue

        results.append(
            {
                "direction": "LONG",
                "start_time": start["time"],
                "start_price": start["price"],
                "l1_time": l1["time"],
                "l1_price": l1["price"],
                "h1_time": h1["time"],
                "h1_price": h1["price"],
                "l2_time": l2["time"],
                "l2_price": l2["price"],
                "h2_time": h2["time"],
                "h2_price": h2["price"],
                "entry_time": l3["time"],
                "entry_price": entry,
                "tp": tp,
                "sl": sl,
                "range_pct": range_pct,
                "tp_distance_pct": distance_pct,
            }
        )

    return results


# ============================================================
# DETECT HOOKS
# ============================================================

def detect_hooks(
    symbol,
    timeframe,
    df,
    allowed_direction=None
):

    highs, lows = find_pivots(df)

    points = combined_pivots(
        highs,
        lows
    )

    positive = validate_positive_hook(
        points,
        df
    )

    negative = validate_negative_hook(
        points,
        df
    )

    hooks = (
        positive
        + negative
    )

    if allowed_direction:
        hooks = [
            h
            for h in hooks
            if h["direction"]
            == allowed_direction
        ]

    if not hooks:
        return None, {
            "pivots_high": len(highs),
            "pivots_low": len(lows),
            "hooks": 0,
        }

    hooks.sort(
        key=lambda x: x["entry_time"]
    )

    hook = hooks[-1]

    hook["symbol"] = symbol
    hook["timeframe"] = timeframe

    hook["hook_id"] = make_hook_id(
        symbol,
        timeframe,
        hook
    )

    return hook, {
        "pivots_high": len(highs),
        "pivots_low": len(lows),
        "hooks": len(hooks),
    }


# ============================================================
# DATABASE HOOK
# ============================================================

def save_hook(hook):

    conn = db_connect()

    cur = conn.execute(
        """
        INSERT OR IGNORE INTO hooks (
            hook_id,
            symbol,
            timeframe,
            direction,
            start_time,
            start_price,
            entry_time,
            entry_price,
            tp,
            sl,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            hook["hook_id"],
            hook["symbol"],
            hook["timeframe"],
            hook["direction"],
            str(hook["start_time"]),
            hook["start_price"],
            str(hook["entry_time"]),
            hook["entry_price"],
            hook["tp"],
            hook["sl"],
            utc_now_str(),
        ),
    )

    conn.commit()

    inserted = (
        cur.rowcount == 1
    )

    conn.close()

    return inserted


# ============================================================
# OPEN TRADE COUNT
# ============================================================

def count_open_trades():

    conn = db_connect()

    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM paper_trades
        WHERE exit_time IS NULL
        """
    ).fetchone()

    conn.close()

    return int(row["c"])


# ============================================================
# CHECK SYMBOL OPEN TRADE
# ============================================================

def symbol_has_open_trade(symbol):

    conn = db_connect()

    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM paper_trades
        WHERE symbol = ?
          AND exit_time IS NULL
        """,
        (symbol,),
    ).fetchone()

    conn.close()

    return int(row["c"]) > 0


# ============================================================
# SAVE PAPER TRADE
# ============================================================

def save_paper_trade(hook):

    if count_open_trades() >= MAX_OPEN_TRADES:
        return False

    if symbol_has_open_trade(
        hook["symbol"]
    ):
        return False

    conn = db_connect()

    conn.execute(
        """
        INSERT OR IGNORE INTO paper_trades (
            hook_id,
            symbol,
            direction,
            entry_time,
            entry_price,
            tp,
            sl,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            hook["hook_id"],
            hook["symbol"],
            hook["direction"],
            str(hook["entry_time"]),
            hook["entry_price"],
            hook["tp"],
            hook["sl"],
            utc_now_str(),
        ),
    )

    conn.commit()
    conn.close()

    return True


# ============================================================
# EXIT TRADE
# ============================================================

def close_trade(
    trade_id,
    exit_price,
    result
):

    conn = db_connect()

    row = conn.execute(
        """
        SELECT
            direction,
            entry_price
        FROM paper_trades
        WHERE id = ?
        """,
        (trade_id,),
    ).fetchone()

    if not row:
        conn.close()
        return

    direction = row["direction"]
    entry = float(
        row["entry_price"]
    )

    if direction == "LONG":

        pnl_pct = (
            exit_price - entry
        ) / entry * 100

    else:

        pnl_pct = (
            entry - exit_price
        ) / entry * 100

    conn.execute(
        """
        UPDATE paper_trades
        SET
            exit_time = ?,
            exit_price = ?,
            result = ?,
            pnl_pct = ?
        WHERE id = ?
        """,
        (
            utc_now_str(),
            exit_price,
            result,
            pnl_pct,
            trade_id,
        ),
    )

    conn.commit()
    conn.close()

    emoji = (
        "✅"
        if result == "TP"
        else "❌"
    )

    telegram_send(
        f"{emoji} <b>{result}</b>\n"
        f"<b>{direction}</b> {row and ''}\n"
        f"Exit: <b>{exit_price:.8f}</b>\n"
        f"PnL: <b>{pnl_pct:+.2f}%</b>"
    )


# ============================================================
# MONITOR OPEN TRADES
#
# Uses M5 HIGH/LOW rather than close only.
# ============================================================

def monitor_open_trades(
    symbol,
    df
):

    if df is None or df.empty:
        return

    last = df.iloc[-1]

    high = float(
        last["high"]
    )

    low = float(
        last["low"]
    )

    conn = db_connect()

    trades = conn.execute(
        """
        SELECT *
        FROM paper_trades
        WHERE symbol = ?
          AND exit_time IS NULL
        """,
        (symbol,),
    ).fetchall()

    conn.close()

    for trade in trades:

        direction = trade["direction"]

        tp = float(
            trade["tp"]
        )

        sl = float(
            trade["sl"]
        )

        # Conservative rule:
        # if both TP and SL are touched
        # inside one candle, assume SL first.
        if direction == "LONG":

            hit_tp = high >= tp
            hit_sl = low <= sl

            if hit_sl:
                close_trade(
                    trade["id"],
                    sl,
                    "SL"
                )

            elif hit_tp:
                close_trade(
                    trade["id"],
                    tp,
                    "TP"
                )

        else:

            hit_tp = low <= tp
            hit_sl = high >= sl

            if hit_sl:
                close_trade(
                    trade["id"],
                    sl,
                    "SL"
                )

            elif hit_tp:
                close_trade(
                    trade["id"],
                    tp,
                    "TP"
                )


# ============================================================
# CURRENT OPEN TRADE REPORT
# ============================================================

def get_open_trades():

    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM paper_trades
        WHERE exit_time IS NULL
        ORDER BY id DESC
        """
    ).fetchall()

    conn.close()

    return rows


# ============================================================
# PERFORMANCE
# ============================================================

def performance():

    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM paper_trades
        WHERE exit_time IS NOT NULL
        """
    ).fetchall()

    open_rows = conn.execute(
        """
        SELECT *
        FROM paper_trades
        WHERE exit_time IS NULL
        """
    ).fetchall()

    conn.close()

    closed_count = len(rows)

    wins = [
        r for r in rows
        if r["result"] == "TP"
    ]

    losses = [
        r for r in rows
        if r["result"] == "SL"
    ]

    realized = sum(
        float(r["pnl_pct"] or 0)
        for r in rows
    )

    gross_profit = sum(
        float(r["pnl_pct"] or 0)
        for r in wins
    )

    gross_loss = sum(
        float(r["pnl_pct"] or 0)
        for r in losses
    )

    win_rate = (
        len(wins)
        / closed_count
        * 100
        if closed_count
        else 0
    )

    return {
        "closed": closed_count,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "realized": realized,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "open": len(open_rows),
    }


# ============================================================
# CHART
# ============================================================

def make_chart(
    df,
    hook,
    timeframe
):

    symbol = hook["symbol"]

    entry_time = pd.Timestamp(
        hook["entry_time"],
        tz="UTC"
    )

    start_time = pd.Timestamp(
        hook["start_time"],
        tz="UTC"
    )

    points = []

    if hook["direction"] == "SHORT":

        points = [
            ("START", start_time, hook["start_price"]),
            ("H1", hook["h1_time"], hook["h1_price"]),
            ("L1", hook["l1_time"], hook["l1_price"]),
            ("H2", hook["h2_time"], hook["h2_price"]),
            ("L2", hook["l2_time"], hook["l2_price"]),
            ("H3", hook["entry_time"], hook["entry_price"]),
        ]

    else:

        points = [
            ("START", start_time, hook["start_price"]),
            ("L1", hook["l1_time"], hook["l1_price"]),
            ("H1", hook["h1_time"], hook["h1_price"]),
            ("L2", hook["l2_time"], hook["l2_price"]),
            ("H2", hook["h2_time"], hook["h2_price"]),
            ("L3", hook["entry_time"], hook["entry_price"]),
        ]

    times = [
        pd.Timestamp(x[1], tz="UTC")
        for x in points
    ]

    prices = [
        x[2]
        for x in points
    ]

    min_time = min(times)

    max_time = max(
        times[-1],
        pd.Timestamp(
            df.iloc[-1]["time"],
            tz="UTC"
        )
    )

    visible = df[
        (df["time"] >= min_time)
        & (df["time"] <= max_time)
    ].copy()

    if len(visible) < 20:
        visible = df.tail(
            CHART_CANDLES
        ).copy()

    fig, ax = plt.subplots(
        figsize=(14, 7)
    )

    x = mdates.date2num(
        visible["time"].dt.to_pydatetime()
    )

    width = (
        5 / 1440
        if timeframe == "M5"
        else 4 / 24
    )

    for xi, row in zip(
        x,
        visible.itertuples()
    ):

        ax.plot(
            [xi, xi],
            [row.low, row.high],
            linewidth=1
        )

        if row.close >= row.open:

            bottom = row.open
            height = row.close - row.open

        else:

            bottom = row.close
            height = row.open - row.close

        ax.add_patch(
            plt.Rectangle(
                (
                    xi - width / 2,
                    bottom
                ),
                width,
                max(
                    height,
                    abs(row.close)
                    * 0.000001
                ),
                fill=False,
                linewidth=1
            )
        )

    # Hook path.
    point_x = [
        mdates.date2num(
            pd.Timestamp(t, tz="UTC")
        )
        for _, t, _ in points
    ]

    ax.plot(
        point_x,
        prices,
        linewidth=2
    )

    # Pivot labels.
    offsets = [
        (0, 18),
        (0, -24),
        (0, 18),
        (0, -24),
        (0, 18),
        (0, -28),
    ]

    for (label, t, price), (ox, oy) in zip(
        points,
        offsets
    ):

        tx = mdates.date2num(
            pd.Timestamp(t, tz="UTC")
        )

        ax.annotate(
            label,
            (
                tx,
                price
            ),
            xytext=(
                ox,
                oy
            ),
            textcoords="offset points",
            ha="center",
            fontsize=10,
            fontweight="bold"
        )

    # Entry.
    ax.axhline(
        hook["entry_price"],
        linestyle="--",
        linewidth=1
    )

    ax.annotate(
        f"ENTRY {hook['entry_price']:.8g}",
        xy=(
            mdates.date2num(entry_time),
            hook["entry_price"]
        ),
        xytext=(
            55,
            0
        ),
        textcoords="offset points",
        va="center",
        fontweight="bold"
    )

    # TP.
    ax.axhline(
        hook["tp"],
        linestyle="--",
        linewidth=1
    )

    ax.annotate(
        f"TP 86.4% {hook['tp']:.8g}",
        xy=(
            mdates.date2num(entry_time),
            hook["tp"]
        ),
        xytext=(
            55,
            18
        ),
        textcoords="offset points",
        va="center",
        fontweight="bold"
    )

    # SL.
    ax.axhline(
        hook["sl"],
        linestyle=":",
        linewidth=1
    )

    ax.annotate(
        f"SL {hook['sl']:.8g}",
        xy=(
            mdates.date2num(entry_time),
            hook["sl"]
        ),
        xytext=(
            55,
            -18
        ),
        textcoords="offset points",
        va="center",
        fontweight="bold"
    )

    ax.set_title(
        f"NDS {timeframe} | "
        f"{symbol} | "
        f"{hook['direction']}"
    )

    ax.xaxis.set_major_formatter(
        mdates.DateFormatter(
            "%m-%d %H:%M",
            tz=timezone.utc
        )
    )

    ax.grid(
        alpha=0.2
    )

    fig.autofmt_xdate()

    filename = (
        f"{symbol}_"
        f"{hook['direction']}_"
        f"{int(time.time())}.png"
    )

    path = os.path.join(
        CHART_DIR,
        filename
    )

    plt.tight_layout()
    plt.savefig(
        path,
        dpi=140
    )
    plt.close(fig)

    return path


# ============================================================
# H4 FILTER MESSAGE
# ============================================================

def h4_direction_text(hook):

    if hook is None:
        return "NONE"

    return hook["direction"]


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def build_signal_message(
    hook,
    h4_hook
):

    direction = hook["direction"]

    if direction == "SHORT":
        emoji = "🔴"
    else:
        emoji = "🟢"

    return (
        f"{emoji} <b>NDS {direction}</b>\n"
        f"Symbol: <b>{hook['symbol']}</b>\n"
        f"Filter: <b>H4 {h4_direction_text(h4_hook)}</b>\n"
        f"Trigger: <b>M5 {direction} Hook</b>\n"
        f"Entry: <b>{hook['entry_price']:.8g}</b>\n"
        f"TP 86.4%: <b>{hook['tp']:.8g}</b>\n"
        f"SL: <b>{hook['sl']:.8g}</b>\n"
        f"TP Distance: "
        f"<b>{hook['tp_distance_pct']:.2f}%</b>\n"
        f"Hook: "
        f"<b>{ts_to_str(hook['entry_time'])}</b>\n"
        f"Status: <b>PAPER</b>"
    )


# ============================================================
# EXIT REPORT
# ============================================================

def build_report():

    p = performance()

    lines = [
        "📊 <b>NDS H4 → M5 REPORT</b>",
        "",
        f"Closed: <b>{p['closed']}</b>",
        f"Wins: <b>{p['wins']}</b>",
        f"Losses: <b>{p['losses']}</b>",
        f"Win Rate: <b>{p['win_rate']:.2f}%</b>",
        f"Realized PnL: <b>{p['realized']:+.2f}%</b>",
        f"Open Trades: <b>{p['open']}</b>",
    ]

    rows = get_open_trades()

    if rows:

        lines.append("")
        lines.append(
            "<b>OPEN TRADES</b>"
        )

        for r in rows:

            lines.append(
                f"{'🟢' if r['direction']=='LONG' else '🔴'} "
                f"{r['symbol']} "
                f"{r['direction']} "
                f"Entry {float(r['entry_price']):.8g} "
                f"TP {float(r['tp']):.8g} "
                f"SL {float(r['sl']):.8g}"
            )

    return "\n".join(lines)


# ============================================================
# FRESH SIGNAL CHECK
# ============================================================

def is_fresh_hook(hook):

    now = utc_now()

    try:
        entry_time = pd.Timestamp(
            hook["entry_time"],
            tz="UTC"
        )

        age = (
            now
            - entry_time.to_pydatetime()
        ).total_seconds()

        return (
            age >= 0
            and age <=
            MAX_NEW_SIGNAL_AGE_SECONDS
        )

    except Exception:
        return False


# ============================================================
# PROCESS ONE SYMBOL
# ============================================================

def process_symbol(symbol):

    # --------------------------------------------------------
    # STEP 1: H4 FILTER
    # --------------------------------------------------------

    h4_df = fetch_candles(
        symbol,
        INTERVAL_H4,
        H4_COUNT
    )

    if h4_df is None:
        print(
            f"{symbol}: H4 data unavailable"
        )
        return None

    h4_hook, h4_stats = detect_hooks(
        symbol,
        "H4",
        h4_df,
        allowed_direction=None
    )

    # No valid H4 hook = no new M5 signal.
    if h4_hook is None:

        print(
            f"{symbol}: "
            f"H4 NO VALID HOOK | "
            f"PH={h4_stats['pivots_high']} "
            f"PL={h4_stats['pivots_low']}"
        )

        return None

    allowed_direction = (
        h4_hook["direction"]
    )

    print(
        f"{symbol}: "
        f"H4 FILTER = {allowed_direction}"
    )

    # --------------------------------------------------------
    # STEP 2: M5 ONLY IN H4 DIRECTION
    # --------------------------------------------------------

    m5_df = fetch_candles(
        symbol,
        INTERVAL_M5,
        M5_COUNT
    )

    if m5_df is None:
        print(
            f"{symbol}: M5 data unavailable"
        )
        return None

    m5_hook, m5_stats = detect_hooks(
        symbol,
        "M5",
        m5_df,
        allowed_direction=allowed_direction
    )

    if m5_hook is None:

        print(
            f"{symbol}: "
            f"H4={allowed_direction} | "
            f"M5 no matching hook"
        )

        return None

    # --------------------------------------------------------
    # STALE HISTORICAL HOOK
    # --------------------------------------------------------

    if not is_fresh_hook(m5_hook):

        # Save as seen so it cannot replay forever.
        save_hook(m5_hook)

        print(
            f"{symbol}: "
            f"M5 matching hook is stale, "
            f"no new signal"
        )

        return None

    # --------------------------------------------------------
    # DUPLICATE PREVENTION
    # --------------------------------------------------------

    inserted = save_hook(
        m5_hook
    )

    if not inserted:

        print(
            f"{symbol}: "
            f"M5 hook already processed"
        )

        return None

    # --------------------------------------------------------
    # OPEN TRADE LIMIT
    # --------------------------------------------------------

    if count_open_trades() >= MAX_OPEN_TRADES:

        print(
            f"{symbol}: "
            f"MAX OPEN TRADES reached"
        )

        return None

    if symbol_has_open_trade(symbol):

        print(
            f"{symbol}: "
            f"already has open trade"
        )

        return None

    # --------------------------------------------------------
    # CREATE PAPER TRADE
    # --------------------------------------------------------

    created = save_paper_trade(
        m5_hook
    )

    if not created:

        return None

    # --------------------------------------------------------
    # CHART
    # --------------------------------------------------------

    chart = make_chart(
        m5_df,
        m5_hook,
        "M5"
    )

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    message = build_signal_message(
        m5_hook,
        h4_hook
    )

    telegram_photo(
        chart,
        message
    )

    print(
        f"{symbol}: "
        f"NEW {allowed_direction} SIGNAL"
    )

    return m5_hook


# ============================================================
# MONITOR ALL OPEN TRADES
# ============================================================

def monitor_all_open_trades():

    rows = get_open_trades()

    symbols = sorted(
        set(
            r["symbol"]
            for r in rows
        )
    )

    for symbol in symbols:

        df = fetch_candles(
            symbol,
            INTERVAL_M5,
            20
        )

        if df is None:
            continue

        monitor_open_trades(
            symbol,
            df
        )


# ============================================================
# MAIN SCAN
# ============================================================

def run_scan():

    print("")
    print("=" * 70)
    print(
        f"NDS H4 -> M5 SCAN | "
        f"{utc_now_str()}"
    )
    print("=" * 70)

    signals = 0
    h4_valid = 0
    m5_checked = 0

    for symbol in ASSETS:

        try:

            result = process_symbol(
                symbol
            )

            if result is not None:
                signals += 1

        except Exception as e:

            print(
                f"{symbol}: ERROR {e}"
            )

    monitor_all_open_trades()

    print("")
    print(
        f"New signals: {signals}"
    )

    print(
        build_report()
    )

    return signals


# ============================================================
# REPORT
# ============================================================

def send_report():

    text = build_report()

    telegram_send(text)


# ============================================================
# STARTUP
# ============================================================

def main():

    init_db()

    print(
        "============================================================"
    )
    print(
        "NDS H4 -> M5 LIVE SCANNER"
    )
    print(
        "VERSION 5.1.0"
    )
    print(
        "PAPER ONLY"
    )
    print(
        "============================================================"
    )

    print(
        f"H4 interval: {INTERVAL_H4}"
    )

    print(
        f"M5 interval: {INTERVAL_M5}"
    )

    print(
        f"TP retracement: "
        f"{TP_RETRACE * 100:.1f}%"
    )

    print(
        f"SL multiplier: "
        f"{SL_TP_MULTIPLIER:.2f}"
    )

    print(
        "H4 -> M5 direction filter: ENABLED"
    )

    last_report = 0

    while True:

        cycle_start = time.time()

        try:

            run_scan()

        except Exception as e:

            print(
                "SCAN ERROR:",
                e
            )

        now = time.time()

        if (
            now - last_report
            >= REPORT_SECONDS
        ):

            try:
                send_report()
            except Exception as e:
                print(
                    "REPORT ERROR:",
                    e
                )

            last_report = now

        elapsed = (
            time.time()
            - cycle_start
        )

        sleep_for = max(
            1,
            SCAN_SECONDS - elapsed
        )

        print(
            f"Next scan in "
            f"{sleep_for:.0f} seconds"
        )

        time.sleep(
            sleep_for
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
