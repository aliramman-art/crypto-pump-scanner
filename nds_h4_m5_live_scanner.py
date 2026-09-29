# ============================================================
# NDS H4 -> M5 LIVE SCANNER
# VERSION 5.4.2
# ============================================================
#
# PAPER TRADING ONLY
#
# NO REAL ORDERS
# NO M1
# NO 123F
#
# H4:
#   Determines allowed direction from the newest confirmed
#   valid NDS Hook.
#
# M5:
#   Searches for the matching NDS Hook.
#
# SHORT / POSITIVE HOOK:
#   START -> H1 -> L1 -> H2 -> L2 -> H3
#
#   H2 > H1
#   L2 < L1
#   H3 > H2
#
#   ENTRY = H3
#   TP    = 86.4% retracement from START to H3
#   SL    = previous valid M5 high
#
# LONG / NEGATIVE HOOK:
#   START -> L1 -> H1 -> L2 -> H2 -> L3
#
#   L2 < L1
#   H2 > H1
#   L3 < L2
#
#   ENTRY = L3
#   TP    = 86.4% retracement from START to L3
#   SL    = previous valid M5 low
#
# IMPORTANT:
#   H4 confirmation uses 4 HOURS per right pivot.
#   M5 confirmation uses 5 MINUTES per right pivot.
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

VERSION = "5.4.2"

REAL_TRADING = False

KRAKEN_FUTURES_URL = (
    "https://futures.kraken.com/derivatives/api/v3"
)

KRAKEN_CHART_URL = (
    "https://futures.kraken.com/api/charts/v1"
)

TARGET_ASSETS = 100

H4_INTERVAL = "4h"
M5_INTERVAL = "5m"

H4_INTERVAL_MINUTES = 240
M5_INTERVAL_MINUTES = 5

H4_CANDLES = 320
M5_CANDLES = 1500

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

NDS_RETRACE = 0.864

M5_MIN_HOOK_RANGE_PCT = 0.20

MAX_HOOK_AGE_SECONDS = 6 * 60 * 60

MAX_OPEN_TRADES = 3

REQUEST_TIMEOUT = 20

SCAN_SLEEP_SECONDS = 0.25

CHART_CANDLES = 240

DB_FILE = "nds_h4_m5_v542.db"

CHART_DIR = "nds_h4_m5_charts"

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
# DIAGNOSTIC
# ============================================================

DIAG = {
    "assets_scanned": 0,

    "h4_requests": 0,
    "h4_data_ok": 0,
    "h4_data_error": 0,
    "h4_empty": 0,
    "h4_short": 0,
    "h4_pivots": 0,
    "h4_hooks": 0,
    "h4_valid_hooks": 0,
    "h4_short_direction": 0,
    "h4_long_direction": 0,
    "h4_filtered": 0,

    "m5_requests": 0,
    "m5_data_ok": 0,
    "m5_data_error": 0,
    "m5_empty": 0,
    "m5_short": 0,
    "m5_pivots": 0,
    "m5_hooks": 0,
    "m5_valid_hooks": 0,

    "signals": 0,
    "duplicate_signals": 0,
    "already_open": 0,
    "max_open": 0,

    "api_errors": [],
}

H4_DIRECTION = {}

START_TIME = time.time()


# ============================================================
# BASIC HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_now_ts():
    return int(utc_now().timestamp())


def fmt_ts(ts):
    if ts is None:
        return "-"

    try:
        return datetime.fromtimestamp(
            float(ts),
            tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return "-"


def fmt_price(value):
    if value is None:
        return "-"

    try:
        value = float(value)

        if value >= 1000:
            return f"{value:.2f}"

        if value >= 1:
            return f"{value:.5f}"

        if value >= 0.01:
            return f"{value:.7f}"

        return f"{value:.10f}"

    except Exception:
        return "-"


def pct_change(a, b):
    try:
        if float(a) == 0:
            return 0.0

        return (
            (float(b) - float(a))
            / float(a)
            * 100.0
        )
    except Exception:
        return 0.0


# ============================================================
# TELEGRAM
# ============================================================

def telegram_enabled():
    return bool(
        TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    )


def telegram_send(text):
    if not telegram_enabled():
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
        r = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        return r.ok

    except Exception as e:
        DIAG["api_errors"].append(
            f"Telegram text: {str(e)[:160]}"
        )
        return False


def telegram_send_photo(photo_path, caption):
    if not telegram_enabled():
        return False

    if not photo_path:
        return False

    if not os.path.exists(photo_path):
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    try:
        with open(photo_path, "rb") as photo:

            files = {
                "photo": photo
            }

            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
                "parse_mode": "HTML",
            }

            r = requests.post(
                url,
                files=files,
                data=data,
                timeout=REQUEST_TIMEOUT
            )

        return r.ok

    except Exception as e:
        DIAG["api_errors"].append(
            f"Telegram photo: {str(e)[:160]}"
        )
        return False


# ============================================================
# KRAKEN API
# ============================================================

