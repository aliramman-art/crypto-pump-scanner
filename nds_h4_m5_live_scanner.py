# ============================================================
# NDS H4 -> M5 LIVE SCANNER
# VERSION 5.4.0
# ============================================================
#
# PAPER TRADING ONLY
#
# NO 123F
# NO M1
#
# H4:
#   Determines allowed direction.
#
# M5:
#   Finds the six-point NDS Hook.
#
# SHORT:
#   START -> H1 -> L1 -> H2 -> L2 -> H3
#
#   H2 > H1
#   L2 < L1
#   H3 > H2
#
#   ENTRY = H3
#   TP    = 86.4% retracement from START -> H3
#
# LONG:
#   START -> L1 -> H1 -> L2 -> H2 -> L3
#
#   L2 < L1
#   H2 > H1
#   L3 < L2
#
#   ENTRY = L3
#   TP    = 86.4% retracement from START -> L3
#
# Charts:
#   Hook points
#   Entry
#   SL
#   TP 86.4%
#   Confirmation line
#
# REAL ORDERS:
#   NEVER SENT
# ============================================================

import os
import time
import sqlite3
import traceback
from datetime import datetime, timezone

import requests
import pandas as pd
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "5.4.0"

REAL_TRADING = False

KRAKEN_FUTURES_URL = "https://futures.kraken.com/derivatives/api/v3"
KRAKEN_CHART_URL = "https://futures.kraken.com/api/charts/v1"

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

DB_FILE = "nds_h4_m5_v54.db"
CHART_DIR = "nds_h4_m5_charts"

TARGET_ASSETS = 100

H4_INTERVAL = "4h"
M5_INTERVAL = "5m"

H4_CANDLES = 320
M5_CANDLES = 1500

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

NDS_RETRACE = 0.864

M5_MIN_HOOK_RANGE_PCT = 0.20

MAX_HOOK_AGE_SECONDS = 6 * 60 * 60

MAX_OPEN_TRADES = 3

COOLDOWN_SECONDS = 60 * 60

CHART_CANDLES = 240

REQUEST_TIMEOUT = 20

SCAN_SLEEP_SECONDS = 1

REPORT_EVERY_MINUTES = 15


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "NDS-H4-M5-Scanner/5.4.0",
    "Accept": "application/json",
})


# ============================================================
# DIAGNOSTICS
# ============================================================

DIAG = {
    "assets_scanned": 0,

    "h4_requests": 0,
    "h4_ok": 0,
    "h4_error": 0,
    "h4_empty": 0,
    "h4_short": 0,
    "h4_hooks": 0,
    "h4_filtered": 0,

    "m5_requests": 0,
    "m5_ok": 0,
    "m5_error": 0,
    "m5_empty": 0,
    "m5_short": 0,

    "matching_short": 0,
    "matching_long": 0,
    "no_matching_hook": 0,

    "tp_already_touched": 0,
    "old_hooks_removed": 0,
    "duplicate": 0,
    "open_trade_blocked": 0,
    "max_open_blocked": 0,

    "new_signals": 0,
    "charts": 0,

    "closed": 0,
    "wins": 0,
    "losses": 0,
    "realized_pnl": 0.0,
}

ERROR_SAMPLES = []


def add_error_sample(text):
    text = str(text).replace("\n", " ")
    if len(text) > 300:
        text = text[:300] + "..."
    if text not in ERROR_SAMPLES and len(ERROR_SAMPLES) < 12:
        ERROR_SAMPLES.append(text)


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_ts():
    return int(time.time())


def fmt_time(ts):
    try:
        return datetime.fromtimestamp(
            float(ts),
            tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ts)


# ============================================================
# HTTP
# ============================================================

def http_get(url, params=None):
    response = SESSION.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    if response.status_code != 200:
        body = response.text[:500]
        raise RuntimeError(
            f"HTTP {response.status_code} | "
            f"{url} | {body}"
        )

    try:
        return response.json()
    except Exception as exc:
        raise RuntimeError(
            f"JSON parse error | {url} | {exc}"
        )


# ============================================================
# KRAKEN CANDLES
# ============================================================

def normalize_resolution(interval):
    mapping = {
        "1": "1m",
        "1m": "1m",
        "5": "5m",
        "5m": "5m",
        "15": "15m",
        "15m": "15m",
        "30": "30m",
        "30m": "30m",
        "60": "1h",
        "1h": "1h",
        "240": "4h",
        "4h": "4h",
        "12h": "12h",
        "1d": "1d",
        "1w": "1w",
    }

    return mapping.get(str(interval), str(interval))


