# ============================================================
# NDS H4 -> M5 LIVE SCANNER
# VERSION 5.3.0
# ============================================================
#
# PAPER TRADING ONLY
#
# IMPORTANT:
# - No real exchange orders
# - No M1
# - No 123F
# - H4 determines allowed direction
# - M5 finds the same 6-point Hook
# - Entry is H3/L3
# - TP = 86.4%
# - SL = 50% of Entry -> TP distance
# - One scan cycle per GitHub Actions run
# - NO infinite while loop
#
# VERSION 5.3.0 FIXES:
# - Hook freshness is measured from CONFIRMATION time
#   rather than raw H3/L3 pivot time.
# - PIVOT_RIGHT confirmation delay is included.
# - M5 candidates are checked newest -> oldest.
# - A stale newest candidate no longer prevents a newer
#   valid candidate from being considered.
# - Detailed rejection reasons are printed.
# - Signal chart includes all Hook points and 86.4% TP.
#
# ============================================================

import os
import time
import json
import hashlib
import sqlite3
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "5.3.0"

DB_FILE = "nds_h4_m5_live_v51.db"
CHART_DIR = "nds_h4_m5_charts"

PAPER_ONLY = True

KRAKEN_BASE = "https://futures.kraken.com/api/charts/v1/trade"

H4_INTERVAL = "4h"
M5_INTERVAL = "5m"

H4_COUNT = 320
M5_COUNT = 500

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

MIN_SWING_PCT = 0.0005

TP_RETRACE = 0.864

SL_TP_MULTIPLIER = 0.50

MIN_TP_DISTANCE_PCT = 0.30
MAX_TP_DISTANCE_PCT = 5.00

# ------------------------------------------------------------
# IMPORTANT:
# Freshness is measured from the moment H3/L3 becomes
# CONFIRMED, not from the raw pivot candle time.
#
# M5 pivot confirmation:
# 2 candles x 5 minutes = approximately 10 minutes.
# ------------------------------------------------------------

MAX_NEW_SIGNAL_AGE_SECONDS = 20 * 60

MAX_OPEN_TRADES = 3

CHART_CANDLES = 180

REQUEST_TIMEOUT = 12


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
# SESSION
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent": "NDS-H4-M5-Scanner/5.3.0",
        "Accept": "application/json",
    }
)


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS hooks (
            hook_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,
            start_time TEXT NOT NULL,
            entry_time TEXT NOT NULL,
            start_price REAL NOT NULL,
            entry_price REAL NOT NULL,
            tp_price REAL NOT NULL,
            sl_price REAL NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hook_id TEXT UNIQUE,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_time TEXT NOT NULL,
            entry_price REAL NOT NULL,
            tp_price REAL NOT NULL,
            sl_price REAL NOT NULL,
            exit_time TEXT,
            exit_price REAL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            pnl_pct REAL,
            close_reason TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    conn.commit()
    conn.close()


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat()


def normalize_timestamp(ts):
    t = pd.Timestamp(ts)

    if t.tzinfo is None:
        return t.tz_localize("UTC")

    return t.tz_convert("UTC")


def ts_to_iso(ts):
    return normalize_timestamp(ts).isoformat()


def interval_minutes(interval):
    mapping = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "4h": 240,
    }

    return mapping.get(interval, 5)


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials not configured.")
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
        r = session.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        if r.status_code != 200:
            print(
                "Telegram error:",
                r.status_code,
                r.text[:500],
            )
            return False

        return True

    except Exception as e:
        print(
            "Telegram exception:",
            repr(e),
        )
        return False


def telegram_photo(path, caption=""):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    if not os.path.exists(path):
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
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

        if r.status_code != 200:
            print(
                "Telegram photo error:",
                r.status_code,
                r.text[:500],
            )
            return False

        return True

    except Exception as e:
        print(
            "Telegram photo exception:",
            repr(e),
        )
        return False


# ============================================================
# KRAKEN DATA
# ============================================================