def get_futures_instruments():

    url = (
        f"{KRAKEN_FUTURES_URL}/instruments"
    )

    try:
        r = requests.get(
            url,
            timeout=REQUEST_TIMEOUT
        )

        r.raise_for_status()

        data = r.json()

        instruments = data.get(
            "instruments",
            []
        )

        symbols = []

        for item in instruments:

            symbol = (
                item.get("symbol")
                or item.get("instrument")
                or ""
            )

            if not symbol:
                continue

            symbol = str(symbol)

            if not symbol.startswith("PF_"):
                continue

            if "USD" not in symbol:
                continue

            tradeable = item.get(
                "tradeable",
                True
            )

            if tradeable is False:
                continue

            symbols.append(symbol)

        symbols = list(dict.fromkeys(symbols))

        return symbols[:TARGET_ASSETS]

    except Exception as e:

        DIAG["api_errors"].append(
            f"Instruments: {str(e)[:180]}"
        )

        return []


def normalize_resolution(interval):

    if interval == "4h":
        return "4h"

    if interval == "5m":
        return "5m"

    if interval == "1h":
        return "1h"

    if interval == "30m":
        return "30m"

    return interval


def get_candles(symbol, interval, count):

    resolution = normalize_resolution(interval)

    url = (
        f"{KRAKEN_CHART_URL}/trade/"
        f"{symbol}/{resolution}"
    )

    params = {
        "count": int(count)
    }

    try:

        r = requests.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        if not r.ok:

            DIAG["api_errors"].append(
                f"{symbol} {interval} HTTP "
                f"{r.status_code}"
            )

            return None

        data = r.json()

        candles = data.get(
            "candles",
            []
        )

        if not candles:
            return pd.DataFrame()

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

                rows.append(
                    [
                        ts,
                        op,
                        hi,
                        lo,
                        cl,
                        volume
                    ]
                )

            elif isinstance(c, (list, tuple)):

                if len(c) < 5:
                    continue

                ts = c[0]
                op = c[1]
                hi = c[2]
                lo = c[3]
                cl = c[4]
                volume = (
                    c[5]
                    if len(c) > 5
                    else 0
                )

                rows.append(
                    [
                        ts,
                        op,
                        hi,
                        lo,
                        cl,
                        volume
                    ]
                )

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(
            rows,
            columns=[
                "time",
                "open",
                "high",
                "low",
                "close",
                "volume"
            ]
        )

        for col in [
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]:
            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        df["time"] = pd.to_numeric(
            df["time"],
            errors="coerce"
        )

        df = df.dropna(
            subset=[
                "time",
                "open",
                "high",
                "low",
                "close"
            ]
        )

        if df.empty:
            return df

        # Kraken timestamps can be seconds or milliseconds.
        if df["time"].max() > 10_000_000_000:
            df["time"] = (
                df["time"] / 1000.0
            )

        df["time"] = df["time"].astype(float)

        df = (
            df
            .sort_values("time")
            .drop_duplicates("time")
            .reset_index(drop=True)
        )

        return df

    except Exception as e:

        DIAG["api_errors"].append(
            f"{symbol} {interval}: "
            f"{str(e)[:180]}"
        )

        return None


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(df):

    if df is None or df.empty:
        return [], []

    highs = []
    lows = []

    n = len(df)

    if n < (
        PIVOT_LEFT
        + PIVOT_RIGHT
        + 1
    ):
        return highs, lows

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT
    ):

        current_high = float(
            df.iloc[i]["high"]
        )

        current_low = float(
            df.iloc[i]["low"]
        )

        left_highs = [
            float(
                df.iloc[j]["high"]
            )
            for j in range(
                i - PIVOT_LEFT,
                i
            )
        ]

        right_highs = [
            float(
                df.iloc[j]["high"]
            )
            for j in range(
                i + 1,
                i + PIVOT_RIGHT + 1
            )
        ]

        left_lows = [
            float(
                df.iloc[j]["low"]
            )
            for j in range(
                i - PIVOT_LEFT,
                i
            )
        ]

        right_lows = [
            float(
                df.iloc[j]["low"]
            )
            for j in range(
                i + 1,
                i + PIVOT_RIGHT + 1
            )
        ]

        is_high = (
            all(
                current_high > x
                for x in left_highs
            )
            and
            all(
                current_high >= x
                for x in right_highs
            )
        )

        is_low = (
            all(
                current_low < x
                for x in left_lows
            )
            and
            all(
                current_low <= x
                for x in right_lows
            )
        )

        if is_high:

            highs.append({
                "type": "H",
                "index": i,
                "time": float(
                    df.iloc[i]["time"]
                ),
                "price": current_high,
            })

        if is_low:

            lows.append({
                "type": "L",
                "index": i,
                "time": float(
                    df.iloc[i]["time"]
                ),
                "price": current_low,
            })

    return highs, lows