def parse_candles(raw):
    if not isinstance(raw, dict):
        raise RuntimeError(
            f"Unexpected candle response type: {type(raw).__name__}"
        )

    candles = raw.get("candles")

    if candles is None:
        raise RuntimeError(
            "Candle response has no 'candles' field: "
            + str(raw)[:500]
        )

    if not isinstance(candles, list):
        raise RuntimeError(
            f"'candles' is not list: {type(candles).__name__}"
        )

    if not candles:
        return pd.DataFrame()

    rows = []

    for item in candles:

        if isinstance(item, dict):

            t = item.get("time")

            if t is None:
                t = item.get("timestamp")

            o = item.get("open")
            h = item.get("high")
            l = item.get("low")
            c = item.get("close")
            v = item.get("volume", 0)

        elif isinstance(item, (list, tuple)):

            if len(item) < 5:
                continue

            t = item[0]
            o = item[1]
            h = item[2]
            l = item[3]
            c = item[4]
            v = item[5] if len(item) > 5 else 0

        else:
            continue

        try:
            t = float(t)

            # Kraken chart API documents candle time in ms.
            if t > 10_000_000_000:
                t = t / 1000.0

            rows.append({
                "time": t,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v or 0),
            })

        except Exception:
            continue

    if not rows:
        raise RuntimeError(
            "Candle list exists but no valid OHLC rows could be parsed"
        )

    df = pd.DataFrame(rows)

    df = df.drop_duplicates(
        subset=["time"]
    )

    df = df.sort_values(
        "time"
    ).reset_index(drop=True)

    return df


def get_candles(symbol, interval, count):
    resolution = normalize_resolution(interval)

    # Official current Kraken Futures chart endpoint:
    # /api/charts/v1/{tick_type}/{symbol}/{resolution}
    url = (
        f"{KRAKEN_CHART_URL}/trade/"
        f"{symbol}/"
        f"{resolution}"
    )

    params = {
        "count": int(count)
    }

    try:

        raw = http_get(
            url,
            params=params
        )

        df = parse_candles(raw)

        return df

    except Exception as exc:

        add_error_sample(
            f"{symbol} {resolution}: {exc}"
        )

        return pd.DataFrame()


# ============================================================
# FUTURES INSTRUMENTS
# ============================================================

def get_futures_instruments():

    try:

        data = http_get(
            f"{KRAKEN_FUTURES_URL}/instruments"
        )

        instruments = data.get(
            "instruments",
            []
        )

        symbols = []

        for item in instruments:

            if not isinstance(item, dict):
                continue

            symbol = (
                item.get("symbol")
                or item.get("tradeable")
                or item.get("instrument")
            )

            if not symbol:
                continue

            symbol = str(symbol)

            # Per existing scanner convention.
            if not symbol.startswith("PF_"):
                continue

            if "USD" not in symbol:
                continue

            symbols.append(symbol)

        symbols = sorted(
            list(dict.fromkeys(symbols))
        )

        return symbols[:TARGET_ASSETS]

    except Exception as exc:

        add_error_sample(
            f"instruments: {exc}"
        )

        return []


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(df):

    if df.empty:
        return [], []

    highs = []
    lows = []

    high_values = df["high"].values
    low_values = df["low"].values
    times = df["time"].values

    n = len(df)

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT
    ):

        current_high = high_values[i]
        current_low = low_values[i]

        left_highs = high_values[
            i - PIVOT_LEFT:i
        ]

        right_highs = high_values[
            i + 1:i + 1 + PIVOT_RIGHT
        ]

        left_lows = low_values[
            i - PIVOT_LEFT:i
        ]

        right_lows = low_values[
            i + 1:i + 1 + PIVOT_RIGHT
        ]

        is_high = (
            current_high > left_highs.max()
            and current_high >= right_highs.max()
        )

        is_low = (
            current_low < left_lows.min()
            and current_low <= right_lows.min()
        )

        if is_high:
            highs.append({
                "type": "H",
                "time": float(times[i]),
                "price": float(current_high),
                "index": i,
            })

        if is_low:
            lows.append({
                "type": "L",
                "time": float(times[i]),
                "price": float(current_low),
                "index": i,
            })

    return highs, lows


# ============================================================
# ORDERED PIVOTS
# ============================================================

def build_ordered_pivots(highs, lows):

    pivots = sorted(
        highs + lows,
        key=lambda x: x["time"]
    )

    if not pivots:
        return []

    result = []

    for p in pivots:

        if not result:
            result.append(p)
            continue

        last = result[-1]

        if p["type"] == last["type"]:

            if p["type"] == "H":

                if p["price"] >= last["price"]:
                    result[-1] = p

            else:

                if p["price"] <= last["price"]:
                    result[-1] = p

        else:

            result.append(p)

    return result


