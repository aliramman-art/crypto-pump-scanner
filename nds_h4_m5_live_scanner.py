# ============================================================
# NDS H4 -> M5 LIVE SCANNER
# VERSION 5.3.2
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
# IMPORTANT:
#   86.4% is NOT the trade start.
#   Entry is only at confirmed H3/L3.
#
# SELECTION:
#   Among valid M5 hooks matching H4 direction,
#   select the hook with the greatest percentage
#   distance from ENTRY to TP 86.4%.
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

VERSION = "5.3.2"

DB_FILE = "nds_h4_m5_live_v51.db"
CHART_DIR = "nds_h4_m5_charts"

KRAKEN_BASE = "https://futures.kraken.com/api/charts/v1/trade"

# Kraken Futures chart resolutions
H4_TIMEFRAME = "4h"
M5_TIMEFRAME = "5m"

H4_COUNT = 320
M5_COUNT = 500

CHART_CANDLES = 180

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

H4_MIN_SWING_PCT = 0.20
M5_MIN_SWING_PCT = 0.07

# Kept for diagnostic information.
# STALE NO LONGER BLOCKS THE SELECTED HOOK.
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
# DIAGNOSTICS
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

    conn.execute(
        """
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

            h1_time INTEGER,
            h1_price REAL,

            l1_time INTEGER,
            l1_price REAL,

            h2_time INTEGER,
            h2_price REAL,

            l2_time INTEGER,
            l2_price REAL,

            h3_time INTEGER,
            h3_price REAL,

            l3_time INTEGER,
            l3_price REAL,

            created_at INTEGER NOT NULL,

            UNIQUE(
                symbol,
                direction,
                start_time,
                entry_time
            )
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,

            entry_time INTEGER NOT NULL,
            entry_price REAL NOT NULL,

            tp REAL NOT NULL,
            sl REAL NOT NULL,

            exit_time INTEGER,
            exit_price REAL,

            status TEXT NOT NULL,

            pnl_pct REAL,

            created_at INTEGER NOT NULL
        )
        """
    )

    conn.commit()
    conn.close()


# ============================================================
# TIME
# ============================================================

def now_ts():
    return int(time.time())


def interval_minutes(timeframe):

    mapping = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "4h": 240,
        "12h": 720,
        "1d": 1440,
        "1w": 10080,
    }

    return mapping.get(timeframe, 1)


def hook_confirmation_time(
    hook,
    timeframe,
):

    return int(
        hook["entry_time"]
        + (
            PIVOT_RIGHT
            * interval_minutes(timeframe)
            * 60
        )
    )


def hook_age_seconds(hook):

    confirmation_time = hook.get(
        "confirmation_time",
        hook["entry_time"],
    )

    return max(
        0,
        now_ts() - int(confirmation_time),
    )


def ts_to_dt(ts):

    return datetime.fromtimestamp(
        int(ts),
        tz=timezone.utc,
    )


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
            timeout=REQUEST_TIMEOUT,
        )

        return r.ok

    except Exception:

        return False


def telegram_photo(
    photo_path,
    caption="",
):

    if not telegram_enabled():
        return False

    if not os.path.exists(photo_path):
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    try:

        with open(
            photo_path,
            "rb",
        ) as f:

            files = {
                "photo": f
            }

            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
                "parse_mode": "HTML",
            }

            r = requests.post(
                url,
                data=data,
                files=files,
                timeout=REQUEST_TIMEOUT,
            )

        return r.ok

    except Exception:

        return False


# ============================================================
# KRAKEN CANDLES
# ============================================================