def fetch_candles(symbol, interval, count):
    url = f"{KRAKEN_BASE}/{symbol}/{interval}"

    try:
        r = session.get(
            url,
            timeout=REQUEST_TIMEOUT,
        )

        if r.status_code != 200:
            print(
                f"[DATA] {symbol} {interval} "
                f"HTTP {r.status_code}"
            )
            return None

        data = r.json()

        if isinstance(data, dict):
            rows = (
                data.get("candles")
                or data.get("data")
                or data.get("result")
                or []
            )
        else:
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

                    o = row.get("open")
                    h = row.get("high")
                    l = row.get("low")
                    c = row.get("close")
                    v = row.get("volume", 0)

                elif isinstance(row, (list, tuple)):
                    if len(row) < 5:
                        continue

                    ts = row[0]
                    o = row[1]
                    h = row[2]
                    l = row[3]
                    c = row[4]
                    v = (
                        row[5]
                        if len(row) > 5
                        else 0
                    )

                else:
                    continue

                if ts is None:
                    continue

                ts_num = float(ts)

                if ts_num > 10_000_000_000:
                    ts_num /= 1000.0

                parsed.append(
                    [
                        pd.to_datetime(
                            ts_num,
                            unit="s",
                            utc=True,
                        ),
                        float(o),
                        float(h),
                        float(l),
                        float(c),
                        float(v or 0),
                    ]
                )

            except Exception:
                continue

        if not parsed:
            return None

        df = pd.DataFrame(
            parsed,
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
            df.drop_duplicates(
                subset=["time"]
            )
            .sort_values("time")
            .tail(count)
            .reset_index(drop=True)
        )

        return df

    except Exception as e:
        print(
            f"[DATA ERROR] {symbol} {interval}: "
            f"{repr(e)}"
        )
        return None


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(df):
    highs = []
    lows = []

    n = len(df)

    if n < PIVOT_LEFT + PIVOT_RIGHT + 1:
        return highs, lows

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT,
    ):
        current_high = float(
            df.iloc[i]["high"]
        )

        current_low = float(
            df.iloc[i]["low"]
        )

        left_highs = df.iloc[
            i - PIVOT_LEFT:i
        ]["high"].astype(float)

        right_highs = df.iloc[
            i + 1:i + PIVOT_RIGHT + 1
        ]["high"].astype(float)

        left_lows = df.iloc[
            i - PIVOT_LEFT:i
        ]["low"].astype(float)

        right_lows = df.iloc[
            i + 1:i + PIVOT_RIGHT + 1
        ]["low"].astype(float)

        if (
            current_high >= left_highs.max()
            and current_high >= right_highs.max()
        ):
            highs.append(
                {
                    "index": i,
                    "time": normalize_timestamp(
                        df.iloc[i]["time"]
                    ),
                    "price": current_high,
                    "type": "H",
                }
            )

        if (
            current_low <= left_lows.min()
            and current_low <= right_lows.min()
        ):
            lows.append(
                {
                    "index": i,
                    "time": normalize_timestamp(
                        df.iloc[i]["time"]
                    ),
                    "price": current_low,
                    "type": "L",
                }
            )

    return highs, lows


# ============================================================
# PIVOT MERGING
# ============================================================

def build_ordered_pivots(highs, lows):
    pivots = []

    for p in highs:
        pivots.append(dict(p))

    for p in lows:
        pivots.append(dict(p))

    pivots.sort(
        key=lambda x: (
            x["index"],
            0 if x["type"] == "L" else 1,
        )
    )

    cleaned = []

    for p in pivots:
        if not cleaned:
            cleaned.append(p)
            continue

        last = cleaned[-1]

        if p["type"] != last["type"]:
            cleaned.append(p)
            continue

        if p["type"] == "H":
            if p["price"] >= last["price"]:
                cleaned[-1] = p

        else:
            if p["price"] <= last["price"]:
                cleaned[-1] = p

    return cleaned


# ============================================================
# SWING
# ============================================================

def pct_distance(a, b):
    if a == 0:
        return 0.0

    return (
        abs(a - b)
        / abs(a)
        * 100.0
    )


def valid_swing(a, b):
    return pct_distance(
        a,
        b,
    ) >= (
        MIN_SWING_PCT * 100.0
    )


# ============================================================
# HOOK ID
# ============================================================