# ============================================================
# HOOK DETECTION
# ============================================================

def detect_hooks(df):

    highs, lows = find_pivots(df)

    pivots = build_ordered_pivots(
        highs,
        lows
    )

    hooks = []

    if len(pivots) < 6:
        return hooks

    for i in range(
        0,
        len(pivots) - 5
    ):

        p = pivots[i:i + 6]

        # ----------------------------------------------------
        # SHORT
        #
        # START -> H1 -> L1 -> H2 -> L2 -> H3
        #
        # H2 > H1
        # L2 < L1
        # H3 > H2
        # ----------------------------------------------------

        if [x["type"] for x in p] == [
            "L", "H", "L", "H", "L", "H"
        ]:

            start, h1, l1, h2, l2, h3 = p

            valid = (
                h2["price"] > h1["price"]
                and
                l2["price"] < l1["price"]
                and
                h3["price"] > h2["price"]
            )

            if valid:

                start_price = start["price"]
                entry = h3["price"]

                move = entry - start_price

                if move <= 0:
                    continue

                range_pct = (
                    abs(move)
                    / start_price
                    * 100
                )

                if range_pct < M5_MIN_HOOK_RANGE_PCT:
                    continue

                tp = (
                    entry
                    - NDS_RETRACE * move
                )

                hooks.append({
                    "direction": "SHORT",
                    "start": start,
                    "h1": h1,
                    "l1": l1,
                    "h2": h2,
                    "l2": l2,
                    "h3": h3,
                    "entry": entry,
                    "tp": tp,
                    "range_pct": range_pct,
                    "confirmation_time": (
                        h3["time"]
                        + 5 * 60 * PIVOT_RIGHT
                    ),
                })

        # ----------------------------------------------------
        # LONG
        #
        # START -> L1 -> H1 -> L2 -> H2 -> L3
        #
        # L2 < L1
        # H2 > H1
        # L3 < L2
        # ----------------------------------------------------

        if [x["type"] for x in p] == [
            "H", "L", "H", "L", "H", "L"
        ]:

            start, l1, h1, l2, h2, l3 = p

            valid = (
                l2["price"] < l1["price"]
                and
                h2["price"] > h1["price"]
                and
                l3["price"] < l2["price"]
            )

            if valid:

                start_price = start["price"]
                entry = l3["price"]

                move = start_price - entry

                if move <= 0:
                    continue

                range_pct = (
                    abs(move)
                    / start_price
                    * 100
                )

                if range_pct < M5_MIN_HOOK_RANGE_PCT:
                    continue

                tp = (
                    entry
                    + NDS_RETRACE * move
                )

                hooks.append({
                    "direction": "LONG",
                    "start": start,
                    "l1": l1,
                    "h1": h1,
                    "l2": l2,
                    "h2": h2,
                    "l3": l3,
                    "entry": entry,
                    "tp": tp,
                    "range_pct": range_pct,
                    "confirmation_time": (
                        l3["time"]
                        + 5 * 60 * PIVOT_RIGHT
                    ),
                })

    return hooks


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

    if df.empty:

        DIAG["h4_error"] += 1
        DIAG["h4_empty"] += 1

        return None

    if len(df) < 30:

        DIAG["h4_error"] += 1
        DIAG["h4_short"] += 1

        add_error_sample(
            f"{symbol} H4 short data: {len(df)} candles"
        )

        return None

    DIAG["h4_ok"] += 1

    hooks = detect_hooks(df)

    DIAG["h4_hooks"] += len(hooks)

    if not hooks:
        return None

    now = utc_ts()

    valid = []

    for hook in hooks:

        final_time = (
            hook["h3"]["time"]
            if hook["direction"] == "SHORT"
            else hook["l3"]["time"]
        )

        # Latest pivot must be the final hook point.
        latest_pivot_time = df["time"].iloc[
            -PIVOT_RIGHT - 1
        ]

        if abs(
            final_time - float(latest_pivot_time)
        ) > 1:

            continue

        confirmation = hook[
            "confirmation_time"
        ]

        if confirmation > now:
            continue

        if (
            now - confirmation
            > MAX_HOOK_AGE_SECONDS
        ):
            continue

        valid.append(hook)

    if not valid:
        return None

    valid.sort(
        key=lambda x: x["confirmation_time"],
        reverse=True
    )

    return valid[0]["direction"]