def fetch_candles(
    symbol,
    timeframe,
    count,
):

    if timeframe == H4_TIMEFRAME:

        DIAG["h4_requests"] += 1
        diag_key = "h4"

    elif timeframe == M5_TIMEFRAME:

        DIAG["m5_requests"] += 1
        diag_key = "m5"

    else:

        diag_key = None

    url = (
        f"{KRAKEN_BASE}/"
        f"{symbol}/"
        f"{timeframe}"
    )

    try:

        r = requests.get(
            url,
            params={
                "count": int(count)
            },
            headers={
                "Accept": "application/json"
            },
            timeout=REQUEST_TIMEOUT,
        )

        if not r.ok:

            msg = (
                f"HTTP {r.status_code}: "
                f"{r.text[:500]}"
            )

            if diag_key == "h4":

                DIAG["h4_errors"] += 1
                DIAG["last_h4_error"] = msg

            elif diag_key == "m5":

                DIAG["m5_errors"] += 1
                DIAG["last_m5_error"] = msg

            return None

        try:

            data = r.json()

        except Exception:

            msg = (
                "Invalid JSON: "
                f"{r.text[:500]}"
            )

            if diag_key == "h4":

                DIAG["h4_errors"] += 1
                DIAG["last_h4_error"] = msg

            elif diag_key == "m5":

                DIAG["m5_errors"] += 1
                DIAG["last_m5_error"] = msg

            return None

        rows = data.get("candles")

        if rows is None:

            rows = data.get("data")

        if (
            rows is None
            and isinstance(
                data.get("result"),
                dict,
            )
        ):

            rows = data["result"].get(
                "candles"
            )

        if not rows:

            msg = (
                "No candles in response: "
                f"{str(data)[:500]}"
            )

            if diag_key == "h4":

                DIAG["h4_errors"] += 1
                DIAG["last_h4_error"] = msg

            elif diag_key == "m5":

                DIAG["m5_errors"] += 1
                DIAG["last_m5_error"] = msg

            return None

        parsed = []

        for row in rows:

            try:

                if isinstance(
                    row,
                    dict,
                ):

                    ts = (
                        row.get("time")
                        or row.get("timestamp")
                        or row.get("ts")
                    )

                    o = row.get("open")
                    h = row.get("high")
                    l = row.get("low")
                    c = row.get("close")

                else:

                    if len(row) < 5:
                        continue

                    ts = row[0]
                    o = row[1]
                    h = row[2]
                    l = row[3]
                    c = row[4]

                if (
                    ts is None
                    or o is None
                    or h is None
                    or l is None
                    or c is None
                ):

                    continue

                ts = float(ts)

                if ts > 10_000_000_000:
                    ts /= 1000.0

                parsed.append(
                    {
                        "time": int(ts),
                        "open": float(o),
                        "high": float(h),
                        "low": float(l),
                        "close": float(c),
                    }
                )

            except Exception:

                continue

        if len(parsed) < 20:

            msg = (
                "Too few parsed candles: "
                f"{len(parsed)}"
            )

            if diag_key == "h4":

                DIAG["h4_errors"] += 1
                DIAG["last_h4_error"] = msg

            elif diag_key == "m5":

                DIAG["m5_errors"] += 1
                DIAG["last_m5_error"] = msg

            return None

        df = pd.DataFrame(
            parsed
        )

        df = (
            df
            .drop_duplicates("time")
            .sort_values("time")
            .reset_index(drop=True)
        )

        if diag_key == "h4":
            DIAG["h4_ok"] += 1

        elif diag_key == "m5":
            DIAG["m5_ok"] += 1

        return df.tail(
            count
        ).reset_index(
            drop=True
        )

    except Exception as e:

        msg = (
            f"{type(e).__name__}: "
            f"{e}"
        )

        if diag_key == "h4":

            DIAG["h4_errors"] += 1
            DIAG["last_h4_error"] = msg

        elif diag_key == "m5":

            DIAG["m5_errors"] += 1
            DIAG["last_m5_error"] = msg

        return None


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(df):

    pivots = []

    n = len(df)

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT,
    ):

        high = float(
            df.iloc[i]["high"]
        )

        low = float(
            df.iloc[i]["low"]
        )

        left_highs = [
            float(
                df.iloc[j]["high"]
            )
            for j in range(
                i - PIVOT_LEFT,
                i,
            )
        ]

        right_highs = [
            float(
                df.iloc[j]["high"]
            )
            for j in range(
                i + 1,
                i + PIVOT_RIGHT + 1,
            )
        ]

        left_lows = [
            float(
                df.iloc[j]["low"]
            )
            for j in range(
                i - PIVOT_LEFT,
                i,
            )
        ]

        right_lows = [
            float(
                df.iloc[j]["low"]
            )
            for j in range(
                i + 1,
                i + PIVOT_RIGHT + 1,
            )
        ]

        is_high = (
            all(
                high >= x
                for x in left_highs
            )
            and
            all(
                high >= x
                for x in right_highs
            )
        )

        is_low = (
            all(
                low <= x
                for x in left_lows
            )
            and
            all(
                low <= x
                for x in right_lows
            )
        )

        if is_high:

            pivots.append(
                {
                    "type": "H",
                    "time": int(
                        df.iloc[i]["time"]
                    ),
                    "price": high,
                    "index": i,
                }
            )

        if is_low:

            pivots.append(
                {
                    "type": "L",
                    "time": int(
                        df.iloc[i]["time"]
                    ),
                    "price": low,
                    "index": i,
                }
            )

    pivots.sort(
        key=lambda x: (
            x["time"],
            0 if x["type"] == "H" else 1,
        )
    )

    return pivots


# ============================================================
# CLEAN PIVOT SEQUENCE
# ============================================================

def build_ordered_pivots(pivots):

    cleaned = []

    for p in pivots:

        if not cleaned:

            cleaned.append(
                dict(p)
            )

            continue

        last = cleaned[-1]

        if p["type"] != last["type"]:

            cleaned.append(
                dict(p)
            )

            continue

        if p["type"] == "H":

            if p["price"] >= last["price"]:

                cleaned[-1] = dict(p)

        else:

            if p["price"] <= last["price"]:

                cleaned[-1] = dict(p)

    return cleaned


# ============================================================
# PERCENT DISTANCE
# ============================================================

def pct_change(
    a,
    b,
):

    if a == 0:
        return 0.0

    return (
        abs(b - a)
        / abs(a)
        * 100.0
    )


# ============================================================
# CALCULATE HOOK
# ============================================================

