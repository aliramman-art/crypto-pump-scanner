# ============================================================
# NDS H4 -> M5 LIVE SCANNER
# VERSION 5.4.1
# ============================================================
#
# PAPER TRADING ONLY
#
# NO 123F
# NO M1
#
# H4:
#   Determines allowed direction from the newest valid
#   confirmed H4 Hook.
#
# M5:
#   Finds the matching six-point NDS Hook.
#
# SHORT:
#   START -> H1 -> L1 -> H2 -> L2 -> H3
#
#   H2 > H1
#   L2 < L1
#   H3 > H2
#
#   ENTRY = H3
#   TP    = 86.4% retracement
#
# LONG:
#   START -> L1 -> H1 -> L2 -> H2 -> L3
#
#   L2 < L1
#   H2 > H1
#   L3 < L2
#
#   ENTRY = L3
#   TP    = 86.4% retracement
#
# ============================================================

import os
import time
import sqlite3
import traceback
from datetime import datetime, timezone

import requests
import pandas as pd
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "5.4.1"

REAL_TRADING = False

KRAKEN_FUTURES_URL = (
    "https://futures.kraken.com/derivatives/api/v3"
)

KRAKEN_CHART_URL = (
    "https://futures.kraken.com/api/charts/v1"
)

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

DB_FILE = "nds_h4_m5_v541.db"

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

REQUEST_TIMEOUT = 20

SCAN_SLEEP_SECONDS = 0.5

CHART_CANDLES = 240


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "NDS-H4-M5-Scanner/5.4.1",
    "Accept": "application/json",
})


# ============================================================
# DIAGNOSTICS
# ============================================================

DIAG = {

    "assets_scanned": 0,

    # H4
    "h4_requests": 0,
    "h4_ok": 0,
    "h4_error": 0,
    "h4_empty": 0,
    "h4_short": 0,
    "h4_hooks": 0,
    "h4_valid_hooks": 0,
    "h4_short_direction": 0,
    "h4_long_direction": 0,
    "h4_filtered": 0,

    # M5
    "m5_requests": 0,
    "m5_ok": 0,
    "m5_error": 0,
    "m5_empty": 0,
    "m5_short": 0,

    "matching_short": 0,
    "matching_long": 0,
    "no_matching_hook": 0,

    "old_hooks_removed": 0,
    "tp_already_touched": 0,

    "duplicate": 0,
    "open_trade_blocked": 0,
    "max_open_blocked": 0,

    # Signals
    "new_signals": 0,
    "charts": 0,

    # Performance
    "closed": 0,
    "wins": 0,
    "losses": 0,
    "realized_pnl": 0.0,
}

ERROR_SAMPLES = []


def add_error_sample(message):

    message = str(message)

    message = message.replace(
        "\n",
        " "
    )

    if len(message) > 350:
        message = message[:350] + "..."

    if (
        message not in ERROR_SAMPLES
        and len(ERROR_SAMPLES) < 12
    ):
        ERROR_SAMPLES.append(message)


# ============================================================
# TIME
# ============================================================

def utc_now():

    return datetime.now(
        timezone.utc
    )


def utc_ts():

    return int(
        time.time()
    )


def fmt_time(ts):

    try:

        return datetime.fromtimestamp(
            float(ts),
            tz=timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

    except Exception:

        return str(ts)


# ============================================================
# HTTP GET
# ============================================================

def http_get(
    url,
    params=None
):

    response = SESSION.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"HTTP {response.status_code} | "
            f"{url} | "
            f"{response.text[:500]}"
        )

    try:

        return response.json()

    except Exception as exc:

        raise RuntimeError(
            f"JSON parse error | "
            f"{url} | "
            f"{exc}"
        )


# ============================================================
# RESOLUTION
# ============================================================

def normalize_resolution(
    interval
):

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

    return mapping.get(
        str(interval),
        str(interval)
    )


# ============================================================
# PARSE CANDLES
# ============================================================