def build_ordered_pivots(highs, lows):

    pivots = (
        highs + lows
    )

    pivots.sort(
        key=lambda x: (
            x["time"],
            x["index"]
        )
    )

    result = []

    for p in pivots:

        if not result:

            result.append(p)
            continue

        last = result[-1]

        if p["type"] != last["type"]:

            result.append(p)

        else:

            # Same type twice in a row.
            # Keep the more extreme pivot.
            if p["type"] == "H":

                if p["price"] >= last["price"]:
                    result[-1] = p

            else:

                if p["price"] <= last["price"]:
                    result[-1] = p

    return result


# ============================================================
# HOOK DETECTION
# ============================================================

def detect_hooks(
    df,
    interval_minutes
):

    """
    IMPORTANT:

    Confirmation is calculated according to the actual
    timeframe.

    H4:
        240 minutes * PIVOT_RIGHT

    M5:
        5 minutes * PIVOT_RIGHT
    """

    if df is None or df.empty:
        return []

    highs, lows = find_pivots(df)

    ordered = build_ordered_pivots(
        highs,
        lows
    )

    hooks = []

    confirmation_seconds = (
        interval_minutes
        * 60
        * PIVOT_RIGHT
    )

    if len(ordered) < 6:
        return hooks

    # --------------------------------------------------------
    # Scan every 6-point alternating sequence.
    # --------------------------------------------------------

    for i in range(
        0,
        len(ordered) - 5
    ):

        p = ordered[i:i + 6]

        # ----------------------------------------------------
        # SHORT / POSITIVE
        #
        # START(L)
        # H1(H)
        # L1(L)
        # H2(H)
        # L2(L)
        # H3(H)
        # ----------------------------------------------------

        if (
            p[0]["type"] == "L"
            and p[1]["type"] == "H"
            and p[2]["type"] == "L"
            and p[3]["type"] == "H"
            and p[4]["type"] == "L"
            and p[5]["type"] == "H"
        ):

            start = p[0]
            h1 = p[1]
            l1 = p[2]
            h2 = p[3]
            l2 = p[4]
            h3 = p[5]

            if not (
                h2["price"] > h1["price"]
            ):
                continue

            if not (
                l2["price"] < l1["price"]
            ):
                continue

            if not (
                h3["price"] > h2["price"]
            ):
                continue

            hook_range = (
                h3["price"]
                - start["price"]
            )

            if hook_range <= 0:
                continue

            range_pct = (
                hook_range
                / start["price"]
                * 100.0
            )

            confirmation_time = (
                h3["time"]
                + confirmation_seconds
            )

            tp = (
                h3["price"]
                - NDS_RETRACE
                * hook_range
            )

            hooks.append({
                "direction": "SHORT",
                "start": start,
                "h1": h1,
                "l1": l1,
                "h2": h2,
                "l2": l2,
                "h3": h3,
                "final": h3,
                "entry": h3["price"],
                "tp": tp,
                "range_pct": range_pct,
                "confirmation_time": confirmation_time,
                "created_time": h3["time"],
            })

        # ----------------------------------------------------
        # LONG / NEGATIVE
        #
        # START(H)
        # L1(L)
        # H1(H)
        # L2(L)
        # H2(H)
        # L3(L)
        # ----------------------------------------------------

        if (
            p[0]["type"] == "H"
            and p[1]["type"] == "L"
            and p[2]["type"] == "H"
            and p[3]["type"] == "L"
            and p[4]["type"] == "H"
            and p[5]["type"] == "L"
        ):

            start = p[0]
            l1 = p[1]
            h1 = p[2]
            l2 = p[3]
            h2 = p[4]
            l3 = p[5]

            if not (
                l2["price"] < l1["price"]
            ):
                continue

            if not (
                h2["price"] > h1["price"]
            ):
                continue

            if not (
                l3["price"] < l2["price"]
            ):
                continue

            hook_range = (
                start["price"]
                - l3["price"]
            )

            if hook_range <= 0:
                continue

            range_pct = (
                hook_range
                / start["price"]
                * 100.0
            )

            confirmation_time = (
                l3["time"]
                + confirmation_seconds
            )

            tp = (
                l3["price"]
                + NDS_RETRACE
                * hook_range
            )

            hooks.append({
                "direction": "LONG",
                "start": start,
                "l1": l1,
                "h1": h1,
                "l2": l2,
                "h2": h2,
                "l3": l3,
                "final": l3,
                "entry": l3["price"],
                "tp": tp,
                "range_pct": range_pct,
                "confirmation_time": confirmation_time,
                "created_time": l3["time"],
            })

    return hooks


# ============================================================
# HOOK HELPERS
# ============================================================

def hook_id(symbol, hook):

    final = hook["final"]

    return (
        f"{symbol}|"
        f"{hook['direction']}|"
        f"{int(final['time'])}|"
        f"{final['price']:.12f}"
    )


def hook_is_confirmed(
    hook,
    now_ts=None
):

    if now_ts is None:
        now_ts = utc_now_ts()

    return (
        hook["confirmation_time"]
        <= now_ts
    )


def hook_is_recent(
    hook,
    now_ts=None
):

    if now_ts is None:
        now_ts = utc_now_ts()

    age = (
        now_ts
        - hook["confirmation_time"]
    )

    return (
        age >= 0
        and age <= MAX_HOOK_AGE_SECONDS
    )