def calculate_hook(
    points,
    direction,
    timeframe,
):

    if len(points) != 6:
        return None

    p0, p1, p2, p3, p4, p5 = points

    if timeframe == H4_TIMEFRAME:

        min_swing = H4_MIN_SWING_PCT

    else:

        min_swing = M5_MIN_SWING_PCT

    # ========================================================
    # SHORT
    #
    # START -> H1 -> L1 -> H2 -> L2 -> H3
    # ========================================================

    if direction == "SHORT":

        if [
            p["type"]
            for p in points
        ] != [
            "L",
            "H",
            "L",
            "H",
            "L",
            "H",
        ]:

            return None

        start = p0
        h1 = p1
        l1 = p2
        h2 = p3
        l2 = p4
        h3 = p5

        if not (
            h2["price"] > h1["price"]
            and
            l2["price"] < l1["price"]
            and
            h3["price"] > h2["price"]
        ):

            return None

        if not (
            start["price"] < l1["price"]
            and
            start["price"] < l2["price"]
        ):

            return None

        swings = [
            (h1, l1),
            (l1, h2),
            (h2, l2),
            (l2, h3),
        ]

        for a, b in swings:

            if (
                pct_change(
                    a["price"],
                    b["price"],
                )
                < min_swing
            ):

                return None

        entry = h3["price"]

        # 86.4% retracement
        tp = (
            entry
            - 0.864
            * (
                entry
                - start["price"]
            )
        )

        # Existing SL logic preserved
        sl = (
            entry
            + 0.50
            * abs(
                entry
                - tp
            )
        )

        confirmation_time = (
            hook_confirmation_time(
                {
                    "entry_time":
                        h3["time"]
                },
                timeframe,
            )
        )

        return {
            "direction": "SHORT",

            "start_time":
                start["time"],

            "start_price":
                start["price"],

            "entry_time":
                h3["time"],

            "entry_price":
                entry,

            "confirmation_time":
                confirmation_time,

            "tp":
                tp,

            "sl":
                sl,

            "h1_time":
                h1["time"],

            "h1_price":
                h1["price"],

            "l1_time":
                l1["time"],

            "l1_price":
                l1["price"],

            "h2_time":
                h2["time"],

            "h2_price":
                h2["price"],

            "l2_time":
                l2["time"],

            "l2_price":
                l2["price"],

            "h3_time":
                h3["time"],

            "h3_price":
                h3["price"],

            "l3_time":
                None,

            "l3_price":
                None,

            "points":
                points,
        }

    # ========================================================
    # LONG
    #
    # START -> L1 -> H1 -> L2 -> H2 -> L3
    # ========================================================

    if direction == "LONG":

        if [
            p["type"]
            for p in points
        ] != [
            "H",
            "L",
            "H",
            "L",
            "H",
            "L",
        ]:

            return None

        start = p0
        l1 = p1
        h1 = p2
        l2 = p3
        h2 = p4
        l3 = p5

        if not (
            l2["price"] < l1["price"]
            and
            h2["price"] > h1["price"]
            and
            l3["price"] < l2["price"]
        ):

            return None

        if not (
            start["price"] > h1["price"]
            and
            start["price"] > h2["price"]
        ):

            return None

        swings = [
            (l1, h1),
            (h1, l2),
            (l2, h2),
            (h2, l3),
        ]

        for a, b in swings:

            if (
                pct_change(
                    a["price"],
                    b["price"],
                )
                < min_swing
            ):

                return None

        entry = l3["price"]

        # 86.4% retracement
        tp = (
            entry
            + 0.864
            * (
                start["price"]
                - entry
            )
        )

        # Existing SL logic preserved
        sl = (
            entry
            - 0.50
            * abs(
                entry
                - tp
            )
        )

        confirmation_time = (
            hook_confirmation_time(
                {
                    "entry_time":
                        l3["time"]
                },
                timeframe,
            )
        )

        return {
            "direction": "LONG",

            "start_time":
                start["time"],

            "start_price":
                start["price"],

            "entry_time":
                l3["time"],

            "entry_price":
                entry,

            "confirmation_time":
                confirmation_time,

            "tp":
                tp,

            "sl":
                sl,

            "h1_time":
                h1["time"],

            "h1_price":
                h1["price"],

            "l1_time":
                l1["time"],

            "l1_price":
                l1["price"],

            "h2_time":
                h2["time"],

            "h2_price":
                h2["price"],

            "l2_time":
                l2["time"],

            "l2_price":
                l2["price"],

            "h3_time":
                None,

            "h3_price":
                None,

            "l3_time":
                l3["time"],

            "l3_price":
                l3["price"],

            "points":
                points,
        }

    return None


# ============================================================
# DETECT HOOKS
# ============================================================

def detect_hooks(
    df,
    direction,
    timeframe,
):

    pivots = find_pivots(df)

    cleaned = build_ordered_pivots(
        pivots
    )

    hooks = []

    if len(cleaned) < 6:
        return hooks

    start_index = max(
        0,
        len(cleaned) - 500,
    )

    for i in range(
        start_index,
        len(cleaned) - 5,
    ):

        points = cleaned[
            i:i + 6
        ]

        hook = calculate_hook(
            points,
            direction,
            timeframe,
        )

        if hook is not None:

            hooks.append(
                hook
            )

    # Newest first
    hooks.sort(
        key=lambda x: (
            x["confirmation_time"],
            x["entry_time"],
        ),
        reverse=True,
    )

    return hooks