def parse_candles(raw):

    if not isinstance(
        raw,
        dict
    ):

        raise RuntimeError(
            "Unexpected candle response type: "
            + type(raw).__name__
        )

    candles = raw.get(
        "candles"
    )

    if candles is None:

        raise RuntimeError(
            "Candle response has no candles field: "
            + str(raw)[:500]
        )

    if not isinstance(
        candles,
        list
    ):

        raise RuntimeError(
            "'candles' is not a list"
        )

    if not candles:

        return pd.DataFrame()

    rows = []

    for item in candles:

        try:

            if isinstance(
                item,
                dict
            ):

                timestamp = (
                    item.get("time")
                    or item.get("timestamp")
                )

                o = item.get("open")
                h = item.get("high")
                l = item.get("low")
                c = item.get("close")
                v = item.get(
                    "volume",
                    0
                )

            elif isinstance(
                item,
                (list, tuple)
            ):

                if len(item) < 5:
                    continue

                timestamp = item[0]
                o = item[1]
                h = item[2]
                l = item[3]
                c = item[4]

                v = (
                    item[5]
                    if len(item) > 5
                    else 0
                )

            else:

                continue

            timestamp = float(
                timestamp
            )

            # milliseconds -> seconds
            if timestamp > 10_000_000_000:

                timestamp /= 1000.0

            rows.append({

                "time": timestamp,

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
            "Candle list exists but no valid OHLC rows"
        )

    df = pd.DataFrame(
        rows
    )

    df = df.drop_duplicates(
        subset=["time"]
    )

    df = df.sort_values(
        "time"
    ).reset_index(
        drop=True
    )

    return df


# ============================================================
# GET CANDLES
# ============================================================

def get_candles(
    symbol,
    interval,
    count
):

    resolution = normalize_resolution(
        interval
    )

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

        return parse_candles(
            raw
        )

    except Exception as exc:

        add_error_sample(
            f"{symbol} {resolution}: {exc}"
        )

        return pd.DataFrame()


# ============================================================
# INSTRUMENTS
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

            if not isinstance(
                item,
                dict
            ):
                continue

            symbol = (
                item.get("symbol")
                or item.get("tradeable")
                or item.get("instrument")
            )

            if not symbol:
                continue

            symbol = str(
                symbol
            )

            if not symbol.startswith(
                "PF_"
            ):
                continue

            if "USD" not in symbol:
                continue

            symbols.append(
                symbol
            )

        symbols = sorted(
            list(
                dict.fromkeys(
                    symbols
                )
            )
        )

        return symbols[
            :TARGET_ASSETS
        ]

    except Exception as exc:

        add_error_sample(
            f"instruments: {exc}"
        )

        return []


# ============================================================
# PIVOT DETECTION
# ============================================================

def find_pivots(df):

    if df.empty:

        return [], []

    highs = []
    lows = []

    high_values = (
        df["high"].values
    )

    low_values = (
        df["low"].values
    )

    times = (
        df["time"].values
    )

    n = len(df)

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT
    ):

        current_high = (
            high_values[i]
        )

        current_low = (
            low_values[i]
        )

        left_highs = (
            high_values[
                i - PIVOT_LEFT:i
            ]
        )

        right_highs = (
            high_values[
                i + 1:
                i + 1 + PIVOT_RIGHT
            ]
        )

        left_lows = (
            low_values[
                i - PIVOT_LEFT:i
            ]
        )

        right_lows = (
            low_values[
                i + 1:
                i + 1 + PIVOT_RIGHT
            ]
        )

        is_high = (

            current_high
            > left_highs.max()

            and

            current_high
            >= right_highs.max()

        )

        is_low = (

            current_low
            < left_lows.min()

            and

            current_low
            <= right_lows.min()

        )

        if is_high:

            highs.append({

                "type": "H",

                "time": float(
                    times[i]
                ),

                "price": float(
                    current_high
                ),

                "index": i,

            })

        if is_low:

            lows.append({

                "type": "L",

                "time": float(
                    times[i]
                ),

                "price": float(
                    current_low
                ),

                "index": i,

            })

    return highs, lows