# ============================================================
# M5 HOOK MATCHING
# ============================================================

def select_m5_hook(
    symbol,
    allowed_direction,
    df
):

    hooks = detect_hooks(df)

    if not hooks:

        DIAG["no_matching_hook"] += 1
        return None

    now = utc_ts()

    candidates = []

    for hook in hooks:

        if hook["direction"] != allowed_direction:
            continue

        final_point = (
            hook["h3"]
            if allowed_direction == "SHORT"
            else hook["l3"]
        )

        # Final hook point must be latest confirmed pivot.
        latest_pivot_time = df["time"].iloc[
            -PIVOT_RIGHT - 1
        ]

        if abs(
            final_point["time"]
            - float(latest_pivot_time)
        ) > 1:
            continue

        confirmation = hook[
            "confirmation_time"
        ]

        if confirmation > now:
            continue

        if (
            now - confirmation
            > MAX_HOOK_AGE_SECONDS
        ):
            DIAG["old_hooks_removed"] += 1
            continue

        # ----------------------------------------------------
        # Do not accept a trade where TP was already reached
        # after the final Hook point.
        # ----------------------------------------------------

        future = df[
            df["time"] >= final_point["time"]
        ]

        tp = hook["tp"]

        if allowed_direction == "SHORT":

            if (
                not future.empty
                and future["low"].min() <= tp
            ):
                DIAG["tp_already_touched"] += 1
                continue

            DIAG["matching_short"] += 1

        else:

            if (
                not future.empty
                and future["high"].max() >= tp
            ):
                DIAG["tp_already_touched"] += 1
                continue

            DIAG["matching_long"] += 1

        candidates.append(hook)

    if not candidates:

        return None

    # Prefer the most recently confirmed valid Hook.
    candidates.sort(
        key=lambda x: x["confirmation_time"],
        reverse=True
    )

    return candidates[0]


# ============================================================
# STOP LOSS
# ============================================================

def calculate_sl(hook, df):

    direction = hook["direction"]

    final_point = (
        hook["h3"]
        if direction == "SHORT"
        else hook["l3"]
    )

    before = df[
        df["time"] < final_point["time"]
    ]

    if before.empty:
        return None

    highs, lows = find_pivots(before)

    if direction == "SHORT":

        valid_highs = [
            p["price"]
            for p in highs
            if p["time"] < final_point["time"]
        ]

        if not valid_highs:
            return None

        # Previous valid M5 high.
        return float(valid_highs[-1])

    else:

        valid_lows = [
            p["price"]
            for p in lows
            if p["time"] < final_point["time"]
        ]

        if not valid_lows:
            return None

        # Previous valid M5 low.
        return float(valid_lows[-1])


# ============================================================
# DATABASE
# ============================================================

def db_connect():

    conn = sqlite3.connect(
        DB_FILE
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
            start_time REAL NOT NULL,
            entry_time REAL NOT NULL,
            start_price REAL NOT NULL,
            entry_price REAL NOT NULL,
            tp REAL NOT NULL,
            sl REAL,
            confirmation_time REAL NOT NULL,
            range_pct REAL,
            created_at REAL NOT NULL,
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
            entry_time REAL NOT NULL,
            entry_price REAL NOT NULL,
            tp REAL NOT NULL,
            sl REAL,
            exit_time REAL,
            exit_price REAL,
            pnl_pct REAL,
            status TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def trade_exists(symbol, direction, entry_time):

    conn = db_connect()

    row = conn.execute("""
        SELECT id
        FROM trades
        WHERE symbol = ?
          AND direction = ?
          AND entry_time = ?
        LIMIT 1
    """, (
        symbol,
        direction,
        entry_time
    )).fetchone()

    conn.close()

    return row is not None


def open_trade_count():

    conn = db_connect()

    row = conn.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'OPEN'
    """).fetchone()

    conn.close()

    return int(row[0])


def has_open_symbol(symbol):

    conn = db_connect()

    row = conn.execute("""
        SELECT id
        FROM trades
        WHERE symbol = ?
          AND status = 'OPEN'
        LIMIT 1
    """, (
        symbol,
    )).fetchone()

    conn.close()

    return row is not None


def save_hook(symbol, hook, sl):

    conn = db_connect()

    try:

        conn.execute("""
            INSERT OR IGNORE INTO hooks (
                symbol,
                direction,
                start_time,
                entry_time,
                start_price,
                entry_price,
                tp,
                sl,
                confirmation_time,
                range_pct,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol,
            hook["direction"],
            hook["start"]["time"],
            (
                hook["h3"]["time"]
                if hook["direction"] == "SHORT"
                else hook["l3"]["time"]
            ),
            hook["start"]["price"],
            hook["entry"],
            hook["tp"],
            sl,
            hook["confirmation_time"],
            hook["range_pct"],
            utc_ts(),
        ))

        conn.commit()

    finally:
        conn.close()