# ============================================================
# H4 DIRECTION
# ============================================================

def get_h4_direction(symbol):

    df = fetch_candles(
        symbol,
        H4_TIMEFRAME,
        H4_COUNT,
    )

    if (
        df is None
        or len(df) < 50
    ):

        DIAG["h4_no_valid"] += 1

        return None, None

    short_hooks = detect_hooks(
        df,
        "SHORT",
        H4_TIMEFRAME,
    )

    long_hooks = detect_hooks(
        df,
        "LONG",
        H4_TIMEFRAME,
    )

    if (
        not short_hooks
        and not long_hooks
    ):

        DIAG["h4_no_valid"] += 1

        return None, None

    # IMPORTANT:
    # detect_hooks() sorts newest first.
    latest_short = (
        short_hooks[0]
        if short_hooks
        else None
    )

    latest_long = (
        long_hooks[0]
        if long_hooks
        else None
    )

    if (
        latest_short
        and latest_long
    ):

        if (
            latest_short[
                "confirmation_time"
            ]
            >=
            latest_long[
                "confirmation_time"
            ]
        ):

            DIAG["h4_short"] += 1

            return (
                "SHORT",
                latest_short,
            )

        DIAG["h4_long"] += 1

        return (
            "LONG",
            latest_long,
        )

    if latest_short:

        DIAG["h4_short"] += 1

        return (
            "SHORT",
            latest_short,
        )

    if latest_long:

        DIAG["h4_long"] += 1

        return (
            "LONG",
            latest_long,
        )

    DIAG["h4_no_valid"] += 1

    return None, None


# ============================================================
# DATABASE CHECKS
# ============================================================

def hook_exists(
    symbol,
    hook,
):

    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
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