# ============================================================
# ORDER PIVOTS
# ============================================================

def build_ordered_pivots(
    highs,
    lows
):

    pivots = sorted(

        highs + lows,

        key=lambda x: x["time"]

    )

    if not pivots:

        return []

    result = []

    for pivot in pivots:

        if not result:

            result.append(
                pivot
            )

            continue

        last = result[-1]

        # Same pivot type:
        # retain the more extreme pivot.
        if pivot["type"] == last["type"]:

            if pivot["type"] == "H":

                if (
                    pivot["price"]
                    >= last["price"]
                ):

                    result[-1] = pivot

            else:

                if (
                    pivot["price"]
                    <= last["price"]
                ):

                    result[-1] = pivot

        else:

            result.append(
                pivot
            )

    return result


# ============================================================
# HOOK DETECTION
# ============================================================

def detect_hooks(df):

    highs, lows = find_pivots(
        df
    )

    pivots = build_ordered_pivots(
        highs,
        lows
    )

    hooks = []

    if len(pivots) < 6:

        return hooks

    for i in range(
        len(pivots) - 5
    ):

        p = pivots[
            i:i + 6
        ]

        types = [
            x["type"]
            for x in p
        ]

        # ====================================================
        # SHORT
        #
        # START L
        # H1
        # L1
        # H2
        # L2
        # H3
        #
        # H2 > H1
        # L2 < L1
        # H3 > H2
        # ====================================================

        if types == [
            "L",
            "H",
            "L",
            "H",
            "L",
            "H",
        ]:

            start, h1, l1, h2, l2, h3 = p

            valid = (

                h2["price"]
                > h1["price"]

                and

                l2["price"]
                < l1["price"]

                and

                h3["price"]
                > h2["price"]

            )

            if not valid:
                continue

            start_price = (
                start["price"]
            )

            entry = (
                h3["price"]
            )

            move = (
                entry
                - start_price
            )

            if move <= 0:
                continue

            range_pct = (
                move
                / start_price
                * 100
            )

            if (
                range_pct
                < M5_MIN_HOOK_RANGE_PCT
            ):
                continue

            # 86.4% retracement
            tp = (
                entry
                - NDS_RETRACE * move
            )

            confirmation_time = (

                h3["time"]

                + (
                    5
                    * 60
                    * PIVOT_RIGHT
                )

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

                "confirmation_time":
                    confirmation_time,

            })

        # ====================================================
        # LONG
        #
        # START H
        # L1
        # H1
        # L2
        # H2
        # L3
        #
        # L2 < L1
        # H2 > H1
        # L3 < L2
        # ====================================================

        if types == [
            "H",
            "L",
            "H",
            "L",
            "H",
            "L",
        ]:

            start, l1, h1, l2, h2, l3 = p

            valid = (

                l2["price"]
                < l1["price"]

                and

                h2["price"]
                > h1["price"]

                and

                l3["price"]
                < l2["price"]

            )

            if not valid:
                continue

            start_price = (
                start["price"]
            )

            entry = (
                l3["price"]
            )

            move = (
                start_price
                - entry
            )

            if move <= 0:
                continue

            range_pct = (
                move
                / start_price
                * 100
            )

            if (
                range_pct
                < M5_MIN_HOOK_RANGE_PCT
            ):
                continue

            # 86.4% retracement
            tp = (
                entry
                + NDS_RETRACE * move
            )

            confirmation_time = (

                l3["time"]

                + (
                    5
                    * 60
                    * PIVOT_RIGHT
                )

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

                "confirmation_time":
                    confirmation_time,

            })

    return hooks


# ============================================================
# H4 DIRECTION
#
# IMPORTANT:
# H4 does NOT require the final Hook point to equal
# the last possible pivot in the dataset.
#
# It selects the NEWEST VALID CONFIRMED Hook.
# ============================================================