def save_trade(symbol, hook, sl):

    entry_time = (
        hook["h3"]["time"]
        if hook["direction"] == "SHORT"
        else hook["l3"]["time"]
    )

    conn = db_connect()

    try:

        conn.execute("""
            INSERT INTO trades (
                symbol,
                direction,
                entry_time,
                entry_price,
                tp,
                sl,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?)
        """, (
            symbol,
            hook["direction"],
            entry_time,
            hook["entry"],
            hook["tp"],
            sl,
            utc_ts(),
        ))

        conn.commit()

    finally:
        conn.close()


# ============================================================
# PAPER TRADE MONITOR
# ============================================================

def calculate_pnl(direction, entry, exit_price):

    if direction == "LONG":

        if entry == 0:
            return 0.0

        return (
            (exit_price - entry)
            / entry
            * 100
        )

    else:

        if entry == 0:
            return 0.0

        return (
            (entry - exit_price)
            / entry
            * 100
        )


def monitor_open_trades():

    conn = db_connect()

    rows = conn.execute("""
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
    """).fetchall()

    for trade in rows:

        symbol = trade["symbol"]

        df = get_candles(
            symbol,
            M5_INTERVAL,
            20
        )

        if df.empty:
            continue

        direction = trade["direction"]

        tp = float(trade["tp"])
        sl = (
            float(trade["sl"])
            if trade["sl"] is not None
            else None
        )

        entry = float(
            trade["entry_price"]
        )

        for _, candle in df.iterrows():

            candle_time = float(
                candle["time"]
            )

            if candle_time <= float(
                trade["entry_time"]
            ):
                continue

            high = float(candle["high"])
            low = float(candle["low"])

            exit_price = None
            reason = None

            # Conservative rule:
            # SL is checked before TP when both
            # are touched in the same candle.
            if direction == "LONG":

                if (
                    sl is not None
                    and low <= sl
                ):
                    exit_price = sl
                    reason = "SL"

                elif high >= tp:
                    exit_price = tp
                    reason = "TP"

            else:

                if (
                    sl is not None
                    and high >= sl
                ):
                    exit_price = sl
                    reason = "SL"

                elif low <= tp:
                    exit_price = tp
                    reason = "TP"

            if exit_price is None:
                continue

            pnl = calculate_pnl(
                direction,
                entry,
                exit_price
            )

            conn.execute("""
                UPDATE trades
                SET
                    exit_time = ?,
                    exit_price = ?,
                    pnl_pct = ?,
                    status = 'CLOSED'
                WHERE id = ?
            """, (
                candle_time,
                exit_price,
                pnl,
                trade["id"],
            ))

            DIAG["closed"] += 1

            if pnl >= 0:
                DIAG["wins"] += 1
            else:
                DIAG["losses"] += 1

            DIAG["realized_pnl"] += pnl

            break

    conn.commit()
    conn.close()


# ============================================================
# CHART
# ============================================================