def trade_exists(
    symbol,
    hook,
):

    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
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
        SELECT id
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
        FROM trades
        WHERE status = 'OPEN'
        """
    ).fetchone()

    conn.close()

    return int(
        row[0]
    )


# ============================================================
# SAVE HOOK
# ============================================================

def save_hook(
    symbol,
    hook,
):

    conn = db_connect()

    conn.execute(
        """
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

            h1_time,
            h1_price,

            l1_time,
            l1_price,

            h2_time,
            h2_price,

            l2_time,
            l2_price,

            h3_time,
            h3_price,

            l3_time,
            l3_price,

            created_at

        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?
        )
        """,
        (
            symbol,
            hook["direction"],

            hook["start_time"],
            hook["entry_time"],
            hook["confirmation_time"],

            hook["start_price"],
            hook["entry_price"],

            hook["tp"],
            hook["sl"],

            hook.get("h1_time"),
            hook.get("h1_price"),

            hook.get("l1_time"),
            hook.get("l1_price"),

            hook.get("h2_time"),
            hook.get("h2_price"),

            hook.get("l2_time"),
            hook.get("l2_price"),

            hook.get("h3_time"),
            hook.get("h3_price"),

            hook.get("l3_time"),
            hook.get("l3_price"),

            now_ts(),
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# PAPER TRADE
# ============================================================

def create_paper_trade(
    symbol,
    hook,
):

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
            pnl_pct,

            created_at

        )
        VALUES (
            ?, ?, ?, ?, ?, ?, 'OPEN', NULL, ?
        )
        """,
        (
            symbol,
            hook["direction"],

            hook["entry_time"],
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
    pnl_pct,
    status,
):

    conn = db_connect()

    conn.execute(
        """
        UPDATE trades

        SET
            exit_time = ?,
            exit_price = ?,
            pnl_pct = ?,
            status = ?

        WHERE id = ?
        """,
        (
            exit_time,
            exit_price,
            pnl_pct,
            status,
            trade_id,
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# TP ALREADY TOUCHED
# ============================================================

def tp_already_touched(
    df,
    hook,
):

    after = df[
        df["time"]
        >= hook["entry_time"]
    ]

    if after.empty:
        return False

    if hook["direction"] == "SHORT":

        return bool(
            (
                after["low"]
                <= hook["tp"]
            ).any()
        )

    return bool(
        (
            after["high"]
            >= hook["tp"]
        ).any()
    )


# ============================================================
# SELECT HOOK
# ============================================================

def select_tradeable_hook(
    symbol,
    hooks,
):
    """
    Select the valid M5 hook with the greatest
    percentage distance from ENTRY to TP 86.4%.

    STALE DOES NOT BLOCK SELECTION.

    Duplicate hooks remain blocked.
    """

    candidates = []

    for hook in hooks:

        # ----------------------------------------------------
        # Duplicate protection
        # ----------------------------------------------------

        if (
            hook_exists(
                symbol,
                hook,
            )
            or
            trade_exists(
                symbol,
                hook,
            )
        ):

            DIAG["duplicate"] += 1

            continue

        entry = float(
            hook["entry_price"]
        )

        tp = float(
            hook["tp"]
        )

        if entry <= 0:
            continue

        # ----------------------------------------------------
        # Entry -> TP 86.4% distance
        # ----------------------------------------------------

        distance_pct = (
            abs(entry - tp)
            / entry
            * 100.0
        )

        hook["_tp_distance_pct"] = (
            distance_pct
        )

        candidates.append(
            hook
        )

    if not candidates:
        return None

    # --------------------------------------------------------
    # Greatest distance first
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x:
            x["_tp_distance_pct"],
        reverse=True,
    )

    selected = candidates[0]

    # --------------------------------------------------------
    # Stale is informational only
    # --------------------------------------------------------

    if (
        hook_age_seconds(
            selected
        )
        >
        MAX_NEW_SIGNAL_AGE_SECONDS
    ):

        DIAG["stale"] += 1

    return selected


# ============================================================
# MONITOR OPEN TRADES
# ============================================================

def monitor_open_trades():

    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY id ASC
        """
    ).fetchall()

    conn.close()

    for trade in rows:

        df = fetch_candles(
            trade["symbol"],
            M5_TIMEFRAME,
            10,
        )

        if (
            df is None
            or df.empty
        ):

            continue

        latest = df.iloc[-1]

        high = float(
            latest["high"]
        )

        low = float(
            latest["low"]
        )

        direction = (
            trade["direction"]
        )

        hit_sl = False
        hit_tp = False

        if direction == "SHORT":

            if high >= trade["sl"]:
                hit_sl = True

            if low <= trade["tp"]:
                hit_tp = True

        else:

            if low <= trade["sl"]:
                hit_sl = True

            if high >= trade["tp"]:
                hit_tp = True

        # ----------------------------------------------------
        # SL has priority if both occur in one candle.
        # ----------------------------------------------------

        if hit_sl:

            exit_price = trade["sl"]

            if direction == "SHORT":

                pnl_pct = (
                    (
                        trade["entry_price"]
                        - exit_price
                    )
                    / trade["entry_price"]
                ) * 100

            else:

                pnl_pct = (
                    (
                        exit_price
                        - trade["entry_price"]
                    )
                    / trade["entry_price"]
                ) * 100

            close_trade(
                trade["id"],
                int(
                    latest["time"]
                ),
                exit_price,
                pnl_pct,
                "LOSS",
            )

            telegram_send(
                f"🔴 <b>TRADE CLOSED</b>\n"
                f"{trade['symbol']}\n"
                f"{direction}\n"
                f"Exit: "
                f"{exit_price:.8g}\n"
                f"PnL: "
                f"{pnl_pct:+.2f}%"
            )

        elif hit_tp:

            exit_price = trade["tp"]

            if direction == "SHORT":

                pnl_pct = (
                    (
                        trade["entry_price"]
                        - exit_price
                    )
                    / trade["entry_price"]
                ) * 100

            else:

                pnl_pct = (
                    (
                        exit_price
                        - trade["entry_price"]
                    )
                    / trade["entry_price"]
                ) * 100

            close_trade(
                trade["id"],
                int(
                    latest["time"]
                ),
                exit_price,
                pnl_pct,
                "WIN",
            )

            telegram_send(
                f"🟢 <b>TRADE CLOSED</b>\n"
                f"{trade['symbol']}\n"
                f"{direction}\n"
                f"Exit: "
                f"{exit_price:.8g}\n"
                f"PnL: "
                f"{pnl_pct:+.2f}%"
            )


# ============================================================
# CHART
# ============================================================

def save_hook_chart(
    symbol,
    df,
    hook,
):

    os.makedirs(
        CHART_DIR,
        exist_ok=True,
    )

    filename = (
        f"{symbol}_"
        f"{hook['direction']}_"
        f"{hook['entry_time']}.png"
    )

    path = os.path.join(
        CHART_DIR,
        filename,
    )

    chart_df = df.tail(
        CHART_CANDLES
    ).copy()

    chart_df["dt"] = (
        chart_df["time"]
        .apply(ts_to_dt)
    )

    fig, ax = plt.subplots(
        figsize=(15, 8)
    )

    ax.plot(
        chart_df["dt"],
        chart_df["close"],
        linewidth=1.2,
        label="M5 Close",
    )

    # --------------------------------------------------------
    # HOOK POINTS
    # --------------------------------------------------------

    labels = [
        (
            "START",
            hook["start_time"],
            hook["start_price"],
        ),
        (
            "H1",
            hook.get("h1_time"),
            hook.get("h1_price"),
        ),
        (
            "L1",
            hook.get("l1_time"),
            hook.get("l1_price"),
        ),
        (
            "H2",
            hook.get("h2_time"),
            hook.get("h2_price"),
        ),
        (
            "L2",
            hook.get("l2_time"),
            hook.get("l2_price"),
        ),
    ]

    if hook["direction"] == "SHORT":

        labels.append(
            (
                "H3",
                hook.get("h3_time"),
                hook.get("h3_price"),
            )
        )

    else:

        labels.append(
            (
                "L3",
                hook.get("l3_time"),
                hook.get("l3_price"),
            )
        )

    hook_x = []
    hook_y = []

    for (
        label,
        t,
        price,
    ) in labels:

        if (
            t is None
            or price is None
        ):

            continue

        dt = ts_to_dt(t)

        hook_x.append(dt)
        hook_y.append(price)

        ax.scatter(
            [dt],
            [price],
            s=55,
            zorder=5,
        )

        if label in (
            "START",
            "H1",
            "H2",
        ):

            offset = 14

        else:

            offset = -20

        ax.annotate(
            (
                f"{label}\n"
                f"{price:.8g}"
            ),
            (
                dt,
                price,
            ),
            xytext=(
                0,
                offset,
            ),
            textcoords="offset points",
            ha="center",
            fontsize=9,
            fontweight="bold",
        )

    if hook_x:

        ax.plot(
            hook_x,
            hook_y,
            linewidth=2.0,
            linestyle="--",
            label="NDS Hook",
        )

    # --------------------------------------------------------
    # ENTRY
    # --------------------------------------------------------

    entry_dt = ts_to_dt(
        hook["entry_time"]
    )

    ax.axhline(
        hook["entry_price"],
        linewidth=1.2,
        label=(
            f"ENTRY "
            f"{hook['entry_price']:.8g}"
        ),
    )

    ax.annotate(
        (
            f"ENTRY\n"
            f"{hook['entry_price']:.8g}"
        ),
        (
            entry_dt,
            hook["entry_price"],
        ),
        xytext=(
            25,
            25,
        ),
        textcoords="offset points",
        fontsize=9,
        fontweight="bold",
    )

    # --------------------------------------------------------
    # TP 86.4%
    # --------------------------------------------------------

    ax.axhline(
        hook["tp"],
        linestyle=":",
        linewidth=1.8,
        label=(
            f"TP 86.4% "
            f"{hook['tp']:.8g}"
        ),
    )

    ax.annotate(
        (
            f"TP 86.4%\n"
            f"{hook['tp']:.8g}"
        ),
        (
            entry_dt,
            hook["tp"],
        ),
        xytext=(
            35,
            -20,
        ),
        textcoords="offset points",
        fontsize=9,
        fontweight="bold",
    )

    # --------------------------------------------------------
    # SL
    # --------------------------------------------------------

    ax.axhline(
        hook["sl"],
        linestyle="--",
        linewidth=1.4,
        label=(
            f"SL "
            f"{hook['sl']:.8g}"
        ),
    )

    ax.annotate(
        (
            f"SL\n"
            f"{hook['sl']:.8g}"
        ),
        (
            entry_dt,
            hook["sl"],
        ),
        xytext=(
            35,
            20,
        ),
        textcoords="offset points",
        fontsize=9,
        fontweight="bold",
    )

    # --------------------------------------------------------
    # CONFIRMATION
    # --------------------------------------------------------

    confirmation_dt = ts_to_dt(
        hook["confirmation_time"]
    )

    ax.axvline(
        confirmation_dt,
        linestyle=":",
        linewidth=1.0,
        alpha=0.7,
        label="CONFIRMED",
    )

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    final_point = (
        "H3"
        if hook["direction"] == "SHORT"
        else "L3"
    )

    distance_pct = (
        hook.get(
            "_tp_distance_pct",
            abs(
                hook["entry_price"]
                - hook["tp"]
            )
            / hook["entry_price"]
            * 100.0,
        )
    )

    ax.set_title(
        (
            f"NDS H4 → M5 | "
            f"{symbol} | "
            f"{hook['direction']}\n"
            f"Selected {final_point} | "
            f"Max TP 86.4% Distance: "
            f"{distance_pct:.2f}%"
        )
    )

    ax.set_xlabel("UTC")
    ax.set_ylabel("Price")

    ax.grid(
        alpha=0.25
    )

    ax.legend(
        loc="best",
        fontsize=8,
    )

    fig.autofmt_xdate()

    plt.tight_layout()

    fig.savefig(
        path,
        dpi=150,
        bbox_inches="tight",
    )

    plt.close(fig)

    return path


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def build_signal_message(
    symbol,
    hook,
):

    direction = (
        hook["direction"]
    )

    emoji = (
        "🔴"
        if direction == "SHORT"
        else "🟢"
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

    distance_pct = (
        abs(entry - tp)
        / entry
        * 100.0
    )

    if direction == "SHORT":

        tp_pct = (
            (entry - tp)
            / entry
        ) * 100

        sl_pct = (
            (sl - entry)
            / entry
        ) * 100

    else:

        tp_pct = (
            (tp - entry)
            / entry
        ) * 100

        sl_pct = (
            (entry - sl)
            / entry
        ) * 100

    confirmation_dt = ts_to_dt(
        hook["confirmation_time"]
    )

    final_point = (
        "H3"
        if direction == "SHORT"
        else "L3"
    )

    return (
        f"{emoji} "
        f"<b>NDS {direction}</b>\n"
        f"<b>{symbol}</b>\n\n"

        f"⭐ <b>SELECTED MAX 86.4% DISTANCE</b>\n\n"

        f"Entry ({final_point}): "
        f"<b>{entry:.8g}</b>\n"

        f"TP 86.4%: "
        f"<b>{tp:.8g}</b> "
        f"({tp_pct:.2f}%)\n"

        f"Distance to TP: "
        f"<b>{distance_pct:.2f}%</b>\n"

        f"SL: "
        f"<b>{sl:.8g}</b> "
        f"({sl_pct:.2f}%)\n\n"

        f"START: "
        f"<b>{hook['start_price']:.8g}</b>\n"

        f"H1: "
        f"{hook.get('h1_price', '-')}\n"

        f"L1: "
        f"{hook.get('l1_price', '-')}\n"

        f"H2: "
        f"{hook.get('h2_price', '-')}\n"

        f"L2: "
        f"{hook.get('l2_price', '-')}\n"

        f"{final_point}: "
        f"<b>{entry:.8g}</b>\n\n"

        f"Confirmed: "
        f"{confirmation_dt.strftime('%Y-%m-%d %H:%M:%S')} "
        f"UTC\n\n"

        f"🟡 <b>PAPER TRADE OPENED</b>"
    )


# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(symbol):

    try:

        # ----------------------------------------------------
        # H4 direction
        # ----------------------------------------------------

        direction, h4_hook = (
            get_h4_direction(
                symbol
            )
        )

        if direction is None:
            return

        DIAG["h4_filtered"] += 1

        # ----------------------------------------------------
        # M5 data
        # ----------------------------------------------------

        df = fetch_candles(
            symbol,
            M5_TIMEFRAME,
            M5_COUNT,
        )

        if (
            df is None
            or len(df) < 100
        ):

            DIAG["m5_short"] += 1

            return

        # ----------------------------------------------------
        # M5 matching hooks
        # ----------------------------------------------------

        hooks = detect_hooks(
            df,
            direction,
            M5_TIMEFRAME,
        )

        if direction == "SHORT":

            DIAG[
                "matching_short"
            ] += len(hooks)

        else:

            DIAG[
                "matching_long"
            ] += len(hooks)

        if not hooks:

            DIAG[
                "no_matching"
            ] += 1

            return

        # ----------------------------------------------------
        # SELECT MAX DISTANCE TO 86.4%
        # ----------------------------------------------------

        hook = (
            select_tradeable_hook(
                symbol,
                hooks,
            )
        )

        if hook is None:
            return

        # ----------------------------------------------------
        # TP ALREADY TOUCHED
        # ----------------------------------------------------

        if tp_already_touched(
            df,
            hook,
        ):

            DIAG[
                "tp_touched"
            ] += 1

            return

        # ----------------------------------------------------
        # EXISTING OPEN TRADE
        # ----------------------------------------------------

        if has_open_trade(
            symbol
        ):

            DIAG[
                "open_blocked"
            ] += 1

            return

        # ----------------------------------------------------
        # MAX OPEN TRADES
        # ----------------------------------------------------

        if (
            count_open_trades()
            >= MAX_OPEN_TRADES
        ):

            DIAG[
                "max_open_blocked"
            ] += 1

            return

        # ----------------------------------------------------
        # SAVE SELECTED HOOK
        # ----------------------------------------------------

        save_hook(
            symbol,
            hook,
        )

        # ----------------------------------------------------
        # CREATE PAPER TRADE
        # ----------------------------------------------------

        create_paper_trade(
            symbol,
            hook,
        )

        DIAG["signals"] += 1

        # ----------------------------------------------------
        # CHART
        # ----------------------------------------------------

        chart_path = (
            save_hook_chart(
                symbol,
                df,
                hook,
            )
        )

        # ----------------------------------------------------
        # TELEGRAM SIGNAL
        # ----------------------------------------------------

        telegram_send(
            build_signal_message(
                symbol,
                hook,
            )
        )

        # ----------------------------------------------------
        # TELEGRAM CHART
        # ----------------------------------------------------

        if chart_path:

            telegram_photo(
                chart_path,
                (
                    f"NDS "
                    f"{direction} | "
                    f"{symbol} | "
                    f"MAX 86.4% DISTANCE"
                ),
            )

    except Exception:

        traceback.print_exc()


# ============================================================
# PERFORMANCE
# ============================================================

def performance():

    conn = db_connect()

    closed = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status IN (
            'WIN',
            'LOSS'
        )
        """
    ).fetchone()[0]

    wins = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'WIN'
        """
    ).fetchone()[0]

    losses = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'LOSS'
        """
    ).fetchone()[0]

    pnl = conn.execute(
        """
        SELECT COALESCE(
            SUM(pnl_pct),
            0
        )
        FROM trades
        WHERE status IN (
            'WIN',
            'LOSS'
        )
        """
    ).fetchone()[0]

    open_trades = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'OPEN'
        """
    ).fetchone()[0]

    conn.close()

    win_rate = (
        wins
        / closed
        * 100
        if closed
        else 0.0
    )

    return {
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "pnl": pnl,
        "open": open_trades,
    }


# ============================================================
# DIAGNOSTIC
# ============================================================

def build_diagnostic(
    runtime_seconds,
):

    p = performance()

    now = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    text = (
        f"🔎 "
        f"<b>NDS H4 → M5 DIAGNOSTIC</b>\n"

        f"Version: "
        f"<b>{VERSION}</b>\n"

        f"Time: "
        f"<b>{now} UTC</b>\n"

        f"Runtime: "
        f"<b>{runtime_seconds:.1f}s</b>\n\n"

        f"━━━ <b>H4</b> ━━━\n"

        f"Requests: "
        f"{DIAG['h4_requests']}\n"

        f"Data OK: "
        f"{DIAG['h4_ok']}\n"

        f"Data Error: "
        f"{DIAG['h4_errors']}\n"

        f"No Valid Hook: "
        f"{DIAG['h4_no_valid']}\n"

        f"H4 Filtered: "
        f"{DIAG['h4_filtered']}\n"

        f"SHORT Hooks: "
        f"{DIAG['h4_short']}\n"

        f"LONG Hooks: "
        f"{DIAG['h4_long']}\n\n"

        f"━━━ <b>M5</b> ━━━\n"

        f"Requests: "
        f"{DIAG['m5_requests']}\n"

        f"Data OK: "
        f"{DIAG['m5_ok']}\n"

        f"Data Error: "
        f"{DIAG['m5_errors']}\n"

        f"Short Data: "
        f"{DIAG['m5_short']}\n"

        f"Matching SHORT Hooks: "
        f"{DIAG['matching_short']}\n"

        f"Matching LONG Hooks: "
        f"{DIAG['matching_long']}\n"

        f"No Matching Hook: "
        f"{DIAG['no_matching']}\n"

        f"Stale: "
        f"{DIAG['stale']}\n"

        f"TP Already Touched: "
        f"{DIAG['tp_touched']}\n"

        f"Duplicate: "
        f"{DIAG['duplicate']}\n"

        f"Open Trade Blocked: "
        f"{DIAG['open_blocked']}\n"

        f"Max Open Blocked: "
        f"{DIAG['max_open_blocked']}\n\n"

        f"━━━ <b>SIGNALS</b> ━━━\n"

        f"New Signals: "
        f"{DIAG['signals']}\n\n"

        f"━━━ <b>OPEN TRADES</b> ━━━\n"
    )

    # --------------------------------------------------------
    # OPEN TRADES DETAILS
    # --------------------------------------------------------

    conn = db_connect()

    open_rows = conn.execute(
        """
        SELECT
            symbol,
            direction,
            entry_price,
            tp,
            sl,
            entry_time
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY entry_time DESC
        """
    ).fetchall()

    conn.close()

    if not open_rows:

        text += (
            "No open trades.\n"
        )

    else:

        for row in open_rows:

            entry = float(
                row["entry_price"]
            )

            tp = float(
                row["tp"]
            )

            sl = float(
                row["sl"]
            )

            distance_pct = (
                abs(entry - tp)
                / entry
                * 100.0
            )

            emoji = (
                "🔴"
                if row["direction"]
                == "SHORT"
                else "🟢"
            )

            text += (
                f"\n{emoji} "
                f"<b>{row['symbol']}</b> "
                f"{row['direction']}\n"

                f"Entry: "
                f"<b>{entry:.8g}</b>\n"

                f"TP 86.4%: "
                f"<b>{tp:.8g}</b> "
                f"({distance_pct:.2f}%)\n"

                f"SL: "
                f"<b>{sl:.8g}</b>\n"
            )

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    text += (
        "\n━━━ <b>PAPER PERFORMANCE</b> ━━━\n"

        f"Closed: "
        f"{p['closed']}\n"

        f"Wins: "
        f"{p['wins']}\n"

        f"Losses: "
        f"{p['losses']}\n"

        f"Win Rate: "
        f"{p['win_rate']:.2f}%\n"

        f"Realized PnL: "
        f"{p['pnl']:+.2f}%\n"

        f"Open Trades: "
        f"{p['open']}\n\n"

        f"🟡 <b>PAPER ONLY</b>\n"
        f"No real exchange orders are sent."
    )

    # --------------------------------------------------------
    # API ERRORS
    # --------------------------------------------------------

    if DIAG["last_h4_error"]:

        text += (
            "\n\n"
            f"⚠️ <b>Last H4 API error:</b>\n"
            f"<code>"
            f"{DIAG['last_h4_error']}"
            f"</code>"
        )

    if DIAG["last_m5_error"]:

        text += (
            "\n\n"
            f"⚠️ <b>Last M5 API error:</b>\n"
            f"<code>"
            f"{DIAG['last_m5_error']}"
            f"</code>"
        )

    return text


# ============================================================
# MAIN SCAN
# ============================================================

def run_scan():

    init_db()

    start = time.time()

    # --------------------------------------------------------
    # Monitor existing paper trades
    # --------------------------------------------------------

    monitor_open_trades()

    # --------------------------------------------------------
    # Scan all assets
    # --------------------------------------------------------

    for symbol in ASSETS:

        process_symbol(
            symbol
        )

    runtime = (
        time.time()
        - start
    )

    diagnostic = (
        build_diagnostic(
            runtime
        )
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

    try:

        run_scan()

    except KeyboardInterrupt:

        print(
            "Scanner stopped by user."
        )

    except Exception:

        traceback.print_exc()

        raise