def get_h4_direction(
    symbol
):

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
            f"{symbol} H4 short data: "
            f"{len(df)} candles"
        )

        return None

    DIAG["h4_ok"] += 1

    hooks = detect_hooks(
        df
    )

    DIAG["h4_hooks"] += len(
        hooks
    )

    if not hooks:

        DIAG["h4_filtered"] += 1

        return None

    now = utc_ts()

    valid_hooks = []

    for hook in hooks:

        confirmation = float(
            hook["confirmation_time"]
        )

        # Hook is not confirmed yet.
        if confirmation > now:

            continue

        # Hook is too old.
        if (
            now - confirmation
            > MAX_HOOK_AGE_SECONDS
        ):

            continue

        valid_hooks.append(
            hook
        )

    DIAG["h4_valid_hooks"] += len(
        valid_hooks
    )

    if not valid_hooks:

        DIAG["h4_filtered"] += 1

        return None

    # --------------------------------------------------------
    # IMPORTANT FIX:
    #
    # Select the newest valid confirmed H4 Hook.
    #
    # Do NOT require its final pivot to be the latest pivot
    # in the entire H4 dataframe.
    # --------------------------------------------------------

    valid_hooks.sort(

        key=lambda x:
        x["confirmation_time"],

        reverse=True

    )

    selected = valid_hooks[0]

    direction = (
        selected["direction"]
    )

    if direction == "SHORT":

        DIAG[
            "h4_short_direction"
        ] += 1

    else:

        DIAG[
            "h4_long_direction"
        ] += 1

    return direction


# ============================================================
# M5 HOOK SELECTION
# ============================================================

def select_m5_hook(
    symbol,
    direction,
    df
):

    hooks = detect_hooks(
        df
    )

    if not hooks:

        DIAG[
            "no_matching_hook"
        ] += 1

        return None

    now = utc_ts()

    candidates = []

    for hook in hooks:

        if (
            hook["direction"]
            != direction
        ):

            continue

        confirmation = float(
            hook["confirmation_time"]
        )

        if confirmation > now:

            continue

        if (
            now - confirmation
            > MAX_HOOK_AGE_SECONDS
        ):

            DIAG[
                "old_hooks_removed"
            ] += 1

            continue

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # M5 must also use the newest confirmed Hook.
        # We do not require the Hook to equal the last pivot.
        # ----------------------------------------------------

        final_point = (

            hook["h3"]

            if direction == "SHORT"

            else

            hook["l3"]

        )

        future = df[
            df["time"]
            >= final_point["time"]
        ]

        tp = float(
            hook["tp"]
        )

        # ----------------------------------------------------
        # TP already touched after Hook
        # ----------------------------------------------------

        if direction == "SHORT":

            if (
                not future.empty
                and
                float(
                    future["low"].min()
                )
                <= tp
            ):

                DIAG[
                    "tp_already_touched"
                ] += 1

                continue

            DIAG[
                "matching_short"
            ] += 1

        else:

            if (
                not future.empty
                and
                float(
                    future["high"].max()
                )
                >= tp
            ):

                DIAG[
                    "tp_already_touched"
                ] += 1

                continue

            DIAG[
                "matching_long"
            ] += 1

        candidates.append(
            hook
        )

    if not candidates:

        return None

    # Newest confirmed Hook first.
    candidates.sort(

        key=lambda x:
        x["confirmation_time"],

        reverse=True

    )

    return candidates[0]


# ============================================================
# STOP LOSS
#
# Previous valid pivot:
# SHORT -> previous valid HIGH
# LONG  -> previous valid LOW
# ============================================================

def calculate_sl(
    hook,
    df
):

    direction = (
        hook["direction"]
    )

    final_point = (

        hook["h3"]

        if direction == "SHORT"

        else

        hook["l3"]

    )

    before = df[
        df["time"]
        < final_point["time"]
    ]

    if before.empty:

        return None

    highs, lows = find_pivots(
        before
    )

    if direction == "SHORT":

        valid_highs = [

            p["price"]

            for p in highs

            if p["time"]
            < final_point["time"]

        ]

        if not valid_highs:

            return None

        return float(
            valid_highs[-1]
        )

    else:

        valid_lows = [

            p["price"]

            for p in lows

            if p["time"]
            < final_point["time"]

        ]

        if not valid_lows:

            return None

        return float(
            valid_lows[-1]
        )