def make_chart(
    symbol,
    df,
    hook,
    sl
):

    os.makedirs(
        CHART_DIR,
        exist_ok=True
    )

    direction = hook["direction"]

    if direction == "SHORT":

        points = [
            ("START", hook["start"]),
            ("H1", hook["h1"]),
            ("L1", hook["l1"]),
            ("H2", hook["h2"]),
            ("L2", hook["l2"]),
            ("H3", hook["h3"]),
        ]

        entry_point = hook["h3"]

    else:

        points = [
            ("START", hook["start"]),
            ("L1", hook["l1"]),
            ("H1", hook["h1"]),
            ("L2", hook["l2"]),
            ("H2", hook["h2"]),
            ("L3", hook["l3"]),
        ]

        entry_point = hook["l3"]

    chart_df = df.tail(
        CHART_CANDLES
    ).copy()

    if chart_df.empty:
        return None

    x = pd.to_datetime(
        chart_df["time"],
        unit="s",
        utc=True
    )

    fig, ax = plt.subplots(
        figsize=(16, 8)
    )

    ax.plot(
        x,
        chart_df["close"],
        linewidth=1.1,
        label="M5 Close"
    )

    # Hook line
    hook_x = [
        pd.to_datetime(
            p["time"],
            unit="s",
            utc=True
        )
        for _, p in points
    ]

    hook_y = [
        p["price"]
        for _, p in points
    ]

    ax.plot(
        hook_x,
        hook_y,
        linewidth=2.0,
        marker="o",
        label=f"{direction} Hook"
    )

    # Point labels
    for i, (name, p) in enumerate(points):

        offset = 10 if i % 2 == 0 else -15

        ax.annotate(
            name,
            (
                pd.to_datetime(
                    p["time"],
                    unit="s",
                    utc=True
                ),
                p["price"]
            ),
            xytext=(0, offset),
            textcoords="offset points",
            ha="center",
            fontsize=9,
            fontweight="bold"
        )

    # Entry
    ax.axhline(
        hook["entry"],
        linestyle="--",
        linewidth=1.2,
        label=f"ENTRY {hook['entry']:.8g}"
    )

    # TP 86.4
    ax.axhline(
        hook["tp"],
        linestyle="-.",
        linewidth=1.5,
        label=f"TP 86.4% {hook['tp']:.8g}"
    )

    # SL
    if sl is not None:

        ax.axhline(
            sl,
            linestyle=":",
            linewidth=1.5,
            label=f"SL {sl:.8g}"
        )

    # Confirmation
    confirmation_dt = pd.to_datetime(
        hook["confirmation_time"],
        unit="s",
        utc=True
    )

    ax.axvline(
        confirmation_dt,
        linestyle=":",
        linewidth=1.0,
        alpha=0.8
    )

    ax.annotate(
        "CONFIRMED",
        (
            confirmation_dt,
            chart_df["close"].max()
        ),
        xytext=(5, -20),
        textcoords="offset points",
        fontsize=8
    )

    ax.set_title(
        f"NDS H4 → M5 | {symbol} | {direction} | "
        f"TP = 86.4%"
    )

    ax.set_xlabel(
        "UTC"
    )

    ax.set_ylabel(
        "Price"
    )

    ax.grid(
        alpha=0.25
    )

    ax.legend(
        loc="best",
        fontsize=8
    )

    fig.autofmt_xdate()

    safe_symbol = symbol.replace(
        "/",
        "_"
    )

    filename = (
        f"{safe_symbol}_"
        f"{direction}_"
        f"{int(entry_point['time'])}.png"
    )

    path = os.path.join(
        CHART_DIR,
        filename
    )

    plt.tight_layout()

    fig.savefig(
        path,
        dpi=140,
        bbox_inches="tight"
    )

    plt.close(fig)

    return path


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):

    if not TELEGRAM_BOT_TOKEN:
        return False

    if not TELEGRAM_CHAT_ID:
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:

        r = SESSION.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=20
        )

        return r.status_code == 200

    except Exception:
        return False


def telegram_photo(path, caption):

    if not TELEGRAM_BOT_TOKEN:
        return False

    if not TELEGRAM_CHAT_ID:
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    try:

        with open(
            path,
            "rb"
        ) as photo:

            r = SESSION.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption,
                    "parse_mode": "HTML",
                },
                files={
                    "photo": photo
                },
                timeout=30
            )

        return r.status_code == 200

    except Exception:
        return False


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def signal_text(
    symbol,
    hook,
    sl
):

    direction = hook["direction"]

    emoji = (
        "🔴 SHORT"
        if direction == "SHORT"
        else "🟢 LONG"
    )

    return (
        f"🚨 <b>NDS H4 → M5 SIGNAL</b>\n\n"
        f"<b>{emoji}</b>\n"
        f"Symbol: <b>{symbol}</b>\n\n"
        f"Entry: <b>{hook['entry']:.8g}</b>\n"
        f"SL: <b>{sl:.8g}</b>\n"
        f"TP 86.4%: <b>{hook['tp']:.8g}</b>\n"
        f"Hook Range: <b>{hook['range_pct']:.2f}%</b>\n"
        f"Confirmed: <b>{fmt_time(hook['confirmation_time'])}</b>\n\n"
        f"🟡 <b>PAPER ONLY</b>"
    )


# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(symbol):

    # --------------------------------------------------------
    # H4 direction filter
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

    if df.empty:

        DIAG["m5_error"] += 1
        DIAG["m5_empty"] += 1

        return

    if len(df) < 100:

        DIAG["m5_error"] += 1
        DIAG["m5_short"] += 1

        add_error_sample(
            f"{symbol} M5 short data: {len(df)}"
        )

        return

    DIAG["m5_ok"] += 1

    hook = select_m5_hook(
        symbol,
        direction,
        df
    )

    if hook is None:
        return

    # --------------------------------------------------------
    # Entry point
    # --------------------------------------------------------

    entry_time = (
        hook["h3"]["time"]
        if direction == "SHORT"
        else hook["l3"]["time"]
    )

    # --------------------------------------------------------
    # Duplicate
    # --------------------------------------------------------

    if trade_exists(
        symbol,
        direction,
        entry_time
    ):

        DIAG["duplicate"] += 1

        return

    # --------------------------------------------------------
    # Existing open symbol
    # --------------------------------------------------------

    if has_open_symbol(symbol):

        DIAG["open_trade_blocked"] += 1

        return

    # --------------------------------------------------------
    # Maximum open trades
    # --------------------------------------------------------

    if open_trade_count() >= MAX_OPEN_TRADES:

        DIAG["max_open_blocked"] += 1

        return

    # --------------------------------------------------------
    # SL
    # --------------------------------------------------------

    sl = calculate_sl(
        hook,
        df
    )

    if sl is None:
        return

    # Validate SL direction.
    if direction == "SHORT":

        if sl <= hook["entry"]:
            return

    else:

        if sl >= hook["entry"]:
            return

    # --------------------------------------------------------
    # Save hook
    # --------------------------------------------------------

    save_hook(
        symbol,
        hook,
        sl
    )

    # --------------------------------------------------------
    # Paper trade
    # --------------------------------------------------------

    if not REAL_TRADING:

        save_trade(
            symbol,
            hook,
            sl
        )

    # --------------------------------------------------------
    # Chart
    # --------------------------------------------------------

    chart = make_chart(
        symbol,
        df,
        hook,
        sl
    )

    if chart:
        DIAG["charts"] += 1

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    text = signal_text(
        symbol,
        hook,
        sl
    )

    telegram_send(
        text
    )

    if chart:

        telegram_photo(
            chart,
            (
                f"NDS H4 → M5 | "
                f"{symbol} | "
                f"{direction} | "
                f"TP 86.4%"
            )
        )

    DIAG["new_signals"] += 1


# ============================================================
# PERFORMANCE
# ============================================================