def make_hook_id(
    symbol,
    timeframe,
    direction,
    points,
):
    raw = (
        f"{symbol}|{timeframe}|{direction}|"
        + "|".join(
            f"{p['type']}:{p['index']}:"
            f"{p['price']:.12f}"
            for p in points
        )
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


# ============================================================
# HOOK CONFIRMATION TIME
# ============================================================

def hook_confirmation_time(hook):
    """
    H3/L3 is only confirmed after PIVOT_RIGHT candles.

    For M5:
        2 right candles = approximately 10 minutes.

    Therefore freshness must start from this confirmation
    point, not from the raw pivot candle.
    """

    entry_time = normalize_timestamp(
        hook["entry_time"]
    )

    minutes = (
        PIVOT_RIGHT
        * interval_minutes(
            hook["timeframe"]
        )
    )

    return (
        entry_time
        + pd.Timedelta(
            minutes=minutes
        )
    )


# ============================================================
# HOOK VALIDATION
# ============================================================

def calculate_hook(
    symbol,
    timeframe,
    points,
):
    if len(points) != 6:
        return None

    p0, p1, p2, p3, p4, p5 = points

    # ========================================================
    # POSITIVE HOOK = SHORT
    #
    # START -> H1 -> L1 -> H2 -> L2 -> H3
    #
    # START is the lowest point.
    #
    # H2 > H1
    # L2 < L1
    # H3 > H2
    #
    # Entry = H3
    # TP = 86.4% from H3 toward START
    # SL = 50% of Entry -> TP distance above Entry
    # ========================================================

    if (
        p0["type"] == "L"
        and p1["type"] == "H"
        and p2["type"] == "L"
        and p3["type"] == "H"
        and p4["type"] == "L"
        and p5["type"] == "H"
    ):
        start = p0["price"]
        h1 = p1["price"]
        l1 = p2["price"]
        h2 = p3["price"]
        l2 = p4["price"]
        h3 = p5["price"]

        if not (
            start < h1
            and start < l1
            and start < h2
            and start < l2
            and start < h3
        ):
            return None

        if not (
            h2 > h1
            and l2 < l1
            and h3 > h2
        ):
            return None

        if not (
            valid_swing(start, h1)
            and valid_swing(h1, l1)
            and valid_swing(l1, h2)
            and valid_swing(h2, l2)
            and valid_swing(l2, h3)
        ):
            return None

        entry = h3

        tp = (
            h3
            - TP_RETRACE
            * (h3 - start)
        )

        sl = (
            entry
            + SL_TP_MULTIPLIER
            * abs(entry - tp)
        )

        direction = "SHORT"

    # ========================================================
    # NEGATIVE HOOK = LONG
    #
    # START -> L1 -> H1 -> L2 -> H2 -> L3
    #
    # START is the highest point.
    #
    # L2 < L1
    # H2 > H1
    # L3 < L2
    #
    # Entry = L3
    # TP = 86.4% from L3 toward START
    # SL = 50% of Entry -> TP distance below Entry
    # ========================================================

    elif (
        p0["type"] == "H"
        and p1["type"] == "L"
        and p2["type"] == "H"
        and p3["type"] == "L"
        and p4["type"] == "H"
        and p5["type"] == "L"
    ):
        start = p0["price"]
        l1 = p1["price"]
        h1 = p2["price"]
        l2 = p3["price"]
        h2 = p4["price"]
        l3 = p5["price"]

        if not (
            start > h1
            and start > l1
            and start > h2
            and start > l2
            and start > l3
        ):
            return None

        if not (
            l2 < l1
            and h2 > h1
            and l3 < l2
        ):
            return None

        if not (
            valid_swing(start, l1)
            and valid_swing(l1, h1)
            and valid_swing(h1, l2)
            and valid_swing(l2, h2)
            and valid_swing(h2, l3)
        ):
            return None

        entry = l3

        tp = (
            l3
            + TP_RETRACE
            * (start - l3)
        )

        sl = (
            entry
            - SL_TP_MULTIPLIER
            * abs(entry - tp)
        )

        direction = "LONG"

    else:
        return None

    tp_distance_pct = pct_distance(
        entry,
        tp,
    )

    if (
        tp_distance_pct
        < MIN_TP_DISTANCE_PCT
    ):
        return None

    if (
        tp_distance_pct
        > MAX_TP_DISTANCE_PCT
    ):
        return None

    hook_id = make_hook_id(
        symbol,
        timeframe,
        direction,
        points,
    )

    confirmation_time = (
        normalize_timestamp(
            p5["time"]
        )
        + pd.Timedelta(
            minutes=(
                PIVOT_RIGHT
                * interval_minutes(
                    timeframe
                )
            )
        )
    )

    return {
        "hook_id": hook_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "points": points,
        "start_time": normalize_timestamp(
            p0["time"]
        ),
        "entry_time": normalize_timestamp(
            p5["time"]
        ),
        "confirmation_time": confirmation_time,
        "start_price": float(start),
        "entry_price": float(entry),
        "tp_price": float(tp),
        "sl_price": float(sl),
        "tp_distance_pct": tp_distance_pct,
    }


# ============================================================
# DETECT HOOKS
# ============================================================

def detect_hooks(
    symbol,
    timeframe,
    df,
    wanted_direction=None,
):
    highs, lows = find_pivots(df)

    pivots = build_ordered_pivots(
        highs,
        lows,
    )

    hooks = []

    if len(pivots) < 6:
        return {
            "high_count": len(highs),
            "low_count": len(lows),
            "pivot_count": len(pivots),
            "hooks": [],
        }

    for i in range(
        len(pivots) - 5
    ):
        points = pivots[i:i + 6]

        hook = calculate_hook(
            symbol,
            timeframe,
            points,
        )

        if hook is None:
            continue

        if (
            wanted_direction is not None
            and hook["direction"]
            != wanted_direction
        ):
            continue

        hooks.append(hook)

    hooks.sort(
        key=lambda x: (
            x["confirmation_time"],
            x["entry_time"],
        )
    )

    return {
        "high_count": len(highs),
        "low_count": len(lows),
        "pivot_count": len(pivots),
        "hooks": hooks,
    }


# ============================================================
# TP ALREADY TOUCHED
# ============================================================

def tp_already_touched(
    df,
    entry_index,
    direction,
    tp_price,
):
    if entry_index is None:
        return False

    if entry_index + 1 >= len(df):
        return False

    future = df.iloc[
        entry_index + 1:
    ]

    if direction == "SHORT":
        return bool(
            (
                future["low"]
                <= tp_price
            ).any()
        )

    return bool(
        (
            future["high"]
            >= tp_price
        ).any()
    )


# ============================================================
# FRESHNESS
# ============================================================

def hook_age_seconds(hook):
    now = utc_now()

    confirmation_time = (
        normalize_timestamp(
            hook["confirmation_time"]
        )
    )

    return (
        now
        - confirmation_time.to_pydatetime()
    ).total_seconds()


def is_fresh_hook(hook):
    age = hook_age_seconds(hook)

    return (
        age >= -60
        and age <= MAX_NEW_SIGNAL_AGE_SECONDS
    )


# ============================================================
# DATABASE HELPERS
# ============================================================

def hook_exists(hook_id):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT hook_id
        FROM hooks
        WHERE hook_id = ?
        LIMIT 1
        """,
        (hook_id,),
    ).fetchone()

    conn.close()

    return row is not None


def save_hook(hook):
    conn = db_connect()

    conn.execute(
        """
        INSERT OR IGNORE INTO hooks (
            hook_id,
            symbol,
            timeframe,
            direction,
            start_time,
            entry_time,
            start_price,
            entry_price,
            tp_price,
            sl_price,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            hook["hook_id"],
            hook["symbol"],
            hook["timeframe"],
            hook["direction"],
            ts_to_iso(
                hook["start_time"]
            ),
            ts_to_iso(
                hook["entry_time"]
            ),
            hook["start_price"],
            hook["entry_price"],
            hook["tp_price"],
            hook["sl_price"],
            iso_now(),
        ),
    )

    inserted = conn.total_changes > 0

    conn.commit()
    conn.close()

    return inserted