def hook_range_valid(
    hook,
    minimum_pct=None
):

    if minimum_pct is None:
        minimum_pct = M5_MIN_HOOK_RANGE_PCT

    return (
        hook["range_pct"]
        >= minimum_pct
    )


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

    if df is None:

        DIAG["h4_data_error"] += 1

        return None

    if df.empty:

        DIAG["h4_empty"] += 1

        return None

    if len(df) < 50:

        DIAG["h4_short"] += 1

        return None

    DIAG["h4_data_ok"] += 1

    highs, lows = find_pivots(df)

    DIAG["h4_pivots"] += (
        len(highs)
        + len(lows)
    )

    hooks = detect_hooks(
        df,
        H4_INTERVAL_MINUTES
    )

    DIAG["h4_hooks"] += len(hooks)

    now_ts = utc_now_ts()

    valid = []

    for hook in hooks:

        if not hook_is_confirmed(
            hook,
            now_ts
        ):
            continue

        if not hook_is_recent(
            hook,
            now_ts
        ):
            continue

        valid.append(hook)

    DIAG["h4_valid_hooks"] += len(valid)

    if not valid:
        return None

    valid.sort(
        key=lambda x: (
            x["confirmation_time"],
            x["final"]["time"]
        ),
        reverse=True
    )

    latest = valid[0]

    direction = latest["direction"]

    H4_DIRECTION[symbol] = {
        "direction": direction,
        "hook": latest,
    }

    if direction == "SHORT":
        DIAG["h4_short_direction"] += 1
    else:
        DIAG["h4_long_direction"] += 1

    return direction


# ============================================================
# PREVIOUS VALID SL
# ============================================================

def calculate_sl(
    m5_df,
    hook
):

    highs, lows = find_pivots(
        m5_df
    )

    final_time = (
        hook["final"]["time"]
    )

    if hook["direction"] == "SHORT":

        candidates = [
            x for x in highs
            if x["time"] < final_time
        ]

        if not candidates:
            return None

        candidates.sort(
            key=lambda x: x["time"],
            reverse=True
        )

        return candidates[0]["price"]

    else:

        candidates = [
            x for x in lows
            if x["time"] < final_time
        ]

        if not candidates:
            return None

        candidates.sort(
            key=lambda x: x["time"],
            reverse=True
        )

        return candidates[0]["price"]


# ============================================================
# TP TOUCH TEST
# ============================================================

def tp_already_touched(
    df,
    hook
):

    if df is None or df.empty:
        return False

    final_time = (
        hook["final"]["time"]
    )

    future = df[
        df["time"] > final_time
    ]

    if future.empty:
        return False

    tp = float(
        hook["tp"]
    )

    if hook["direction"] == "SHORT":

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
# M5 HOOK SELECTION
# ============================================================

def select_m5_hook(
    symbol,
    direction,
    df
):

    if df is None or df.empty:
        return None

    highs, lows = find_pivots(df)

    DIAG["m5_pivots"] += (
        len(highs)
        + len(lows)
    )

    hooks = detect_hooks(
        df,
        M5_INTERVAL_MINUTES
    )

    DIAG["m5_hooks"] += len(hooks)

    now_ts = utc_now_ts()

    candidates = []

    for hook in hooks:

        if hook["direction"] != direction:
            continue

        if not hook_is_confirmed(
            hook,
            now_ts
        ):
            continue

        if not hook_is_recent(
            hook,
            now_ts
        ):
            continue

        if not hook_range_valid(
            hook,
            M5_MIN_HOOK_RANGE_PCT
        ):
            continue

        if tp_already_touched(
            df,
            hook
        ):
            continue

        candidates.append(hook)

    DIAG["m5_valid_hooks"] += len(
        candidates
    )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (
            x["confirmation_time"],
            x["final"]["time"]
        ),
        reverse=True
    )

    return candidates[0]


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

    os.makedirs(
        CHART_DIR,
        exist_ok=True
    )

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS hooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hook_key TEXT UNIQUE,
            symbol TEXT,
            direction TEXT,
            start_price REAL,
            h1_price REAL,
            l1_price REAL,
            h2_price REAL,
            l2_price REAL,
            final_price REAL,
            entry REAL,
            tp REAL,
            sl REAL,
            range_pct REAL,
            confirmation_time INTEGER,
            created_time INTEGER,
            detected_time INTEGER,
            chart_path TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hook_key TEXT UNIQUE,
            symbol TEXT,
            direction TEXT,
            entry REAL,
            sl REAL,
            tp REAL,
            opened_at INTEGER,
            closed_at INTEGER,
            close_price REAL,
            status TEXT,
            pnl_pct REAL,
            pnl_price REAL,
            chart_path TEXT
        )
    """)

    conn.commit()

    conn.close()


def hook_exists(
    hook_key
):

    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
        FROM hooks
        WHERE hook_key = ?
        LIMIT 1
        """,
        (hook_key,)
    ).fetchone()

    conn.close()

    return row is not None


