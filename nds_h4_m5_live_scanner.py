# ============================================================
# NDS H4 -> M5 LIVE SCANNER
# VERSION 5.3.0
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
# SHORT / POSITIVE HOOK:
#   START -> H1 -> L1 -> H2 -> L2 -> H3
#
#   START = lowest point
#   H2 > H1
#   L2 < L1
#   H3 > H2
#
#   ENTRY = H3
#
# LONG / NEGATIVE HOOK:
#   START -> L1 -> H1 -> L2 -> H2 -> L3
#
#   START = highest point
#   L2 < L1
#   H2 > H1
#   L3 < L2
#
#   ENTRY = L3
#
# TP:
#   86.4% retracement
#
# IMPORTANT:
#   86.4% is NOT the trade start.
#   Trade starts only at confirmed H3/L3.
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

VERSION = "5.3.0"

DB_FILE = "nds_h4_m5_live_v51.db"
CHART_DIR = "nds_h4_m5_charts"

KRAKEN_BASE = "https://futures.kraken.com/api/charts/v1/trade"

H4_TIMEFRAME = "240"
M5_TIMEFRAME = "5"

H4_COUNT = 320
M5_COUNT = 500

CHART_CANDLES = 180

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

M5_MIN_SWING_PCT = 0.07
H4_MIN_SWING_PCT = 0.20

MAX_NEW_SIGNAL_AGE_SECONDS = 20 * 60

MAX_OPEN_TRADES = 3

REQUEST_TIMEOUT = 20

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
# GLOBAL DIAGNOSTICS
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
# TIME HELPERS
# ============================================================

def now_ts():
    return int(time.time())


def interval_minutes(timeframe):
    if timeframe == "1":
        return 1
    if timeframe == "5":
        return 5
    if timeframe == "15":
        return 15
    if timeframe == "30":
        return 30
    if timeframe == "60":
        return 60
    if timeframe == "240":
        return 240

    return 1


def hook_confirmation_time(hook, timeframe):
    """
    Pivot with PIVOT_RIGHT candles is confirmed only after
    those right-side candles exist.

    For M5 with PIVOT_RIGHT=2:
        confirmation = pivot time + 10 minutes
    """

    return int(
        hook["entry_time"]
        + PIVOT_RIGHT * interval_minutes(timeframe) * 60
    )


def hook_age_seconds(hook):
    confirmation_time = hook.get(
        "confirmation_time",
        hook["entry_time"]
    )

    return max(0, now_ts() - int(confirmation_time))


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


def telegram_photo(photo_path, caption=""):
    if not telegram_enabled():
        return False

    if not os.path.exists(photo_path):
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    try:
        with open(photo_path, "rb") as f:
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
# KRAKEN DATA
# ============================================================