def trade_exists(hook_id):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
        FROM paper_trades
        WHERE hook_id = ?
        LIMIT 1
        """,
        (hook_id,),
    ).fetchone()

    conn.close()

    return row is not None


def get_open_trades():
    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM paper_trades
        WHERE status = 'OPEN'
        ORDER BY id ASC
        """
    ).fetchall()

    conn.close()

    return rows


def has_open_trade(symbol):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
        FROM paper_trades
        WHERE symbol = ?
          AND status = 'OPEN'
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()

    conn.close()

    return row is not None


def create_paper_trade(hook):
    if not PAPER_ONLY:
        raise RuntimeError(
            "SAFETY ERROR: PAPER_ONLY must remain True."
        )

    conn = db_connect()

    try:
        conn.execute(
            """
            INSERT INTO paper_trades (
                hook_id,
                symbol,
                direction,
                entry_time,
                entry_price,
                tp_price,
                sl_price,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
            """,
            (
                hook["hook_id"],
                hook["symbol"],
                hook["direction"],
                ts_to_iso(
                    hook["entry_time"]
                ),
                hook["entry_price"],
                hook["tp_price"],
                hook["sl_price"],
                iso_now(),
            ),
        )

        conn.commit()
        return True

    except sqlite3.IntegrityError:
        conn.rollback()
        return False

    finally:
        conn.close()