def trade_exists(
    hook_key
):

    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
        FROM trades
        WHERE hook_key = ?
        LIMIT 1
        """,
        (hook_key,)
    ).fetchone()

    conn.close()

    return row is not None


def open_trade_count():

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

    return int(
        row["c"]
    )


def save_hook(
    symbol,
    hook,
    sl,
    chart_path=None
):

    key = hook_id(
        symbol,
        hook
    )

    conn = db_connect()

    conn.execute(
        """
        INSERT OR IGNORE INTO hooks (
            hook_key,
            symbol,
            direction,
            start_price,
            h1_price,
            l1_price,
            h2_price,
            l2_price,
            final_price,
            entry,
            tp,
            sl,
            range_pct,
            confirmation_time,
            created_time,
            detected_time,
            chart_path
        )
        VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            key,
            symbol,
            hook["direction"],
            hook["start"]["price"],
            hook.get("h1", {}).get("price"),
            hook.get("l1", {}).get("price"),
            hook.get("h2", {}).get("price"),
            hook.get("l2", {}).get("price"),
            hook["final"]["price"],
            hook["entry"],
            hook["tp"],
            sl,
            hook["range_pct"],
            int(
                hook["confirmation_time"]
            ),
            int(
                hook["created_time"]
            ),
            utc_now_ts(),
            chart_path
        )
    )

    conn.commit()
    conn.close()

    return key


def save_trade(
    hook_key,
    symbol,
    direction,
    entry,
    sl,
    tp,
    chart_path=None
):

    conn = db_connect()

    conn.execute(
        """
        INSERT OR IGNORE INTO trades (
            hook_key,
            symbol,
            direction,
            entry,
            sl,
            tp,
            opened_at,
            status,
            chart_path
        )
        VALUES (
            ?,?,?,?,?,?,?,?,?
        )
        """,
        (
            hook_key,
            symbol,
            direction,
            entry,
            sl,
            tp,
            utc_now_ts(),
            "OPEN",
            chart_path
        )
    )

    conn.commit()
    conn.close()


# ============================================================
# CHART
# ============================================================