# ============================================================
# DATABASE
# ============================================================

def db_connect():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.row_factory = (
        sqlite3.Row
    )

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


def trade_exists(
    symbol,
    direction,
    entry_time
):

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

    return int(
        row[0]
    )


def has_open_symbol(
    symbol
):

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


def save_hook(
    symbol,
    hook,
    sl
):

    entry_time = (

        hook["h3"]["time"]

        if hook["direction"] == "SHORT"

        else

        hook["l3"]["time"]

    )

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

            entry_time,

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


def save_trade(
    symbol,
    hook,
    sl
):

    entry_time = (

        hook["h3"]["time"]

        if hook["direction"] == "SHORT"

        else

        hook["l3"]["time"]

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

def calculate_pnl(
    direction,
    entry,
    exit_price
):

    if entry == 0:

        return 0.0

    if direction == "LONG":

        return (
            (exit_price - entry)
            / entry
            * 100
        )

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

        symbol = trade[
            "symbol"
        ]

        df = get_candles(
            symbol,
            M5_INTERVAL,
            20
        )

        if df.empty:
            continue

        direction = trade[
            "direction"
        ]

        entry = float(
            trade["entry_price"]
        )

        tp = float(
            trade["tp"]
        )

        sl = (

            float(trade["sl"])

            if trade["sl"] is not None

            else None

        )

        for _, candle in df.iterrows():

            candle_time = float(
                candle["time"]
            )

            if (
                candle_time
                <= float(
                    trade["entry_time"]
                )
            ):
                continue

            high = float(
                candle["high"]
            )

            low = float(
                candle["low"]
            )

            exit_price = None

            if direction == "LONG":

                if (
                    sl is not None
                    and low <= sl
                ):

                    exit_price = sl

                elif high >= tp:

                    exit_price = tp

            else:

                if (
                    sl is not None
                    and high >= sl
                ):

                    exit_price = sl

                elif low <= tp:

                    exit_price = tp

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

            DIAG[
                "closed"
            ] += 1

            if pnl >= 0:

                DIAG[
                    "wins"
                ] += 1

            else:

                DIAG[
                    "losses"
                ] += 1

            DIAG[
                "realized_pnl"
            ] += pnl

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

    direction = (
        hook["direction"]
    )

    if direction == "SHORT":

        points = [

            ("START", hook["start"]),

            ("H1", hook["h1"]),

            ("L1", hook["l1"]),

            ("H2", hook["h2"]),

            ("L2", hook["l2"]),

            ("H3", hook["h3"]),

        ]

    else:

        points = [

            ("START", hook["start"]),

            ("L1", hook["l1"]),

            ("H1", hook["h1"]),

            ("L2", hook["l2"]),

            ("H2", hook["h2"]),

            ("L3", hook["l3"]),

        ]

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

    # --------------------------------------------------------
    # Labels
    # --------------------------------------------------------

    for i, (
        name,
        point
    ) in enumerate(points):

        offset = (
            12
            if i % 2 == 0
            else -18
        )

        ax.annotate(

            name,

            (
                pd.to_datetime(
                    point["time"],
                    unit="s",
                    utc=True
                ),
                point["price"]
            ),

            xytext=(
                0,
                offset
            ),

            textcoords="offset points",

            ha="center",

            fontsize=9,

            fontweight="bold"

        )

    # --------------------------------------------------------
    # Entry
    # --------------------------------------------------------

    ax.axhline(

        hook["entry"],

        linestyle="--",

        linewidth=1.3,

        label=(
            f"ENTRY "
            f"{hook['entry']:.8g}"
        )

    )

    # --------------------------------------------------------
    # TP 86.4%
    # --------------------------------------------------------

    ax.axhline(

        hook["tp"],

        linestyle="-.",

        linewidth=1.6,

        label=(
            f"TP 86.4% "
            f"{hook['tp']:.8g}"
        )

    )

    # --------------------------------------------------------
    # SL
    # --------------------------------------------------------

    if sl is not None:

        ax.axhline(

            sl,

            linestyle=":",

            linewidth=1.6,

            label=(
                f"SL "
                f"{sl:.8g}"
            )

        )

    # --------------------------------------------------------
    # Confirmation
    # --------------------------------------------------------

    confirmation_dt = (
        pd.to_datetime(
            hook["confirmation_time"],
            unit="s",
            utc=True
        )
    )

    ax.axvline(

        confirmation_dt,

        linestyle=":",

        linewidth=1.0

    )

    ax.set_title(

        f"NDS H4 → M5 | "
        f"{symbol} | "
        f"{direction} | "
        f"TP 86.4%"

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

    safe_symbol = (
        symbol.replace(
            "/",
            "_"
        )
    )

    filename = (

        f"{safe_symbol}_"
        f"{direction}_"
        f"{int(hook['entry'])}_"
        f"{int(hook['confirmation_time'])}.png"

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

    plt.close(
        fig
    )

    return path


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(
    text
):

    if not TELEGRAM_BOT_TOKEN:
        return False

    if not TELEGRAM_CHAT_ID:
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/"
        f"sendMessage"
    )

    try:

        response = SESSION.post(

            url,

            data={

                "chat_id":
                    TELEGRAM_CHAT_ID,

                "text":
                    text,

                "parse_mode":
                    "HTML",

            },

            timeout=20

        )

        return (
            response.status_code
            == 200
        )

    except Exception:

        return False


def telegram_photo(
    path,
    caption
):

    if not TELEGRAM_BOT_TOKEN:
        return False

    if not TELEGRAM_CHAT_ID:
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/"
        f"sendPhoto"
    )

    try:

        with open(
            path,
            "rb"
        ) as photo:

            response = SESSION.post(

                url,

                data={

                    "chat_id":
                        TELEGRAM_CHAT_ID,

                    "caption":
                        caption,

                    "parse_mode":
                        "HTML",

                },

                files={

                    "photo":
                        photo

                },

                timeout=30

            )

        return (
            response.status_code
            == 200
        )

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

    direction = (
        hook["direction"]
    )

    if direction == "SHORT":

        emoji = "🔴 SHORT"

    else:

        emoji = "🟢 LONG"

    return (

        f"🚨 <b>NDS H4 → M5 SIGNAL</b>\n\n"

        f"<b>{emoji}</b>\n"

        f"Symbol: <b>{symbol}</b>\n\n"

        f"Entry: "
        f"<b>{hook['entry']:.8g}</b>\n"

        f"SL: "
        f"<b>{sl:.8g}</b>\n"

        f"TP 86.4%: "
        f"<b>{hook['tp']:.8g}</b>\n"

        f"Hook Range: "
        f"<b>{hook['range_pct']:.2f}%</b>\n"

        f"Confirmed: "
        f"<b>{fmt_time(hook['confirmation_time'])}</b>\n\n"

        f"🟡 <b>PAPER ONLY</b>"

    )


# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(
    symbol
):

    # --------------------------------------------------------
    # H4
    # --------------------------------------------------------

    direction = get_h4_direction(
        symbol
    )

    if direction is None:

        DIAG[
            "h4_filtered"
        ] += 1

        return

    # --------------------------------------------------------
    # M5
    # --------------------------------------------------------

    DIAG[
        "m5_requests"
    ] += 1

    df = get_candles(
        symbol,
        M5_INTERVAL,
        M5_CANDLES
    )

    if df.empty:

        DIAG[
            "m5_error"
        ] += 1

        DIAG[
            "m5_empty"
        ] += 1

        return

    if len(df) < 100:

        DIAG[
            "m5_error"
        ] += 1

        DIAG[
            "m5_short"
        ] += 1

        add_error_sample(

            f"{symbol} M5 short data: "
            f"{len(df)} candles"

        )

        return

    DIAG[
        "m5_ok"
    ] += 1

    hook = select_m5_hook(

        symbol,

        direction,

        df

    )

    if hook is None:

        return

    # --------------------------------------------------------
    # Entry time
    # --------------------------------------------------------

    entry_time = (

        hook["h3"]["time"]

        if direction == "SHORT"

        else

        hook["l3"]["time"]

    )

    # --------------------------------------------------------
    # Duplicate
    # --------------------------------------------------------

    if trade_exists(

        symbol,

        direction,

        entry_time

    ):

        DIAG[
            "duplicate"
        ] += 1

        return

    # --------------------------------------------------------
    # Existing open trade
    # --------------------------------------------------------

    if has_open_symbol(
        symbol
    ):

        DIAG[
            "open_trade_blocked"
        ] += 1

        return

    # --------------------------------------------------------
    # Maximum open trades
    # --------------------------------------------------------

    if (
        open_trade_count()
        >= MAX_OPEN_TRADES
    ):

        DIAG[
            "max_open_blocked"
        ] += 1

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

    # Validate SL position.

    if direction == "SHORT":

        if sl <= hook["entry"]:

            return

    else:

        if sl >= hook["entry"]:

            return

    # --------------------------------------------------------
    # Save Hook
    # --------------------------------------------------------

    save_hook(
        symbol,
        hook,
        sl
    )

    # --------------------------------------------------------
    # PAPER TRADE
    # --------------------------------------------------------

    if not REAL_TRADING:

        save_trade(
            symbol,
            hook,
            sl
        )

    # --------------------------------------------------------
    # CHART
    # --------------------------------------------------------

    chart = make_chart(

        symbol,

        df,

        hook,

        sl

    )

    if chart:

        DIAG[
            "charts"
        ] += 1

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    telegram_send(

        signal_text(
            symbol,
            hook,
            sl
        )

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

    DIAG[
        "new_signals"
    ] += 1


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
                    WHEN pnl_pct >= 0
                    THEN 1
                    ELSE 0
                END
            ) AS wins,
            SUM(
                CASE
                    WHEN pnl_pct < 0
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

        wins
        / closed
        * 100

        if closed

        else

        0

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
# OPEN TRADES
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

        return (
            "No open trades."
        )

    lines = [
        "━━━ OPEN TRADES ━━━"
    ]

    for row in rows:

        direction = row[
            "direction"
        ]

        emoji = (

            "🔴 SHORT"

            if direction == "SHORT"

            else

            "🟢 LONG"

        )

        lines.append(
            f"\n{emoji} "
            f"<b>{row['symbol']}</b>"
        )

        entry = float(
            row["entry_price"]
        )

        sl = float(
            row["sl"]
        )

        tp = float(
            row["tp"]
        )

        lines.append(
            f"Entry: {entry:.8g}"
        )

        lines.append(
            f"SL: {sl:.8g}"
        )

        lines.append(
            f"TP 86.4%: {tp:.8g}"
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
            f"TP distance: "
            f"<b>+{tp_pct:.2f}%</b>"
        )

        lines.append(
            f"SL distance: "
            f"<b>{sl_pct:.2f}%</b>"
        )

    return "\n".join(
        lines
    )


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
        "🔎 <b>NDS H4 → M5 DIAGNOSTIC</b>"
    )

    lines.append(
        f"Version: <b>{VERSION}</b>"
    )

    lines.append(
        "Time: <b>"
        + utc_now().strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        + "</b>"
    )

    lines.append("")

    # ========================================================
    # H4
    # ========================================================

    lines.append(
        "━━━ H4 ━━━"
    )

    lines.append(
        f"Requests: "
        f"{DIAG['h4_requests']}"
    )

    lines.append(
        f"Data OK: "
        f"{DIAG['h4_ok']}"
    )

    lines.append(
        f"Data Error: "
        f"{DIAG['h4_error']}"
    )

    lines.append(
        f"Empty: "
        f"{DIAG['h4_empty']}"
    )

    lines.append(
        f"Short Data: "
        f"{DIAG['h4_short']}"
    )

    lines.append(
        f"Hooks: "
        f"{DIAG['h4_hooks']}"
    )

    lines.append(
        f"Valid Hooks: "
        f"{DIAG['h4_valid_hooks']}"
    )

    lines.append(
        f"SHORT Direction: "
        f"{DIAG['h4_short_direction']}"
    )

    lines.append(
        f"LONG Direction: "
        f"{DIAG['h4_long_direction']}"
    )

    lines.append(
        f"Filtered: "
        f"{DIAG['h4_filtered']}"
    )

    lines.append("")

    # ========================================================
    # M5
    # ========================================================

    lines.append(
        "━━━ M5 ━━━"
    )

    lines.append(
        f"Requests: "
        f"{DIAG['m5_requests']}"
    )

    lines.append(
        f"Data OK: "
        f"{DIAG['m5_ok']}"
    )

    lines.append(
        f"Data Error: "
        f"{DIAG['m5_error']}"
    )

    lines.append(
        f"Empty: "
        f"{DIAG['m5_empty']}"
    )

    lines.append(
        f"Short Data: "
        f"{DIAG['m5_short']}"
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
        f"{DIAG['no_matching_hook']}"
    )

    lines.append(
        f"Old Hooks Removed: "
        f"{DIAG['old_hooks_removed']}"
    )

    lines.append(
        f"TP Already Touched: "
        f"{DIAG['tp_already_touched']}"
    )

    lines.append(
        f"Duplicate: "
        f"{DIAG['duplicate']}"
    )

    lines.append(
        f"Open Trade Blocked: "
        f"{DIAG['open_trade_blocked']}"
    )

    lines.append(
        f"Max Open Blocked: "
        f"{DIAG['max_open_blocked']}"
    )

    lines.append("")

    # ========================================================
    # SIGNALS
    # ========================================================

    lines.append(
        "━━━ SIGNALS ━━━"
    )

    lines.append(
        f"New Signals: "
        f"{DIAG['new_signals']}"
    )

    lines.append(
        f"Charts: "
        f"{DIAG['charts']}"
    )

    lines.append("")

    # ========================================================
    # OPEN TRADES
    # ========================================================

    lines.append(
        open_trades_text()
    )

    lines.append("")

    # ========================================================
    # PERFORMANCE
    # ========================================================

    lines.append(
        "━━━ PAPER PERFORMANCE ━━━"
    )

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

    # ========================================================
    # ERROR SAMPLES
    # ========================================================

    if ERROR_SAMPLES:

        lines.append("")

        lines.append(
            "━━━ API ERROR SAMPLES ━━━"
        )

        for error in ERROR_SAMPLES[:8]:

            lines.append(
                f"• {error}"
            )

    lines.append("")

    lines.append(
        f"Runtime: {runtime:.1f}s"
    )

    return "\n".join(
        lines
    )


# ============================================================
# MAIN
# ============================================================

def main():

    started = time.time()

    init_db()

    # --------------------------------------------------------
    # Existing paper trades
    # --------------------------------------------------------

    monitor_open_trades()

    # --------------------------------------------------------
    # Assets
    # --------------------------------------------------------

    symbols = (
        get_futures_instruments()
    )

    DIAG[
        "assets_scanned"
    ] = len(symbols)

    if not symbols:

        message = (

            f"❌ NDS {VERSION}\n"

            "No Futures instruments found."

        )

        print(
            message
        )

        telegram_send(
            message
        )

        return

    # --------------------------------------------------------
    # Scan
    # --------------------------------------------------------

    for symbol in symbols:

        try:

            process_symbol(
                symbol
            )

        except Exception as exc:

            add_error_sample(

                f"{symbol} "
                f"process error: "
                f"{exc}"

            )

            traceback.print_exc()

        time.sleep(
            SCAN_SLEEP_SECONDS
        )

    # --------------------------------------------------------
    # Update paper trades
    # --------------------------------------------------------

    monitor_open_trades()

    runtime = (
        time.time()
        - started
    )

    report = diagnostic_text(
        runtime
    )

    print(
        report
    )

    telegram_send(
        report
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