def close_trade(
    trade_id,
    exit_time,
    exit_price,
    pnl_pct,
    reason,
):
    conn = db_connect()

    conn.execute(
        """
        UPDATE paper_trades
        SET
            exit_time = ?,
            exit_price = ?,
            status = 'CLOSED',
            pnl_pct = ?,
            close_reason = ?
        WHERE id = ?
        """,
        (
            exit_time,
            exit_price,
            pnl_pct,
            reason,
            trade_id,
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# PNL
# ============================================================

def calculate_pnl(
    direction,
    entry,
    exit,
):
    if entry == 0:
        return 0.0

    if direction == "LONG":
        return (
            (exit - entry)
            / entry
            * 100.0
        )

    return (
        (entry - exit)
        / entry
        * 100.0
    )


# ============================================================
# MONITOR OPEN TRADES
# ============================================================

def monitor_open_trades():
    trades = get_open_trades()

    stats = {
        "checked": len(trades),
        "closed": 0,
        "wins": 0,
        "losses": 0,
        "errors": 0,
    }

    if not trades:
        return stats

    print(
        f"\n[OPEN TRADES] "
        f"Monitoring {len(trades)} trade(s)"
    )

    for trade in trades:
        symbol = trade["symbol"]

        df = fetch_candles(
            symbol,
            M5_INTERVAL,
            10,
        )

        if df is None or df.empty:
            stats["errors"] += 1

            print(
                f"[OPEN] {symbol}: "
                f"no M5 data"
            )

            continue

        last = df.iloc[-1]

        high = float(last["high"])
        low = float(last["low"])

        direction = trade["direction"]

        tp = float(trade["tp_price"])
        sl = float(trade["sl_price"])
        entry = float(trade["entry_price"])

        exit_price = None
        reason = None

        # Conservative rule:
        # If both SL and TP are touched in same candle,
        # assume SL happened first.

        if direction == "LONG":
            if low <= sl:
                exit_price = sl
                reason = "SL"

            elif high >= tp:
                exit_price = tp
                reason = "TP"

        else:
            if high >= sl:
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
            exit_price,
        )

        close_trade(
            trade["id"],
            ts_to_iso(last["time"]),
            exit_price,
            pnl,
            reason,
        )

        stats["closed"] += 1

        if reason == "TP":
            stats["wins"] += 1
        else:
            stats["losses"] += 1

        emoji = (
            "🟢"
            if reason == "TP"
            else "🔴"
        )

        text = (
            f"{emoji} "
            f"<b>NDS TRADE CLOSED</b>\n"
            f"Symbol: <b>{symbol}</b>\n"
            f"Direction: <b>{direction}</b>\n"
            f"Entry: <b>{entry:.8g}</b>\n"
            f"Exit: <b>{exit_price:.8g}</b>\n"
            f"Reason: <b>{reason}</b>\n"
            f"PnL: <b>{pnl:+.2f}%</b>"
        )

        telegram_send(text)

        print(
            f"[CLOSED] {symbol} "
            f"{direction} {reason} "
            f"PnL={pnl:+.2f}%"
        )

    return stats


# ============================================================
# CHART
# ============================================================

def save_hook_chart(
    symbol,
    timeframe,
    df,
    hook,
    path,
):
    os.makedirs(
        CHART_DIR,
        exist_ok=True,
    )

    chart_df = df.tail(
        CHART_CANDLES
    ).copy()

    chart_df["time"] = pd.to_datetime(
        chart_df["time"],
        utc=True,
    )

    fig, ax = plt.subplots(
        figsize=(16, 9)
    )

    # --------------------------------------------------------
    # Price
    # --------------------------------------------------------

    ax.plot(
        chart_df["time"],
        chart_df["close"],
        linewidth=1.2,
        label=f"{symbol} {timeframe}",
    )

    points = hook["points"]

    if hook["direction"] == "SHORT":
        point_names = [
            "START",
            "H1",
            "L1",
            "H2",
            "L2",
            "H3 ENTRY",
        ]
    else:
        point_names = [
            "START",
            "L1",
            "H1",
            "L2",
            "H2",
            "L3 ENTRY",
        ]

    # --------------------------------------------------------
    # Hook points
    # --------------------------------------------------------

    for idx, p in enumerate(points):
        x = normalize_timestamp(
            p["time"]
        )

        y = float(p["price"])

        ax.scatter(
            [x],
            [y],
            s=75,
            zorder=5,
        )

        if idx == 5:
            offset = 24
        elif idx % 2 == 0:
            offset = 16
        else:
            offset = -24

        ax.annotate(
            (
                f"{point_names[idx]}\n"
                f"{y:.8g}"
            ),
            xy=(x, y),
            xytext=(0, offset),
            textcoords="offset points",
            ha="center",
            fontsize=9,
            fontweight="bold",
        )

    # --------------------------------------------------------
    # Connect Hook points
    # --------------------------------------------------------

    hook_x = [
        normalize_timestamp(
            p["time"]
        )
        for p in points
    ]

    hook_y = [
        float(p["price"])
        for p in points
    ]

    ax.plot(
        hook_x,
        hook_y,
        linewidth=1.5,
        linestyle=":",
        label="NDS HOOK",
    )

    # --------------------------------------------------------
    # Entry / TP / SL
    # --------------------------------------------------------

    entry = float(
        hook["entry_price"]
    )

    tp = float(
        hook["tp_price"]
    )

    sl = float(
        hook["sl_price"]
    )

    ax.axhline(
        entry,
        linestyle="--",
        linewidth=1.0,
        label=f"ENTRY {entry:.8g}",
    )

    ax.axhline(
        tp,
        linestyle="--",
        linewidth=1.2,
        label=f"TP 86.4% {tp:.8g}",
    )

    ax.axhline(
        sl,
        linestyle="--",
        linewidth=1.0,
        label=f"SL {sl:.8g}",
    )

    # --------------------------------------------------------
    # Confirmation marker
    # --------------------------------------------------------

    confirmation = normalize_timestamp(
        hook["confirmation_time"]
    )

    ax.axvline(
        confirmation,
        linestyle=":",
        linewidth=0.8,
        alpha=0.7,
        label="H3/L3 CONFIRMED",
    )

    # --------------------------------------------------------
    # Title
    # --------------------------------------------------------

    ax.set_title(
        f"NDS {timeframe} {symbol} "
        f"{hook['direction']} Hook\n"
        f"Entry={entry:.8g} | "
        f"TP 86.4%={tp:.8g} | "
        f"SL={sl:.8g}"
    )

    ax.set_xlabel(
        "UTC"
    )

    ax.set_ylabel(
        "Price"
    )

    ax.grid(
        True,
        alpha=0.25,
    )

    ax.legend(
        loc="best"
    )

    fig.autofmt_xdate()

    plt.tight_layout()

    fig.savefig(
        path,
        dpi=150,
        bbox_inches="tight",
    )

    plt.close(fig)


# ============================================================
# PERFORMANCE
# ============================================================

def performance_stats():
    conn = db_connect()

    closed = conn.execute(
        """
        SELECT
            COUNT(*) AS count,
            COALESCE(
                SUM(pnl_pct),
                0
            ) AS pnl,
            SUM(
                CASE
                    WHEN pnl_pct > 0
                    THEN 1
                    ELSE 0
                END
            ) AS wins,
            SUM(
                CASE
                    WHEN pnl_pct <= 0
                    THEN 1
                    ELSE 0
                END
            ) AS losses
        FROM paper_trades
        WHERE status = 'CLOSED'
        """
    ).fetchone()

    open_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM paper_trades
        WHERE status = 'OPEN'
        """
    ).fetchone()[0]

    conn.close()

    total = int(
        closed["count"] or 0
    )

    wins = int(
        closed["wins"] or 0
    )

    losses = int(
        closed["losses"] or 0
    )

    pnl = float(
        closed["pnl"] or 0.0
    )

    win_rate = (
        wins / total * 100.0
        if total
        else 0.0
    )

    return {
        "closed": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "pnl": pnl,
        "open": int(open_count),
    }


# ============================================================
# H4 STATE
# ============================================================

def determine_h4_state(
    symbol,
    df,
):
    result = detect_hooks(
        symbol,
        H4_INTERVAL,
        df,
        wanted_direction=None,
    )

    hooks = result["hooks"]

    valid_short = [
        h
        for h in hooks
        if h["direction"] == "SHORT"
    ]

    valid_long = [
        h
        for h in hooks
        if h["direction"] == "LONG"
    ]

    latest_short = (
        valid_short[-1]
        if valid_short
        else None
    )

    latest_long = (
        valid_long[-1]
        if valid_long
        else None
    )

    return {
        "result": result,
        "short_hooks": valid_short,
        "long_hooks": valid_long,
        "latest_short": latest_short,
        "latest_long": latest_long,
    }


# ============================================================
# FIND ENTRY INDEX
# ============================================================

def find_entry_index(
    df,
    hook,
):
    target_time = normalize_timestamp(
        hook["entry_time"]
    )

    for i in range(len(df)):
        candle_time = normalize_timestamp(
            df.iloc[i]["time"]
        )

        if candle_time == target_time:
            return i

    return None


# ============================================================
# SELECT FRESH M5 HOOK
# ============================================================

def select_tradeable_hook(
    matching_hooks,
    symbol,
    stats,
):
    if not matching_hooks:
        return None

    # Newest first.
    candidates = sorted(
        matching_hooks,
        key=lambda h: (
            h["confirmation_time"],
            h["entry_time"],
        ),
        reverse=True,
    )

    for hook in candidates:
        age = hook_age_seconds(
            hook
        )

        print(
            f"[CANDIDATE] {symbol} "
            f"{hook['direction']} "
            f"Entry={hook['entry_price']:.8g} "
            f"Confirm="
            f"{ts_to_iso(hook['confirmation_time'])} "
            f"Age={age:.1f}s"
        )

        # ----------------------------------------------------
        # Already processed?
        # ----------------------------------------------------

        if hook_exists(
            hook["hook_id"]
        ):
            if trade_exists(
                hook["hook_id"]
            ):
                stats["duplicate"] += 1

                print(
                    f"[REJECT] {symbol}: "
                    f"DUPLICATE "
                    f"hook={hook['hook_id'][:10]}"
                )

                continue

        # ----------------------------------------------------
        # Freshness
        # ----------------------------------------------------

        if not is_fresh_hook(
            hook
        ):
            stats["m5_stale"] += 1

            print(
                f"[REJECT] {symbol}: "
                f"STALE "
                f"age={age:.1f}s "
                f"limit="
                f"{MAX_NEW_SIGNAL_AGE_SECONDS}s"
            )

            continue

        return hook

    return None


# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(
    symbol,
    h4_state,
    stats,
):
    h4_short = h4_state["latest_short"]
    h4_long = h4_state["latest_long"]

    if (
        h4_short is None
        and h4_long is None
    ):
        stats["h4_no_valid"] += 1

        print(
            f"[FILTER] {symbol}: "
            f"H4 NO VALID HOOK"
        )

        return

    # --------------------------------------------------------
    # Select latest H4 direction
    # --------------------------------------------------------

    if (
        h4_short is not None
        and h4_long is not None
    ):
        if (
            h4_short["entry_time"]
            >= h4_long["entry_time"]
        ):
            h4_hook = h4_short
        else:
            h4_hook = h4_long

    elif h4_short is not None:
        h4_hook = h4_short

    else:
        h4_hook = h4_long

    allowed_direction = (
        h4_hook["direction"]
    )

    stats["h4_filtered"] += 1

    print(
        f"[FILTER] {symbol}: "
        f"H4={allowed_direction}"
    )

    # --------------------------------------------------------
    # M5
    # --------------------------------------------------------

    m5 = fetch_candles(
        symbol,
        M5_INTERVAL,
        M5_COUNT,
    )

    stats["m5_requests"] += 1

    if m5 is None or m5.empty:
        stats["m5_data_error"] += 1

        print(
            f"[M5] {symbol}: "
            f"DATA ERROR"
        )

        return

    if len(m5) < 30:
        stats["m5_short_data"] += 1

        print(
            f"[M5] {symbol}: "
            f"SHORT DATA "
            f"({len(m5)})"
        )

        return

    stats["m5_ok"] += 1

    result = detect_hooks(
        symbol,
        M5_INTERVAL,
        m5,
        wanted_direction=allowed_direction,
    )

    matching_hooks = result["hooks"]

    if allowed_direction == "SHORT":
        stats["m5_matching_short"] += len(
            matching_hooks
        )
    else:
        stats["m5_matching_long"] += len(
            matching_hooks
        )

    if not matching_hooks:
        stats["m5_no_matching_hook"] += 1

        print(
            f"[M5] {symbol}: "
            f"NO MATCHING "
            f"{allowed_direction} HOOK"
        )

        return

    print(
        f"[M5] {symbol}: "
        f"{len(matching_hooks)} "
        f"MATCHING {allowed_direction} "
        f"HOOK(S)"
    )

    # --------------------------------------------------------
    # Select a fresh, unprocessed Hook.
    # --------------------------------------------------------

    hook = select_tradeable_hook(
        matching_hooks,
        symbol,
        stats,
    )

    if hook is None:
        print(
            f"[M5] {symbol}: "
            f"NO TRADEABLE FRESH HOOK"
        )

        return

    # --------------------------------------------------------
    # Save Hook
    # --------------------------------------------------------

    save_hook(hook)

    # --------------------------------------------------------
    # Open trade per symbol
    # --------------------------------------------------------

    if has_open_trade(symbol):
        stats["open_trade_blocked"] += 1

        print(
            f"[REJECT] {symbol}: "
            f"OPEN TRADE EXISTS"
        )

        return

    # --------------------------------------------------------
    # Max open trades
    # --------------------------------------------------------

    current_open = len(
        get_open_trades()
    )

    if current_open >= MAX_OPEN_TRADES:
        stats["max_open_blocked"] += 1

        print(
            f"[REJECT] {symbol}: "
            f"MAX OPEN TRADES "
            f"{current_open}/"
            f"{MAX_OPEN_TRADES}"
        )

        return

    # --------------------------------------------------------
    # Entry index
    # --------------------------------------------------------

    entry_idx = find_entry_index(
        m5,
        hook,
    )

    # --------------------------------------------------------
    # TP already touched
    # --------------------------------------------------------

    if tp_already_touched(
        m5,
        entry_idx,
        hook["direction"],
        hook["tp_price"],
    ):
        stats["tp_already_touched"] += 1

        print(
            f"[REJECT] {symbol}: "
            f"TP ALREADY TOUCHED "
            f"after Entry"
        )

        return

    # --------------------------------------------------------
    # Create paper trade
    # --------------------------------------------------------

    created = create_paper_trade(
        hook
    )

    if not created:
        stats["duplicate"] += 1

        print(
            f"[REJECT] {symbol}: "
            f"TRADE ALREADY EXISTS"
        )

        return

    stats["signals"] += 1

    direction = hook["direction"]

    emoji = (
        "🔴"
        if direction == "SHORT"
        else "🟢"
    )

    entry = float(
        hook["entry_price"]
    )

    tp = float(
        hook["tp_price"]
    )

    sl = float(
        hook["sl_price"]
    )

    chart_name = (
        f"{symbol}_M5_"
        f"{direction}_"
        f"{int(time.time())}.png"
    )

    chart_path = os.path.join(
        CHART_DIR,
        chart_name,
    )

    save_hook_chart(
        symbol,
        M5_INTERVAL,
        m5,
        hook,
        chart_path,
    )

    signal_text = (
        f"{emoji} "
        f"<b>NDS M5 SIGNAL</b>\n"
        f"Symbol: <b>{symbol}</b>\n"
        f"Direction: <b>{direction}</b>\n"
        f"Entry: <b>{entry:.8g}</b>\n"
        f"SL: <b>{sl:.8g}</b>\n"
        f"TP 86.4%: <b>{tp:.8g}</b>\n"
        f"TP Distance: <b>"
        f"{hook['tp_distance_pct']:.2f}%"
        f"</b>\n"
        f"H4 Filter: <b>"
        f"{allowed_direction}"
        f"</b>\n"
        f"Hook Confirmed: <b>"
        f"{ts_to_iso(hook['confirmation_time'])}"
        f"</b>\n"
        f"Mode: <b>PAPER</b>"
    )

    telegram_send(
        signal_text
    )

    telegram_photo(
        chart_path,
        caption=(
            f"NDS M5 {direction} | "
            f"{symbol} | "
            f"Entry "
            f"{entry:.8g} | "
            f"TP 86.4% "
            f"{tp:.8g}"
        ),
    )

    print(
        f"[SIGNAL] {symbol} "
        f"{direction} "
        f"Entry={entry:.8g} "
        f"TP={tp:.8g} "
        f"SL={sl:.8g}"
    )


# ============================================================
# DIAGNOSTIC
# ============================================================

def build_diagnostic(
    stats,
    performance,
    runtime_seconds,
):
    now = utc_now().strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    lines = [
        "🔎 <b>NDS H4 → M5 DIAGNOSTIC</b>",
        f"Version: <b>{VERSION}</b>",
        f"Time: <b>{now}</b>",
        f"Runtime: <b>{runtime_seconds:.1f}s</b>",
        "",
        "━━━ <b>H4</b> ━━━",
        f"Requests: <b>{stats['h4_requests']}</b>",
        f"Data OK: <b>{stats['h4_ok']}</b>",
        f"Data Error: <b>{stats['h4_data_error']}</b>",
        f"No Valid Hook: <b>{stats['h4_no_valid']}</b>",
        f"H4 Filtered: <b>{stats['h4_filtered']}</b>",
        f"SHORT Hooks: <b>{stats['h4_short_hooks']}</b>",
        f"LONG Hooks: <b>{stats['h4_long_hooks']}</b>",
        "",
        "━━━ <b>M5</b> ━━━",
        f"Requests: <b>{stats['m5_requests']}</b>",
        f"Data OK: <b>{stats['m5_ok']}</b>",
        f"Data Error: <b>{stats['m5_data_error']}</b>",
        f"Short Data: <b>{stats['m5_short_data']}</b>",
        f"Matching SHORT Hooks: <b>{stats['m5_matching_short']}</b>",
        f"Matching LONG Hooks: <b>{stats['m5_matching_long']}</b>",
        f"No Matching Hook: <b>{stats['m5_no_matching_hook']}</b>",
        f"Stale: <b>{stats['m5_stale']}</b>",
        f"TP Already Touched: <b>{stats['tp_already_touched']}</b>",
        f"Duplicate: <b>{stats['duplicate']}</b>",
        f"Open Trade Blocked: <b>{stats['open_trade_blocked']}</b>",
        f"Max Open Blocked: <b>{stats['max_open_blocked']}</b>",
        "",
        "━━━ <b>SIGNALS</b> ━━━",
        f"New Signals: <b>{stats['signals']}</b>",
        "",
        "━━━ <b>PAPER PERFORMANCE</b> ━━━",
        f"Closed: <b>{performance['closed']}</b>",
        f"Wins: <b>{performance['wins']}</b>",
        f"Losses: <b>{performance['losses']}</b>",
        f"Win Rate: <b>{performance['win_rate']:.2f}%</b>",
        f"Realized PnL: <b>{performance['pnl']:+.2f}%</b>",
        f"Open Trades: <b>{performance['open']}</b>",
        "",
        "🟡 <b>PAPER ONLY</b>",
        "No real exchange orders are sent.",
    ]

    return "\n".join(lines)


# ============================================================
# MAIN SCAN
# ============================================================

def run_scan():
    start_time = time.time()

    init_db()

    stats = {
        "h4_requests": 0,
        "h4_ok": 0,
        "h4_data_error": 0,
        "h4_no_valid": 0,
        "h4_filtered": 0,
        "h4_short_hooks": 0,
        "h4_long_hooks": 0,

        "m5_requests": 0,
        "m5_ok": 0,
        "m5_data_error": 0,
        "m5_short_data": 0,
        "m5_matching_short": 0,
        "m5_matching_long": 0,
        "m5_no_matching_hook": 0,
        "m5_stale": 0,

        "tp_already_touched": 0,
        "duplicate": 0,
        "open_trade_blocked": 0,
        "max_open_blocked": 0,

        "signals": 0,
    }

    print("=" * 70)

    print(
        "NDS H4 -> M5 LIVE SCANNER"
    )

    print(
        f"VERSION {VERSION}"
    )

    print(
        "PAPER ONLY"
    )

    print(
        "ONE SCAN CYCLE"
    )

    print(
        "FRESHNESS = H3/L3 CONFIRMATION TIME"
    )

    print("=" * 70)

    # --------------------------------------------------------
    # 1. MONITOR OPEN TRADES
    # --------------------------------------------------------

    open_stats = monitor_open_trades()

    print(
        "\n[OPEN TRADE STATS]",
        json.dumps(
            open_stats,
            indent=2,
        ),
    )

    # --------------------------------------------------------
    # 2. H4 SCAN
    # --------------------------------------------------------

    h4_states = {}

    print(
        f"\n[H4] Scanning "
        f"{len(ASSETS)} assets..."
    )

    for symbol in ASSETS:
        stats["h4_requests"] += 1

        df = fetch_candles(
            symbol,
            H4_INTERVAL,
            H4_COUNT,
        )

        if df is None or df.empty:
            stats["h4_data_error"] += 1

            print(
                f"[H4] {symbol}: "
                f"DATA ERROR"
            )

            continue

        if len(df) < 30:
            stats["h4_data_error"] += 1

            print(
                f"[H4] {symbol}: "
                f"SHORT DATA "
                f"({len(df)})"
            )

            continue

        stats["h4_ok"] += 1

        state = determine_h4_state(
            symbol,
            df,
        )

        h4_states[symbol] = {
            "state": state,
            "df": df,
        }

        stats["h4_short_hooks"] += len(
            state["short_hooks"]
        )

        stats["h4_long_hooks"] += len(
            state["long_hooks"]
        )

        print(
            f"[H4] {symbol}: "
            f"pivots="
            f"{state['result']['pivot_count']} "
            f"SHORT="
            f"{len(state['short_hooks'])} "
            f"LONG="
            f"{len(state['long_hooks'])}"
        )

    # --------------------------------------------------------
    # 3. M5 SCAN
    # --------------------------------------------------------

    print(
        "\n[M5] Scanning "
        "H4-filtered assets..."
    )

    for symbol in ASSETS:
        if symbol not in h4_states:
            continue

        state = h4_states[
            symbol
        ]["state"]

        process_symbol(
            symbol,
            state,
            stats,
        )

    # --------------------------------------------------------
    # 4. FINAL PERFORMANCE
    # --------------------------------------------------------

    performance = performance_stats()

    runtime = (
        time.time()
        - start_time
    )

    report = build_diagnostic(
        stats,
        performance,
        runtime,
    )

    print(
        "\n" + "=" * 70
    )

    print(
        report.replace(
            "<b>",
            "",
        ).replace(
            "</b>",
            "",
        )
    )

    print(
        "=" * 70
    )

    telegram_send(report)

    return {
        "stats": stats,
        "performance": performance,
        "runtime": runtime,
    }


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        result = run_scan()

        print(
            "\nSCAN COMPLETED SUCCESSFULLY."
        )

        print(
            json.dumps(
                result,
                indent=2,
                default=str,
            )
        )

    except KeyboardInterrupt:
        print(
            "\nScanner interrupted."
        )

    except Exception as e:
        print(
            "\nFATAL ERROR:",
            repr(e),
        )

        telegram_send(
            "❌ <b>NDS SCANNER ERROR</b>\n"
            f"Version: <b>{VERSION}</b>\n"
            f"Error: <code>"
            f"{str(e)[:1000]}"
            f"</code>"
        )

        raise