def fetch_candles(symbol, timeframe, count):
    url = (
        f"{KRAKEN_BASE}/"
        f"{symbol}/"
        f"{timeframe}"
    )

    DIAG_KEY = None

    if timeframe == H4_TIMEFRAME:
        DIAG["h4_requests"] += 1
        DIAG_KEY = "h4"

    elif timeframe == M5_TIMEFRAME:
        DIAG["m5_requests"] += 1
        DIAG_KEY = "m5"

    try:
        r = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
        )

        if not r.ok:
            if DIAG_KEY == "h4":
                DIAG["h4_errors"] += 1
            elif DIAG_KEY == "m5":
                DIAG["m5_errors"] += 1

            return None

        data = r.json()

        rows = data.get("candles")

        if rows is None:
            rows = data.get("data")

        if not rows:
            if DIAG_KEY == "h4":
                DIAG["h4_errors"] += 1
            elif DIAG_KEY == "m5":
                DIAG["m5_errors"] += 1

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
            if DIAG_KEY == "h4":
                DIAG["h4_errors"] += 1
            elif DIAG_KEY == "m5":
                DIAG["m5_errors"] += 1

            return None

        df = pd.DataFrame(parsed)

        df = (
            df
            .drop_duplicates("time")
            .sort_values("time")
            .reset_index(drop=True)
        )

        if DIAG_KEY == "h4":
            DIAG["h4_ok"] += 1

        elif DIAG_KEY == "m5":
            DIAG["m5_ok"] += 1

        return df.tail(count).reset_index(drop=True)

    except Exception:
        if DIAG_KEY == "h4":
            DIAG["h4_errors"] += 1

        elif DIAG_KEY == "m5":
            DIAG["m5_errors"] += 1

        return None


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(df):
    pivots = []

    n = len(df)

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT
    ):
        high = float(df.iloc[i]["high"])
        low = float(df.iloc[i]["low"])

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

        is_high = (
            all(high >= x for x in left_highs)
            and
            all(high >= x for x in right_highs)
        )

        is_low = (
            all(low <= x for x in left_lows)
            and
            all(low <= x for x in right_lows)
        )

        if is_high:
            pivots.append(
                {
                    "type": "H",
                    "time": int(df.iloc[i]["time"]),
                    "price": high,
                    "index": i,
                }
            )

        if is_low:
            pivots.append(
                {
                    "type": "L",
                    "time": int(df.iloc[i]["time"]),
                    "price": low,
                    "index": i,
                }
            )

    pivots.sort(
        key=lambda x: (
            x["time"],
            0 if x["type"] == "H" else 1
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
            cleaned.append(dict(p))
            continue

        last = cleaned[-1]

        if p["type"] != last["type"]:
            cleaned.append(dict(p))
            continue

        # Same type consecutive pivot.
        # Keep the stronger one.
        if p["type"] == "H":
            if p["price"] >= last["price"]:
                cleaned[-1] = dict(p)

        else:
            if p["price"] <= last["price"]:
                cleaned[-1] = dict(p)

    return cleaned


# ============================================================
# PERCENTAGE SWING
# ============================================================

def pct_change(a, b):
    if a == 0:
        return 0.0

    return abs(b - a) / abs(a) * 100.0


# ============================================================
# HOOK CALCULATION
# ============================================================

def calculate_hook(
    points,
    direction,
    timeframe,
):
    if len(points) != 6:
        return None

    p0, p1, p2, p3, p4, p5 = points

    # --------------------------------------------------------
    # SHORT / POSITIVE
    #
    # START -> H1 -> L1 -> H2 -> L2 -> H3
    # --------------------------------------------------------

    if direction == "SHORT":

        if not (
            p0["type"] == "L"
            and p1["type"] == "H"
            and p2["type"] == "L"
            and p3["type"] == "H"
            and p4["type"] == "L"
            and p5["type"] == "H"
        ):
            return None

        start = p0
        h1 = p1
        l1 = p2
        h2 = p3
        l2 = p4
        h3 = p5

        # Required structure
        if not (
            h2["price"] > h1["price"]
            and
            l2["price"] < l1["price"]
            and
            h3["price"] > h2["price"]
        ):
            return None

        # START must be lower than the hook's highs
        if not (
            start["price"] < l1["price"]
            and
            start["price"] < l2["price"]
        ):
            return None

        # Minimum swings
        if pct_change(
            h1["price"],
            l1["price"]
        ) < M5_MIN_SWING_PCT:
            return None

        if pct_change(
            l1["price"],
            h2["price"]
        ) < M5_MIN_SWING_PCT:
            return None

        if pct_change(
            h2["price"],
            l2["price"]
        ) < M5_MIN_SWING_PCT:
            return None

        if pct_change(
            l2["price"],
            h3["price"]
        ) < M5_MIN_SWING_PCT:
            return None

        entry = h3["price"]

        # ----------------------------------------------------
        # 86.4% retracement
        #
        # Start is the 0% origin.
        # Entry is H3.
        # TP is 86.4% back toward START.
        # ----------------------------------------------------

        tp = (
            entry
            - 0.864 * (entry - start["price"])
        )

        # Current conservative SL rule
        sl = (
            entry
            + 0.50 * abs(entry - tp)
        )

        confirmation_time = hook_confirmation_time(
            {
                "entry_time": h3["time"]
            },
            timeframe
        )

        return {
            "direction": "SHORT",

            "start_time": start["time"],
            "start_price": start["price"],

            "entry_time": h3["time"],
            "entry_price": entry,

            "confirmation_time": confirmation_time,

            "tp": tp,
            "sl": sl,

            "h1_time": h1["time"],
            "h1_price": h1["price"],

            "l1_time": l1["time"],
            "l1_price": l1["price"],

            "h2_time": h2["time"],
            "h2_price": h2["price"],

            "l2_time": l2["time"],
            "l2_price": l2["price"],

            "h3_time": h3["time"],
            "h3_price": h3["price"],

            "points": points,
        }

    # --------------------------------------------------------
    # LONG / NEGATIVE
    #
    # START -> L1 -> H1 -> L2 -> H2 -> L3
    # --------------------------------------------------------

    if direction == "LONG":

        if not (
            p0["type"] == "H"
            and p1["type"] == "L"
            and p2["type"] == "H"
            and p3["type"] == "L"
            and p4["type"] == "H"
            and p5["type"] == "L"
        ):
            return None

        start = p0
        l1 = p1
        h1 = p2
        l2 = p3
        h2 = p4
        l3 = p5

        # Required structure
        if not (
            l2["price"] < l1["price"]
            and
            h2["price"] > h1["price"]
            and
            l3["price"] < l2["price"]
        ):
            return None

        # START must be higher than hook lows
        if not (
            start["price"] > h1["price"]
            and
            start["price"] > h2["price"]
        ):
            return None

        # Minimum swings
        if pct_change(
            l1["price"],
            h1["price"]
        ) < M5_MIN_SWING_PCT:
            return None

        if pct_change(
            h1["price"],
            l2["price"]
        ) < M5_MIN_SWING_PCT:
            return None

        if pct_change(
            l2["price"],
            h2["price"]
        ) < M5_MIN_SWING_PCT:
            return None

        if pct_change(
            h2["price"],
            l3["price"]
        ) < M5_MIN_SWING_PCT:
            return None

        entry = l3["price"]

        # ----------------------------------------------------
        # 86.4% retracement
        #
        # Start is the 0% origin.
        # Entry is L3.
        # TP is 86.4% back toward START.
        # ----------------------------------------------------

        tp = (
            entry
            + 0.864 * (
                start["price"] - entry
            )
        )

        # Current conservative SL rule
        sl = (
            entry
            - 0.50 * abs(entry - tp)
        )

        confirmation_time = hook_confirmation_time(
            {
                "entry_time": l3["time"]
            },
            timeframe
        )

        return {
            "direction": "LONG",

            "start_time": start["time"],
            "start_price": start["price"],

            "entry_time": l3["time"],
            "entry_price": entry,

            "confirmation_time": confirmation_time,

            "tp": tp,
            "sl": sl,

            "h1_time": h1["time"],
            "h1_price": h1["price"],

            "l1_time": l1["time"],
            "l1_price": l1["price"],

            "h2_time": h2["time"],
            "h2_price": h2["price"],

            "l2_time": l2["time"],
            "l2_price": l2["price"],

            "h3_time": None,
            "h3_price": None,

            "l3_time": l3["time"],
            "l3_price": l3["price"],

            "points": points,
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

    cleaned = build_ordered_pivots(pivots)

    hooks = []

    if len(cleaned) < 6:
        return hooks

    for i in range(
        0,
        len(cleaned) - 5
    ):
        points = cleaned[i:i + 6]

        hook = calculate_hook(
            points,
            direction,
            timeframe,
        )

        if hook is not None:
            hooks.append(hook)

    # Newest confirmed hook first
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

    if df is None or len(df) < 50:
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

    if not short_hooks and not long_hooks:
        DIAG["h4_no_valid"] += 1
        return None, None

    if short_hooks:
        latest_short = short_hooks[-1]
    else:
        latest_short = None

    if long_hooks:
        latest_long = long_hooks[-1]
    else:
        latest_long = None

    # H4 direction is determined by newest Hook
    if latest_short and latest_long:

        if (
            latest_short["confirmation_time"]
            >= latest_long["confirmation_time"]
        ):
            DIAG["h4_short"] += 1
            return "SHORT", latest_short

        DIAG["h4_long"] += 1
        return "LONG", latest_long

    if latest_short:
        DIAG["h4_short"] += 1
        return "SHORT", latest_short

    if latest_long:
        DIAG["h4_long"] += 1
        return "LONG", latest_long

    DIAG["h4_no_valid"] += 1

    return None, None


# ============================================================
# DATABASE HOOK CHECK
# ============================================================

def hook_exists(symbol, hook):
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


# ============================================================
# SAVE HOOK
# ============================================================

def save_hook(symbol, hook):
    conn = db_connect()

    try:
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

    finally:
        conn.close()


# ============================================================
# TRADE CHECKS
# ============================================================

def trade_exists(symbol, hook):
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

    return int(row[0])


# ============================================================
# TP ALREADY TOUCHED
# ============================================================

def tp_already_touched(
    df,
    hook,
):
    entry_time = hook["entry_time"]

    after = df[
        df["time"] >= entry_time
    ]

    if after.empty:
        return False

    if hook["direction"] == "SHORT":
        touched = (
            after["low"]
            <= hook["tp"]
        )

    else:
        touched = (
            after["high"]
            >= hook["tp"]
        )

    return bool(touched.any())


# ============================================================
# SELECT TRADEABLE HOOK
# ============================================================

def select_tradeable_hook(
    symbol,
    hooks,
):
    """
    Do NOT simply take the newest detected hook.

    A newer hook can be stale/duplicate while an older
    confirmed hook may still be inside the allowed window.

    Therefore inspect candidates newest -> oldest.
    """

    for hook in hooks:

        if hook_exists(symbol, hook):
            DIAG["duplicate"] += 1
            continue

        if trade_exists(symbol, hook):
            DIAG["duplicate"] += 1
            continue

        age = hook_age_seconds(hook)

        if age > MAX_NEW_SIGNAL_AGE_SECONDS:
            DIAG["stale"] += 1
            continue

        return hook

    return None


# ============================================================
# SAVE PAPER TRADE
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
        VALUES (?, ?, ?, ?, ?, ?, 'OPEN', NULL, ?)
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


# ============================================================
# CLOSE TRADE
# ============================================================

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

        if df is None or df.empty:
            continue

        latest = df.iloc[-1]

        high = float(latest["high"])
        low = float(latest["low"])

        direction = trade["direction"]

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

        # Conservative:
        # If both are touched in the same candle,
        # assume SL happened first.
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
                int(latest["time"]),
                exit_price,
                pnl_pct,
                "LOSS",
            )

            telegram_send(
                f"🔴 <b>TRADE CLOSED</b>\n"
                f"{trade['symbol']}\n"
                f"{direction}\n"
                f"Exit: {exit_price:.8g}\n"
                f"PnL: {pnl_pct:+.2f}%"
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
                int(latest["time"]),
                exit_price,
                pnl_pct,
                "WIN",
            )

            telegram_send(
                f"🟢 <b>TRADE CLOSED</b>\n"
                f"{trade['symbol']}\n"
                f"{direction}\n"
                f"Exit: {exit_price:.8g}\n"
                f"PnL: {pnl_pct:+.2f}%"
            )


# ============================================================
# CHART
# ============================================================

def ts_to_dt(ts):
    return datetime.fromtimestamp(
        int(ts),
        tz=timezone.utc,
    )


def save_hook_chart(
    symbol,
    df,
    hook,
):
    os.makedirs(
        CHART_DIR,
        exist_ok=True,
    )

    direction = hook["direction"]

    safe_symbol = (
        symbol
        .replace("/", "_")
        .replace(":", "_")
    )

    filename = (
        f"{safe_symbol}_"
        f"{direction}_"
        f"{hook['entry_time']}.png"
    )

    path = os.path.join(
        CHART_DIR,
        filename,
    )

    chart_df = df.tail(
        CHART_CANDLES
    ).copy()

    chart_df["dt"] = chart_df["time"].apply(
        ts_to_dt
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
    # Hook points
    # --------------------------------------------------------

    labels = [
        ("START", hook["start_time"], hook["start_price"]),
        ("H1", hook.get("h1_time"), hook.get("h1_price")),
        ("L1", hook.get("l1_time"), hook.get("l1_price")),
        ("H2", hook.get("h2_time"), hook.get("h2_price")),
        ("L2", hook.get("l2_time"), hook.get("l2_price")),
    ]

    if direction == "SHORT":
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

    for label, t, price in labels:

        if t is None or price is None:
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

        offset = 8

        if label in ("START", "H1", "H2"):
            offset = 12

        if label in ("L1", "L2", "L3"):
            offset = -18

        ax.annotate(
            f"{label}\n{price:.8g}",
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

    # --------------------------------------------------------
    # Connect Hook points
    # --------------------------------------------------------

    if hook_x and hook_y:
        ax.plot(
            hook_x,
            hook_y,
            linewidth=2.0,
            linestyle="--",
            label="NDS Hook",
        )

    # --------------------------------------------------------
    # Entry
    # --------------------------------------------------------

    entry_dt = ts_to_dt(
        hook["entry_time"]
    )

    ax.axhline(
        hook["entry_price"],
        linestyle="-",
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
        bbox=dict(
            boxstyle="round,pad=0.25",
            alpha=0.8,
        ),
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
        bbox=dict(
            boxstyle="round,pad=0.25",
            alpha=0.8,
        ),
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
        bbox=dict(
            boxstyle="round,pad=0.25",
            alpha=0.8,
        ),
    )

    # --------------------------------------------------------
    # Confirmation marker
    # --------------------------------------------------------

    confirmation_dt = ts_to_dt(
        hook["confirmation_time"]
    )

    ax.axvline(
        confirmation_dt,
        linestyle=":",
        linewidth=1.0,
        alpha=0.7,
        label="H3/L3 Confirmation",
    )

    ax.annotate(
        "CONFIRMED",
        (
            confirmation_dt,
            chart_df["close"].max(),
        ),
        xytext=(
            5,
            -15,
        ),
        textcoords="offset points",
        rotation=90,
        fontsize=8,
        va="top",
    )

    ax.set_title(
        (
            f"NDS H4 → M5 | "
            f"{symbol} | {direction}\n"
            f"ENTRY at confirmed "
            f"{'H3' if direction == 'SHORT' else 'L3'}"
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
    direction = hook["direction"]

    emoji = (
        "🔴"
        if direction == "SHORT"
        else "🟢"
    )

    entry = hook["entry_price"]
    tp = hook["tp"]
    sl = hook["sl"]

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

    return (
        f"{emoji} <b>NDS {direction}</b>\n"
        f"<b>{symbol}</b>\n\n"

        f"Entry: <b>{entry:.8g}</b>\n"
        f"TP 86.4%: <b>{tp:.8g}</b> "
        f"({tp_pct:.2f}%)\n"
        f"SL: <b>{sl:.8g}</b> "
        f"({sl_pct:.2f}%)\n\n"

        f"Hook Start: {hook['start_price']:.8g}\n"
        f"Confirmed: "
        f"{confirmation_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"

        f"🟡 PAPER ONLY"
    )


# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(symbol):
    try:

        # ----------------------------------------------------
        # H4 direction
        # ----------------------------------------------------

        direction, h4_hook = get_h4_direction(
            symbol
        )

        if direction is None:
            return

        DIAG["h4_filtered"] += 1

        # ----------------------------------------------------
        # M5
        # ----------------------------------------------------

        df = fetch_candles(
            symbol,
            M5_TIMEFRAME,
            M5_COUNT,
        )

        if df is None or len(df) < 100:
            DIAG["m5_short"] += 1
            return

        # ----------------------------------------------------
        # Matching hooks
        # ----------------------------------------------------

        hooks = detect_hooks(
            df,
            direction,
            M5_TIMEFRAME,
        )

        if direction == "SHORT":
            DIAG["matching_short"] += len(hooks)
        else:
            DIAG["matching_long"] += len(hooks)

        if not hooks:
            DIAG["no_matching"] += 1
            return

        # ----------------------------------------------------
        # Select tradeable hook
        # ----------------------------------------------------

        hook = select_tradeable_hook(
            symbol,
            hooks,
        )

        if hook is None:
            return

        # ----------------------------------------------------
        # TP already touched?
        # ----------------------------------------------------

        if tp_already_touched(
            df,
            hook,
        ):
            DIAG["tp_touched"] += 1
            return

        # ----------------------------------------------------
        # Same symbol open trade
        # ----------------------------------------------------

        if has_open_trade(symbol):
            DIAG["open_blocked"] += 1
            return

        # ----------------------------------------------------
        # Maximum open trades
        # ----------------------------------------------------

        if count_open_trades() >= MAX_OPEN_TRADES:
            DIAG["max_open_blocked"] += 1
            return

        # ----------------------------------------------------
        # Save hook
        # ----------------------------------------------------

        save_hook(
            symbol,
            hook,
        )

        # ----------------------------------------------------
        # Create paper trade
        # ----------------------------------------------------

        create_paper_trade(
            symbol,
            hook,
        )

        DIAG["signals"] += 1

        # ----------------------------------------------------
        # Chart
        # ----------------------------------------------------

        chart_path = save_hook_chart(
            symbol,
            df,
            hook,
        )

        # ----------------------------------------------------
        # Telegram
        # ----------------------------------------------------

        message = build_signal_message(
            symbol,
            hook,
        )

        telegram_send(message)

        if chart_path:
            telegram_photo(
                chart_path,
                (
                    f"NDS {direction} | "
                    f"{symbol} | "
                    f"86.4% TP"
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
        WHERE status IN ('WIN', 'LOSS')
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
        WHERE status IN ('WIN', 'LOSS')
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
        wins / closed * 100
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
    runtime_seconds
):
    p = performance()

    now = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    return (
        f"🔎 <b>NDS H4 → M5 DIAGNOSTIC</b>\n"
        f"Version: <b>{VERSION}</b>\n"
        f"Time: <b>{now} UTC</b>\n"
        f"Runtime: <b>{runtime_seconds:.1f}s</b>\n\n"

        f"━━━ <b>H4</b> ━━━\n"
        f"Requests: {DIAG['h4_requests']}\n"
        f"Data OK: {DIAG['h4_ok']}\n"
        f"Data Error: {DIAG['h4_errors']}\n"
        f"No Valid Hook: {DIAG['h4_no_valid']}\n"
        f"H4 Filtered: {DIAG['h4_filtered']}\n"
        f"SHORT Hooks: {DIAG['h4_short']}\n"
        f"LONG Hooks: {DIAG['h4_long']}\n\n"

        f"━━━ <b>M5</b> ━━━\n"
        f"Requests: {DIAG['m5_requests']}\n"
        f"Data OK: {DIAG['m5_ok']}\n"
        f"Data Error: {DIAG['m5_errors']}\n"
        f"Short Data: {DIAG['m5_short']}\n"
        f"Matching SHORT Hooks: "
        f"{DIAG['matching_short']}\n"
        f"Matching LONG Hooks: "
        f"{DIAG['matching_long']}\n"
        f"No Matching Hook: "
        f"{DIAG['no_matching']}\n"
        f"Stale: {DIAG['stale']}\n"
        f"TP Already Touched: "
        f"{DIAG['tp_touched']}\n"
        f"Duplicate: {DIAG['duplicate']}\n"
        f"Open Trade Blocked: "
        f"{DIAG['open_blocked']}\n"
        f"Max Open Blocked: "
        f"{DIAG['max_open_blocked']}\n\n"

        f"━━━ <b>SIGNALS</b> ━━━\n"
        f"New Signals: "
        f"{DIAG['signals']}\n\n"

        f"━━━ <b>PAPER PERFORMANCE</b> ━━━\n"
        f"Closed: {p['closed']}\n"
        f"Wins: {p['wins']}\n"
        f"Losses: {p['losses']}\n"
        f"Win Rate: {p['win_rate']:.2f}%\n"
        f"Realized PnL: "
        f"{p['pnl']:+.2f}%\n"
        f"Open Trades: {p['open']}\n\n"

        f"🟡 <b>PAPER ONLY</b>\n"
        f"No real exchange orders are sent."
    )


# ============================================================
# MAIN SCAN
# ============================================================

def run_scan():
    init_db()

    start = time.time()

    # --------------------------------------------------------
    # First monitor already-open trades
    # --------------------------------------------------------

    monitor_open_trades()

    # --------------------------------------------------------
    # Scan all assets
    # --------------------------------------------------------

    for symbol in ASSETS:
        process_symbol(symbol)

    runtime = time.time() - start

    diagnostic = build_diagnostic(
        runtime
    )

    print(diagnostic)

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