def get_performance():

    conn = db_connect()

    row = conn.execute("""
        SELECT
            COUNT(*) AS closed,
            SUM(
                CASE
                    WHEN pnl_pct >= 0 THEN 1
                    ELSE 0
                END
            ) AS wins,
            SUM(
                CASE
                    WHEN pnl_pct < 0 THEN 1
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

    closed = int(row["closed"] or 0)
    wins = int(row["wins"] or 0)
    losses = int(row["losses"] or 0)
    pnl = float(row["pnl"] or 0)

    win_rate = (
        wins / closed * 100
        if closed
        else 0
    )

    return (
        closed,
        wins,
        losses,
        win_rate,
        pnl,
        int(open_count)
    )


# ============================================================
# OPEN TRADE REPORT
# ============================================================

def get_open_trades():

    conn = db_connect()

    rows = conn.execute("""
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY entry_time DESC
    """).fetchall()

    conn.close()

    return rows


def open_trades_text():

    rows = get_open_trades()

    if not rows:
        return "No open trades."

    lines = [
        "━━━ OPEN TRADES ━━━"
    ]

    for row in rows:

        direction = row["direction"]

        emoji = (
            "🔴 SHORT"
            if direction == "SHORT"
            else "🟢 LONG"
        )

        lines.append(
            f"\n{emoji} <b>{row['symbol']}</b>"
        )

        lines.append(
            f"Entry: {float(row['entry_price']):.8g}"
        )

        lines.append(
            f"SL: {float(row['sl']):.8g}"
        )

        lines.append(
            f"TP 86.4%: {float(row['tp']):.8g}"
        )

        entry = float(
            row["entry_price"]
        )

        tp = float(
            row["tp"]
        )

        sl = float(
            row["sl"]
        )

        if direction == "LONG":

            tp_pct = (
                (tp - entry)
                / entry
                * 100
            )

            sl_pct = (
                (sl - entry)
                / entry
                * 100
            )

        else:

            tp_pct = (
                (entry - tp)
                / entry
                * 100
            )

            sl_pct = (
                (entry - sl)
                / entry
                * 100
            )

        lines.append(
            f"TP distance: <b>+{tp_pct:.2f}%</b>"
        )

        lines.append(
            f"SL distance: <b>{sl_pct:.2f}%</b>"
        )

    return "\n".join(lines)


# ============================================================
# DIAGNOSTIC REPORT
# ============================================================

def diagnostic_text(
    runtime
):

    (
        closed,
        wins,
        losses,
        win_rate,
        pnl,
        open_count
    ) = get_performance()

    lines = []

    lines.append(
        f"🔎 <b>NDS H4 → M5 DIAGNOSTIC</b>"
    )

    lines.append(
        f"Version: <b>{VERSION}</b>"
    )

    lines.append(
        f"Time: <b>{utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')}</b>"
    )

    lines.append("")

    lines.append("━━━ H4 ━━━")

    lines.append(
        f"Requests: {DIAG['h4_requests']}"
    )

    lines.append(
        f"Data OK: {DIAG['h4_ok']}"
    )

    lines.append(
        f"Data Error: {DIAG['h4_error']}"
    )

    lines.append(
        f"Empty: {DIAG['h4_empty']}"
    )

    lines.append(
        f"Short Data: {DIAG['h4_short']}"
    )

    lines.append(
        f"Hooks: {DIAG['h4_hooks']}"
    )

    lines.append(
        f"Filtered: {DIAG['h4_filtered']}"
    )

    lines.append("")

    lines.append("━━━ M5 ━━━")

    lines.append(
        f"Requests: {DIAG['m5_requests']}"
    )

    lines.append(
        f"Data OK: {DIAG['m5_ok']}"
    )

    lines.append(
        f"Data Error: {DIAG['m5_error']}"
    )

    lines.append(
        f"Empty: {DIAG['m5_empty']}"
    )

    lines.append(
        f"Short Data: {DIAG['m5_short']}"
    )

    lines.append(
        f"Matching SHORT Hooks: {DIAG['matching_short']}"
    )

    lines.append(
        f"Matching LONG Hooks: {DIAG['matching_long']}"
    )

    lines.append(
        f"No Matching Hook: {DIAG['no_matching_hook']}"
    )

    lines.append(
        f"Old Hooks Removed: {DIAG['old_hooks_removed']}"
    )

    lines.append(
        f"TP Already Touched: {DIAG['tp_already_touched']}"
    )

    lines.append(
        f"Duplicate: {DIAG['duplicate']}"
    )

    lines.append(
        f"Open Trade Blocked: {DIAG['open_trade_blocked']}"
    )

    lines.append(
        f"Max Open Blocked: {DIAG['max_open_blocked']}"
    )

    lines.append("")

    lines.append("━━━ SIGNALS ━━━")

    lines.append(
        f"New Signals: {DIAG['new_signals']}"
    )

    lines.append(
        f"Charts: {DIAG['charts']}"
    )

    lines.append("")

    lines.append("━━━ OPEN TRADES ━━━")

    lines.append(
        open_trades_text()
    )

    lines.append("")

    lines.append("━━━ PAPER PERFORMANCE ━━━")

    lines.append(
        f"Closed: {closed}"
    )

    lines.append(
        f"Wins: {wins}"
    )

    lines.append(
        f"Losses: {losses}"
    )

    lines.append(
        f"Win Rate: {win_rate:.2f}%"
    )

    lines.append(
        f"Realized PnL: {pnl:+.2f}%"
    )

    lines.append(
        f"Open Trades: {open_count}"
    )

    lines.append("")

    lines.append(
        "🟡 <b>PAPER ONLY</b>"
    )

    lines.append(
        "No real exchange orders are sent."
    )

    if ERROR_SAMPLES:

        lines.append("")
        lines.append(
            "━━━ API ERROR SAMPLES ━━━"
        )

        for err in ERROR_SAMPLES[:8]:

            lines.append(
                f"• {err}"
            )

    lines.append("")

    lines.append(
        f"Runtime: {runtime:.1f}s"
    )

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():

    started = time.time()

    init_db()

    # First monitor old paper trades.
    monitor_open_trades()

    symbols = get_futures_instruments()

    DIAG["assets_scanned"] = len(
        symbols
    )

    if not symbols:

        msg = (
            f"❌ NDS {VERSION}\n"
            f"No Futures instruments found.\n"
            f"Check Kraken instruments endpoint."
        )

        telegram_send(
            msg
        )

        print(msg)

        return

    for symbol in symbols:

        try:

            process_symbol(
                symbol
            )

        except Exception as exc:

            add_error_sample(
                f"{symbol} process error: {exc}"
            )

            traceback.print_exc()

        time.sleep(
            SCAN_SLEEP_SECONDS
        )

    # Monitor newly opened trades once more.
    monitor_open_trades()

    runtime = (
        time.time()
        - started
    )

    report = diagnostic_text(
        runtime
    )

    print(report)

    telegram_send(
        report
    )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    main()