def create_hook_chart(
    symbol,
    df,
    hook,
    sl,
    path_prefix="signal"
):

    try:

        chart_df = df.tail(
            CHART_CANDLES
        ).copy()

        if chart_df.empty:
            return None

        fig, ax = plt.subplots(
            figsize=(14, 8)
        )

        x = pd.to_datetime(
            chart_df["time"],
            unit="s",
            utc=True
        )

        ax.plot(
            x,
            chart_df["close"],
            linewidth=1.0,
            label="M5 Close"
        )

        points = []

        if hook["direction"] == "SHORT":

            points = [
                hook["start"],
                hook["h1"],
                hook["l1"],
                hook["h2"],
                hook["l2"],
                hook["h3"],
            ]

        else:

            points = [
                hook["start"],
                hook["l1"],
                hook["h1"],
                hook["l2"],
                hook["h2"],
                hook["l3"],
            ]

        px = [
            datetime.fromtimestamp(
                p["time"],
                tz=timezone.utc
            )
            for p in points
        ]

        py = [
            p["price"]
            for p in points
        ]

        ax.plot(
            px,
            py,
            marker="o",
            linewidth=2.0,
            label="NDS Hook"
        )

        if hook["direction"] == "SHORT":

            labels = [
                "START",
                "H1",
                "L1",
                "H2",
                "L2",
                "H3",
            ]

        else:

            labels = [
                "START",
                "L1",
                "H1",
                "L2",
                "H2",
                "L3",
            ]

        for p, label in zip(
            points,
            labels
        ):

            dt = datetime.fromtimestamp(
                p["time"],
                tz=timezone.utc
            )

            ax.annotate(
                f"{label}\n{fmt_price(p['price'])}",
                (
                    dt,
                    p["price"]
                ),
                xytext=(0, 10),
                textcoords="offset points",
                ha="center",
                fontsize=9
            )

        entry = float(
            hook["entry"]
        )

        tp = float(
            hook["tp"]
        )

        sl_value = (
            float(sl)
            if sl is not None
            else None
        )

        start_x = x.iloc[0]
        end_x = x.iloc[-1]

        ax.hlines(
            entry,
            start_x,
            end_x,
            linestyles="--",
            linewidth=1.4,
            label=(
                f"ENTRY {fmt_price(entry)}"
            )
        )

        ax.hlines(
            tp,
            start_x,
            end_x,
            linestyles="--",
            linewidth=1.6,
            label=(
                f"TP 86.4% "
                f"{fmt_price(tp)}"
            )
        )

        if sl_value is not None:

            ax.hlines(
                sl_value,
                start_x,
                end_x,
                linestyles="--",
                linewidth=1.4,
                label=(
                    f"SL {fmt_price(sl_value)}"
                )
            )

        confirmation_dt = (
            datetime.fromtimestamp(
                hook["confirmation_time"],
                tz=timezone.utc
            )
        )

        ax.axvline(
            confirmation_dt,
            linestyle=":",
            linewidth=1.2,
            label="CONFIRMED"
        )

        ax.set_title(
            (
                f"NDS H4 → M5 | "
                f"{symbol} | "
                f"{hook['direction']}"
            )
        )

        ax.set_xlabel(
            "Time UTC"
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

        os.makedirs(
            CHART_DIR,
            exist_ok=True
        )

        safe_symbol = (
            symbol
            .replace("/", "_")
            .replace(":", "_")
        )

        filename = (
            f"{path_prefix}_"
            f"{safe_symbol}_"
            f"{hook['direction']}_"
            f"{int(hook['final']['time'])}.png"
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

    except Exception as e:

        DIAG["api_errors"].append(
            f"Chart {symbol}: "
            f"{str(e)[:180]}"
        )

        try:
            plt.close("all")
        except Exception:
            pass

        return None


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def build_signal_message(
    symbol,
    hook,
    sl
):

    direction = hook["direction"]

    if direction == "SHORT":

        emoji = "🔴"

    else:

        emoji = "🟢"

    entry = float(
        hook["entry"]
    )

    tp = float(
        hook["tp"]
    )

    if sl is not None:

        sl = float(sl)

        if direction == "SHORT":

            risk_pct = (
                (sl - entry)
                / entry
                * 100.0
            )

            reward_pct = (
                (entry - tp)
                / entry
                * 100.0
            )

        else:

            risk_pct = (
                (entry - sl)
                / entry
                * 100.0
            )

            reward_pct = (
                (tp - entry)
                / entry
                * 100.0
            )

    else:

        risk_pct = 0.0
        reward_pct = 0.0

    return (
        f"{emoji} <b>NDS "
        f"{direction}</b>\n"
        f"<b>{symbol}</b>\n\n"

        f"Entry: <b>"
        f"{fmt_price(entry)}"
        f"</b>\n"

        f"SL: <b>"
        f"{fmt_price(sl)}"
        f"</b> "
        f"({risk_pct:.2f}%)\n"

        f"TP 86.4%: <b>"
        f"{fmt_price(tp)}"
        f"</b> "
        f"({reward_pct:.2f}%)\n\n"

        f"Hook Range: "
        f"{hook['range_pct']:.2f}%\n"

        f"Confirmed: "
        f"{fmt_ts(hook['confirmation_time'])}\n\n"

        f"<b>H4 direction confirmed</b>\n"
        f"<b>Paper Trading</b>"
    )


# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(symbol):

    direction = get_h4_direction(
        symbol
    )

    if direction is None:

        # IMPORTANT:
        # Count filtering only here.
        # This prevents the old 100 -> 200 problem.
        DIAG["h4_filtered"] += 1

        return

    DIAG["m5_requests"] += 1

    m5_df = get_candles(
        symbol,
        M5_INTERVAL,
        M5_CANDLES
    )

    if m5_df is None:

        DIAG["m5_data_error"] += 1

        return

    if m5_df.empty:

        DIAG["m5_empty"] += 1

        return

    if len(m5_df) < 100:

        DIAG["m5_short"] += 1

        return

    DIAG["m5_data_ok"] += 1

    hook = select_m5_hook(
        symbol,
        direction,
        m5_df
    )

    if hook is None:
        return

    sl = calculate_sl(
        m5_df,
        hook
    )

    if sl is None:
        return

    # --------------------------------------------------------
    # Basic price sanity.
    # --------------------------------------------------------

    entry = float(
        hook["entry"]
    )

    tp = float(
        hook["tp"]
    )

    sl = float(sl)

    if direction == "SHORT":

        if not (
            sl > entry > tp
        ):
            return

    else:

        if not (
            sl < entry < tp
        ):
            return

    key = hook_id(
        symbol,
        hook
    )

    if hook_exists(key):

        DIAG["duplicate_signals"] += 1

        return

    if trade_exists(key):

        DIAG["duplicate_signals"] += 1

        return

    if open_trade_count() >= MAX_OPEN_TRADES:

        DIAG["max_open"] += 1

        return

    chart_path = create_hook_chart(
        symbol,
        m5_df,
        hook,
        sl,
        "signal"
    )

    save_hook(
        symbol,
        hook,
        sl,
        chart_path
    )

    save_trade(
        key,
        symbol,
        direction,
        entry,
        sl,
        tp,
        chart_path
    )

    DIAG["signals"] += 1

    message = build_signal_message(
        symbol,
        hook,
        sl
    )

    telegram_send(message)

    if chart_path:

        telegram_send_photo(
            chart_path,
            (
                f"NDS {direction} | "
                f"{symbol}"
            )
        )


# ============================================================
# OPEN TRADE MONITOR
# ============================================================

def get_open_trades():

    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY opened_at ASC
        """
    ).fetchall()

    conn.close()

    return rows


def close_trade(
    trade,
    close_price,
    pnl_pct,
    pnl_price,
    reason
):

    conn = db_connect()

    conn.execute(
        """
        UPDATE trades
        SET
            closed_at = ?,
            close_price = ?,
            status = ?,
            pnl_pct = ?,
            pnl_price = ?
        WHERE id = ?
        """,
        (
            utc_now_ts(),
            close_price,
            reason,
            pnl_pct,
            pnl_price,
            trade["id"]
        )
    )

    conn.commit()
    conn.close()


def monitor_open_trades():

    trades = get_open_trades()

    if not trades:
        return

    for trade in trades:

        symbol = trade["symbol"]

        direction = trade["direction"]

        entry = float(
            trade["entry"]
        )

        sl = float(
            trade["sl"]
        )

        tp = float(
            trade["tp"]
        )

        try:

            df = get_candles(
                symbol,
                M5_INTERVAL,
                30
            )

            if (
                df is None
                or df.empty
            ):
                continue

            # ------------------------------------------------
            # Only candles after opening.
            # ------------------------------------------------

            future = df[
                df["time"]
                >= trade["opened_at"]
            ]

            if future.empty:
                continue

            hit_sl = False
            hit_tp = False
            close_price = None
            reason = None

            # ------------------------------------------------
            # Conservative intrabar handling:
            #
            # If both SL and TP are touched in the same candle,
            # SL is assumed first.
            # ------------------------------------------------

            for _, candle in future.iterrows():

                high = float(
                    candle["high"]
                )

                low = float(
                    candle["low"]
                )

                if direction == "SHORT":

                    candle_sl = (
                        high >= sl
                    )

                    candle_tp = (
                        low <= tp
                    )

                    if (
                        candle_sl
                        and candle_tp
                    ):

                        hit_sl = True
                        close_price = sl
                        reason = "SL"

                        break

                    if candle_sl:

                        hit_sl = True
                        close_price = sl
                        reason = "SL"

                        break

                    if candle_tp:

                        hit_tp = True
                        close_price = tp
                        reason = "TP"

                        break

                else:

                    candle_sl = (
                        low <= sl
                    )

                    candle_tp = (
                        high >= tp
                    )

                    if (
                        candle_sl
                        and candle_tp
                    ):

                        hit_sl = True
                        close_price = sl
                        reason = "SL"

                        break

                    if candle_sl:

                        hit_sl = True
                        close_price = sl
                        reason = "SL"

                        break

                    if candle_tp:

                        hit_tp = True
                        close_price = tp
                        reason = "TP"

                        break

            if not (
                hit_sl
                or hit_tp
            ):
                continue

            if direction == "SHORT":

                pnl_price = (
                    entry
                    - close_price
                )

                pnl_pct = (
                    pnl_price
                    / entry
                    * 100.0
                )

            else:

                pnl_price = (
                    close_price
                    - entry
                )

                pnl_pct = (
                    pnl_price
                    / entry
                    * 100.0
                )

            close_trade(
                trade,
                close_price,
                pnl_pct,
                pnl_price,
                reason
            )

            if pnl_pct >= 0:

                emoji = "✅"

            else:

                emoji = "❌"

            message = (
                f"{emoji} <b>NDS "
                f"{direction} CLOSED</b>\n"
                f"<b>{symbol}</b>\n\n"

                f"Entry: "
                f"<b>{fmt_price(entry)}</b>\n"

                f"Close: "
                f"<b>{fmt_price(close_price)}</b>\n"

                f"Result: <b>"
                f"{reason}"
                f"</b>\n"

                f"PnL: <b>"
                f"{pnl_pct:+.2f}%"
                f"</b>\n"
            )

            telegram_send(
                message
            )

        except Exception as e:

            DIAG["api_errors"].append(
                f"Monitor {symbol}: "
                f"{str(e)[:180]}"
            )


# ============================================================
# PERFORMANCE
# ============================================================

def performance_summary():

    conn = db_connect()

    total = conn.execute(
        """
        SELECT COUNT(*)
        AS c
        FROM trades
        WHERE status != 'OPEN'
        """
    ).fetchone()["c"]

    wins = conn.execute(
        """
        SELECT COUNT(*)
        AS c
        FROM trades
        WHERE status = 'TP'
        """
    ).fetchone()["c"]

    losses = conn.execute(
        """
        SELECT COUNT(*)
        AS c
        FROM trades
        WHERE status = 'SL'
        """
    ).fetchone()["c"]

    pnl = conn.execute(
        """
        SELECT COALESCE(
            SUM(pnl_pct),
            0
        ) AS p
        FROM trades
        WHERE status != 'OPEN'
        """
    ).fetchone()["p"]

    conn.close()

    return {
        "total": int(total or 0),
        "wins": int(wins or 0),
        "losses": int(losses or 0),
        "pnl": float(pnl or 0),
    }


# ============================================================
# DIAGNOSTIC REPORT
# ============================================================

def diagnostic_text():

    runtime = (
        time.time()
        - START_TIME
    )

    perf = performance_summary()

    open_count = (
        open_trade_count()
    )

    lines = []

    lines.append(
        "🔎 <b>NDS H4 → M5 DIAGNOSTIC</b>"
    )

    lines.append(
        f"Version: <b>{VERSION}</b>"
    )

    lines.append(
        f"Time: {utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )

    lines.append(
        f"Runtime: <b>{runtime:.1f}s</b>"
    )

    lines.append("")

    lines.append(
        "━━━ <b>ASSETS</b> ━━━"
    )

    lines.append(
        f"Scanned: <b>"
        f"{DIAG['assets_scanned']}"
        f"</b>"
    )

    lines.append("")

    lines.append(
        "━━━ <b>H4</b> ━━━"
    )

    lines.append(
        f"Requests: {DIAG['h4_requests']}"
    )

    lines.append(
        f"Data OK: {DIAG['h4_data_ok']}"
    )

    lines.append(
        f"Data Error: {DIAG['h4_data_error']}"
    )

    lines.append(
        f"Empty: {DIAG['h4_empty']}"
    )

    lines.append(
        f"Short: {DIAG['h4_short']}"
    )

    lines.append(
        f"Pivot Points: {DIAG['h4_pivots']}"
    )

    lines.append(
        f"Hooks: {DIAG['h4_hooks']}"
    )

    lines.append(
        f"Valid Hooks: {DIAG['h4_valid_hooks']}"
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

    lines.append(
        "━━━ <b>M5</b> ━━━"
    )

    lines.append(
        f"Requests: {DIAG['m5_requests']}"
    )

    lines.append(
        f"Data OK: {DIAG['m5_data_ok']}"
    )

    lines.append(
        f"Data Error: {DIAG['m5_data_error']}"
    )

    lines.append(
        f"Empty: {DIAG['m5_empty']}"
    )

    lines.append(
        f"Short: {DIAG['m5_short']}"
    )

    lines.append(
        f"Pivot Points: {DIAG['m5_pivots']}"
    )

    lines.append(
        f"Hooks: {DIAG['m5_hooks']}"
    )

    lines.append(
        f"Valid Hooks: "
        f"{DIAG['m5_valid_hooks']}"
    )

    lines.append("")

    lines.append(
        "━━━ <b>SIGNALS</b> ━━━"
    )

    lines.append(
        f"Signals: {DIAG['signals']}"
    )

    lines.append(
        f"Duplicates: "
        f"{DIAG['duplicate_signals']}"
    )

    lines.append(
        f"Max Open Limit: "
        f"{DIAG['max_open']}"
    )

    lines.append(
        f"Open Trades: <b>"
        f"{open_count}"
        f"</b>"
    )

    lines.append("")

    lines.append(
        "━━━ <b>PAPER PERFORMANCE</b> ━━━"
    )

    lines.append(
        f"Closed Trades: "
        f"{perf['total']}"
    )

    lines.append(
        f"TP: {perf['wins']}"
    )

    lines.append(
        f"SL: {perf['losses']}"
    )

    lines.append(
        f"PnL: <b>"
        f"{perf['pnl']:+.2f}%"
        f"</b>"
    )

    if DIAG["api_errors"]:

        lines.append("")

        lines.append(
            "━━━ <b>ERRORS</b> ━━━"
        )

        for err in DIAG["api_errors"][
            -5:
        ]:

            lines.append(
                f"• {err}"
            )

    lines.append("")

    lines.append(
        "<b>PAPER TRADING ONLY</b>"
    )

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():

    try:

        init_db()

        print(
            f"NDS H4 -> M5 "
            f"Scanner {VERSION}"
        )

        print(
            "PAPER TRADING ONLY"
        )

        print(
            "123F DISABLED"
        )

        print(
            "H4 confirmation = "
            f"{H4_INTERVAL_MINUTES}m "
            f"x {PIVOT_RIGHT}"
        )

        print(
            "M5 confirmation = "
            f"{M5_INTERVAL_MINUTES}m "
            f"x {PIVOT_RIGHT}"
        )

        symbols = (
            get_futures_instruments()
        )

        if not symbols:

            report = (
                "❌ <b>NDS Scanner</b>\n\n"
                "No futures instruments found."
            )

            print(report)

            telegram_send(report)

            return

        DIAG["assets_scanned"] = (
            len(symbols)
        )

        # ----------------------------------------------------
        # First monitor existing paper trades.
        # ----------------------------------------------------

        monitor_open_trades()

        # ----------------------------------------------------
        # Scan all assets.
        # ----------------------------------------------------

        for symbol in symbols:

            try:

                process_symbol(
                    symbol
                )

            except Exception as e:

                DIAG["api_errors"].append(
                    f"Process {symbol}: "
                    f"{str(e)[:180]}"
                )

                traceback.print_exc()

            time.sleep(
                SCAN_SLEEP_SECONDS
            )

        # ----------------------------------------------------
        # Monitor again after scan.
        # ----------------------------------------------------

        monitor_open_trades()

        # ----------------------------------------------------
        # Diagnostic.
        # ----------------------------------------------------

        report = diagnostic_text()

        print("")
        print(report)
        print("")

        telegram_send(
            report
        )

    except Exception as e:

        traceback.print_exc()

        error_message = (
            f"❌ <b>NDS Scanner Error</b>\n\n"
            f"{type(e).__name__}: "
            f"{str(e)[:500]}"
        )

        telegram_send(
            error_message
        )


if __name__ == "__main__":
    main()
