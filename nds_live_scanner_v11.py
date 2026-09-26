# ============================================================
# NDS LIVE SCANNER
# VERSION 1.3
# ============================================================
#
# PAPER ONLY
#
# NDS:
#   Rally -> Hook 1 -> Hook 2 -> Breakout
#
# TIMEFRAME:
#   1H
#
# TELEGRAM:
#   NEW SIGNALS
#   OPEN TRADES
#   PERFORMANCE
#
# DATABASE:
#   nds_v11.db
#
# IMPORTANT:
#   NO REAL ORDERS
# ============================================================

import os
import time
import sqlite3
import traceback
from datetime import datetime, timezone

import requests

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle


# ============================================================
# CONFIG
# ============================================================

VERSION = "1.3"

BASE_URL = "https://futures.kraken.com"

TIMEFRAME = "1h"
CANDLE_LIMIT = 500

DB_FILE = "nds_v11.db"
CHART_DIR = "charts"

REAL_TRADING = False

# ------------------------------------------------------------
# NDS SETTINGS
# ------------------------------------------------------------

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

MIN_RALLY_PCT = 0.003

MIN_HOOK_RETRACE = 0.15
MAX_HOOK_RETRACE = 0.85

MIN_PRICE_SYMMETRY = 0.50
MAX_PRICE_SYMMETRY = 2.00

MIN_TIME_SYMMETRY = 0.50
MAX_TIME_SYMMETRY = 2.00

BREAK_BUFFER = 0.0005

SL_BUFFER = 0.0015

MIN_SL_DISTANCE = 0.002
MAX_SL_DISTANCE = 0.08

MAX_OPEN_TRADES = 3

MAX_SIGNAL_AGE_HOURS = 2.0

CHART_LEFT = 35
CHART_RIGHT = 15


# ============================================================
# ASSETS
# ============================================================

SYMBOLS = [
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
    "PF_ATOMUSD",
    "PF_UNIUSD",
    "PF_AAVEUSD",
    "PF_NEARUSD",
    "PF_ARBUSD",
    "PF_OPUSD",
    "PF_INJUSD",
    "PF_PEPEUSD",
    "PF_SUIUSD",
]


# ============================================================
# TELEGRAM
# ============================================================

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("TELEGRAM_TOKEN")
)

CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or os.getenv("TELEGRAM_CHAT")
)


# ============================================================
# HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def fmt_time(ts):
    try:
        return datetime.fromtimestamp(
            float(ts), tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return "-"


def clean_symbol(symbol):
    if symbol.startswith("PF_"):
        symbol = symbol[3:]
    return symbol.replace("USD", "")


def pct(a, b):
    if b == 0:
        return 0.0
    return abs(a - b) / abs(b) * 100.0


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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT NOT NULL,
            side TEXT NOT NULL,

            rally_pct REAL,
            hook1_retrace REAL,
            hook2_retrace REAL,

            price_symmetry REAL,
            time_symmetry REAL,

            p1_time INTEGER,
            p2_time INTEGER,
            p3_time INTEGER,
            p4_time INTEGER,

            p1_price REAL,
            p2_price REAL,
            p3_price REAL,
            p4_price REAL,

            break_time INTEGER,
            entry REAL,

            sl REAL,
            tp REAL,

            status TEXT NOT NULL DEFAULT 'OPEN',

            exit_price REAL,
            exit_time INTEGER,
            exit_reason TEXT,

            pnl_pct REAL,

            created_at INTEGER NOT NULL
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# DATABASE MIGRATION
# ============================================================

def migrate_db():

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(signals)")
    columns = {row[1] for row in cur.fetchall()}

    required = {
        "exit_price": "REAL",
        "exit_time": "INTEGER",
        "exit_reason": "TEXT",
        "pnl_pct": "REAL",
    }

    for col, typ in required.items():

        if col not in columns:

            try:
                cur.execute(
                    f"ALTER TABLE signals ADD COLUMN {col} {typ}"
                )
            except Exception:
                pass

    conn.commit()
    conn.close()


# ============================================================
# KRAKEN DATA
# ============================================================

def fetch_candles(symbol):

    url = (
        f"{BASE_URL}/api/charts/v1/trade/"
        f"{symbol}/{TIMEFRAME}"
    )

    try:

        r = requests.get(
            url,
            timeout=20
        )

        r.raise_for_status()

        data = r.json()

        candles = []

        # Kraken response normally:
        # {"candles":[...], ...}

        raw = data.get("candles", [])

        for c in raw:

            if isinstance(c, dict):

                ts = c.get("time")

                if ts is None:
                    ts = c.get("timestamp")

                o = c.get("open")
                h = c.get("high")
                l = c.get("low")
                cl = c.get("close")
                v = c.get("volume", 0)

            else:

                if len(c) < 5:
                    continue

                ts = c[0]
                o = c[1]
                h = c[2]
                l = c[3]
                cl = c[4]
                v = c[5] if len(c) > 5 else 0

            if ts is None:
                continue

            ts = float(ts)

            # Handle milliseconds
            if ts > 10_000_000_000:
                ts /= 1000.0

            try:
                candle = {
                    "time": int(ts),
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(cl),
                    "volume": float(v or 0),
                }
            except Exception:
                continue

            candles.append(candle)

        candles.sort(key=lambda x: x["time"])

        # ----------------------------------------------------
        # ONLY CLOSED 1H CANDLES
        # ----------------------------------------------------

        current = int(time.time())

        closed = []

        for c in candles:

            if c["time"] + 3600 <= current:
                closed.append(c)

        return closed[-CANDLE_LIMIT:]

    except Exception as e:

        print(f"[FETCH ERROR] {symbol}: {e}")

        return []


# ============================================================
# PIVOTS
# ============================================================

def detect_pivots(candles):

    pivots = []

    n = len(candles)

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT
    ):

        high = candles[i]["high"]
        low = candles[i]["low"]

        is_high = True
        is_low = True

        for j in range(
            i - PIVOT_LEFT,
            i + PIVOT_RIGHT + 1
        ):

            if j == i:
                continue

            if candles[j]["high"] >= high:
                is_high = False

            if candles[j]["low"] <= low:
                is_low = False

        if is_high:

            pivots.append({
                "index": i,
                "time": candles[i]["time"],
                "price": high,
                "type": "HIGH"
            })

        if is_low:

            pivots.append({
                "index": i,
                "time": candles[i]["time"],
                "price": low,
                "type": "LOW"
            })

    pivots.sort(key=lambda x: x["index"])

    # --------------------------------------------------------
    # Remove consecutive same-type pivots.
    # Keep the stronger extreme.
    # --------------------------------------------------------

    cleaned = []

    for p in pivots:

        if not cleaned:

            cleaned.append(p)
            continue

        last = cleaned[-1]

        if p["type"] != last["type"]:

            cleaned.append(p)

        else:

            if p["type"] == "HIGH":

                if p["price"] > last["price"]:
                    cleaned[-1] = p

            else:

                if p["price"] < last["price"]:
                    cleaned[-1] = p

    return cleaned


# ============================================================
# NDS SETUP
# ============================================================

def build_nds_setup(candles, pivots):

    if len(pivots) < 4:
        return None

    # Search newest 4-node structure first
    for i in range(
        len(pivots) - 4,
        -1,
        -1
    ):

        p1 = pivots[i]
        p2 = pivots[i + 1]
        p3 = pivots[i + 2]
        p4 = pivots[i + 3]

        # ----------------------------------------------------
        # LONG
        #
        # LOW -> HIGH -> LOW -> HIGH
        #
        # Rally:
        # P1 -> P2
        #
        # Hook 1:
        # P2 -> P3
        #
        # Hook 2:
        # P3 -> P4
        # ----------------------------------------------------

        if (
            p1["type"] == "LOW"
            and
            p2["type"] == "HIGH"
            and
            p3["type"] == "LOW"
            and
            p4["type"] == "HIGH"
        ):

            rally_size = p2["price"] - p1["price"]

            if rally_size <= 0:
                continue

            rally_pct = rally_size / p1["price"]

            if rally_pct < MIN_RALLY_PCT:
                continue

            hook1_size = p2["price"] - p3["price"]

            hook2_size = p4["price"] - p3["price"]

            hook1_retrace = (
                hook1_size / rally_size
            )

            hook2_retrace = (
                hook2_size / rally_size
            )

            if not (
                MIN_HOOK_RETRACE
                <= hook1_retrace
                <= MAX_HOOK_RETRACE
            ):
                continue

            if not (
                MIN_HOOK_RETRACE
                <= hook2_retrace
                <= MAX_HOOK_RETRACE
            ):
                continue

            if hook1_size <= 0:
                continue

            price_symmetry = (
                hook2_size / hook1_size
            )

            if not (
                MIN_PRICE_SYMMETRY
                <= price_symmetry
                <= MAX_PRICE_SYMMETRY
            ):
                continue

            time1 = p3["time"] - p2["time"]
            time2 = p4["time"] - p3["time"]

            if time1 <= 0:
                continue

            time_symmetry = time2 / time1

            if not (
                MIN_TIME_SYMMETRY
                <= time_symmetry
                <= MAX_TIME_SYMMETRY
            ):
                continue

            return {
                "side": "LONG",
                "p1": p1,
                "p2": p2,
                "p3": p3,
                "p4": p4,
                "rally_pct": rally_pct,
                "hook1_retrace": hook1_retrace,
                "hook2_retrace": hook2_retrace,
                "price_symmetry": price_symmetry,
                "time_symmetry": time_symmetry,
            }

        # ----------------------------------------------------
        # SHORT
        #
        # HIGH -> LOW -> HIGH -> LOW
        # ----------------------------------------------------

        if (
            p1["type"] == "HIGH"
            and
            p2["type"] == "LOW"
            and
            p3["type"] == "HIGH"
            and
            p4["type"] == "LOW"
        ):

            rally_size = p1["price"] - p2["price"]

            if rally_size <= 0:
                continue

            rally_pct = rally_size / p1["price"]

            if rally_pct < MIN_RALLY_PCT:
                continue

            hook1_size = p3["price"] - p2["price"]

            hook2_size = p3["price"] - p4["price"]

            hook1_retrace = (
                hook1_size / rally_size
            )

            hook2_retrace = (
                hook2_size / rally_size
            )

            if not (
                MIN_HOOK_RETRACE
                <= hook1_retrace
                <= MAX_HOOK_RETRACE
            ):
                continue

            if not (
                MIN_HOOK_RETRACE
                <= hook2_retrace
                <= MAX_HOOK_RETRACE
            ):
                continue

            if hook1_size <= 0:
                continue

            price_symmetry = (
                hook2_size / hook1_size
            )

            if not (
                MIN_PRICE_SYMMETRY
                <= price_symmetry
                <= MAX_PRICE_SYMMETRY
            ):
                continue

            time1 = p3["time"] - p2["time"]
            time2 = p4["time"] - p3["time"]

            if time1 <= 0:
                continue

            time_symmetry = time2 / time1

            if not (
                MIN_TIME_SYMMETRY
                <= time_symmetry
                <= MAX_TIME_SYMMETRY
            ):
                continue

            return {
                "side": "SHORT",
                "p1": p1,
                "p2": p2,
                "p3": p3,
                "p4": p4,
                "rally_pct": rally_pct,
                "hook1_retrace": hook1_retrace,
                "hook2_retrace": hook2_retrace,
                "price_symmetry": price_symmetry,
                "time_symmetry": time_symmetry,
            }

    return None


# ============================================================
# BREAKOUT
# ============================================================

def find_breakout(candles, setup):

    p2 = setup["p2"]
    p4 = setup["p4"]

    start = p2["index"] + 1

    end = len(candles)

    candidates = []

    for i in range(start, end):

        c = candles[i]

        # ----------------------------------------------------
        # LONG
        # Descending resistance P2 -> P4
        # ----------------------------------------------------

        if setup["side"] == "LONG":

            if p4["index"] == p2["index"]:
                continue

            slope = (
                p4["price"] - p2["price"]
            ) / (
                p4["index"] - p2["index"]
            )

            line_price = (
                p2["price"]
                + slope * (i - p2["index"])
            )

            trigger = (
                line_price
                * (1 + BREAK_BUFFER)
            )

            if c["close"] > trigger:

                candidates.append({
                    "index": i,
                    "time": c["time"],
                    "price": c["close"],
                    "line": line_price
                })

        # ----------------------------------------------------
        # SHORT
        # Ascending support P2 -> P4
        # ----------------------------------------------------

        else:

            if p4["index"] == p2["index"]:
                continue

            slope = (
                p4["price"] - p2["price"]
            ) / (
                p4["index"] - p2["index"]
            )

            line_price = (
                p2["price"]
                + slope * (i - p2["index"])
            )

            trigger = (
                line_price
                * (1 - BREAK_BUFFER)
            )

            if c["close"] < trigger:

                candidates.append({
                    "index": i,
                    "time": c["time"],
                    "price": c["close"],
                    "line": line_price
                })

    if not candidates:
        return None

    # newest breakout
    return candidates[-1]


# ============================================================
# STRUCTURAL LEVELS
# ============================================================

def find_levels(candles, setup, entry):

    p1 = setup["p1"]
    p2 = setup["p2"]
    p3 = setup["p3"]
    p4 = setup["p4"]

    if setup["side"] == "LONG":

        # Structural SL:
        # below P3
        sl = p3["price"] * (1 - SL_BUFFER)

        # TP:
        # expected rally projected from P3/P4
        rally_size = p2["price"] - p1["price"]

        tp = p4["price"] + rally_size

        # If TP is below entry, invalid
        if tp <= entry:
            return None, None

        sl_distance = (
            (entry - sl) / entry
        )

        if sl >= entry:
            return None, None

    else:

        # Structural SL:
        # above P3
        sl = p3["price"] * (1 + SL_BUFFER)

        rally_size = p1["price"] - p2["price"]

        tp = p4["price"] - rally_size

        if tp >= entry:
            return None, None

        sl_distance = (
            (sl - entry) / entry
        )

        if sl <= entry:
            return None, None

    if sl_distance < MIN_SL_DISTANCE:
        return None, None

    if sl_distance > MAX_SL_DISTANCE:
        return None, None

    return sl, tp


# ============================================================
# SIGNAL EXISTS
# ============================================================

def signal_exists(symbol, side, break_time):

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT id
        FROM signals
        WHERE symbol = ?
          AND side = ?
          AND break_time = ?
        LIMIT 1
    """, (
        symbol,
        side,
        break_time
    ))

    row = cur.fetchone()

    conn.close()

    return row is not None


# ============================================================
# OPEN COUNT
# ============================================================

def get_open_count():

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*)
        FROM signals
        WHERE status = 'OPEN'
    """)

    count = cur.fetchone()[0]

    conn.close()

    return count


# ============================================================
# INSERT SIGNAL
# ============================================================

def insert_signal(symbol, setup, breakout, sl, tp):

    entry = breakout["price"]

    columns = [
        "symbol",
        "side",
        "rally_pct",
        "hook1_retrace",
        "hook2_retrace",
        "price_symmetry",
        "time_symmetry",
        "p1_time",
        "p2_time",
        "p3_time",
        "p4_time",
        "p1_price",
        "p2_price",
        "p3_price",
        "p4_price",
        "break_time",
        "entry",
        "sl",
        "tp",
        "status",
        "created_at"
    ]

    values = [
        symbol,
        setup["side"],
        setup["rally_pct"],
        setup["hook1_retrace"],
        setup["hook2_retrace"],
        setup["price_symmetry"],
        setup["time_symmetry"],
        setup["p1"]["time"],
        setup["p2"]["time"],
        setup["p3"]["time"],
        setup["p4"]["time"],
        setup["p1"]["price"],
        setup["p2"]["price"],
        setup["p3"]["price"],
        setup["p4"]["price"],
        breakout["time"],
        entry,
        sl,
        tp,
        "OPEN",
        int(time.time())
    ]

    # --------------------------------------------------------
    # IMPORTANT:
    # 21 columns = 21 values
    #
    # This fixes the previous:
    # "25 values for 23 columns"
    # --------------------------------------------------------

    if len(columns) != len(values):
        raise RuntimeError(
            f"DB mismatch: {len(values)} values "
            f"for {len(columns)} columns"
        )

    placeholders = ",".join(
        ["?"] * len(columns)
    )

    sql = f"""
        INSERT INTO signals
        ({",".join(columns)})
        VALUES ({placeholders})
    """

    conn = db_connect()

    cur = conn.cursor()

    cur.execute(sql, values)

    signal_id = cur.lastrowid

    conn.commit()
    conn.close()

    return signal_id


# ============================================================
# EXIT OPEN TRADES
# ============================================================

def monitor_open_trades(latest_prices):

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY id ASC
    """)

    rows = cur.fetchall()

    closed = []

    for row in rows:

        symbol = row["symbol"]

        if symbol not in latest_prices:
            continue

        candle = latest_prices[symbol]

        high = candle["high"]
        low = candle["low"]

        side = row["side"]

        entry = row["entry"]
        sl = row["sl"]
        tp = row["tp"]

        exit_price = None
        exit_reason = None

        # ----------------------------------------------------
        # Conservative rule:
        # if SL and TP happen in same candle,
        # SL is assumed first.
        # ----------------------------------------------------

        if side == "LONG":

            if low <= sl:

                exit_price = sl
                exit_reason = "SL"

            elif high >= tp:

                exit_price = tp
                exit_reason = "TP"

        else:

            if high >= sl:

                exit_price = sl
                exit_reason = "SL"

            elif low <= tp:

                exit_price = tp
                exit_reason = "TP"

        if exit_price is None:
            continue

        if side == "LONG":

            pnl = (
                (exit_price - entry)
                / entry
                * 100
            )

        else:

            pnl = (
                (entry - exit_price)
                / entry
                * 100
            )

        cur.execute("""
            UPDATE signals
            SET
                status = 'CLOSED',
                exit_price = ?,
                exit_time = ?,
                exit_reason = ?,
                pnl_pct = ?
            WHERE id = ?
        """, (
            exit_price,
            candle["time"],
            exit_reason,
            pnl,
            row["id"]
        ))

        closed.append({
            "id": row["id"],
            "symbol": symbol,
            "side": side,
            "entry": entry,
            "exit": exit_price,
            "reason": exit_reason,
            "pnl": pnl,
            "time": candle["time"]
        })

    conn.commit()
    conn.close()

    return closed


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades(latest_prices):

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY id ASC
    """)

    rows = cur.fetchall()

    conn.close()

    result = []

    for row in rows:

        symbol = row["symbol"]

        if symbol not in latest_prices:
            continue

        current = latest_prices[symbol]["close"]

        entry = row["entry"]

        if row["side"] == "LONG":

            pnl = (
                (current - entry)
                / entry
                * 100
            )

        else:

            pnl = (
                (entry - current)
                / entry
                * 100
            )

        result.append({
            "id": row["id"],
            "symbol": symbol,
            "side": row["side"],
            "entry": entry,
            "current": current,
            "sl": row["sl"],
            "tp": row["tp"],
            "pnl": pnl,
            "created_at": row["created_at"],
        })

    return result


# ============================================================
# PERFORMANCE
# ============================================================

def get_performance():

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(
                CASE
                    WHEN status = 'CLOSED'
                    THEN 1
                    ELSE 0
                END
            ) AS closed,

            SUM(
                CASE
                    WHEN status = 'CLOSED'
                     AND pnl_pct > 0
                    THEN 1
                    ELSE 0
                END
            ) AS wins,

            SUM(
                CASE
                    WHEN status = 'CLOSED'
                     AND pnl_pct < 0
                    THEN 1
                    ELSE 0
                END
            ) AS losses,

            COALESCE(
                SUM(
                    CASE
                        WHEN status = 'CLOSED'
                        THEN pnl_pct
                        ELSE 0
                    END
                ),
                0
            ) AS pnl

        FROM signals
    """)

    row = cur.fetchone()

    conn.close()

    total = row["total"] or 0
    closed = row["closed"] or 0
    wins = row["wins"] or 0
    losses = row["losses"] or 0
    pnl = row["pnl"] or 0.0

    if closed > 0:
        win_rate = wins / closed * 100
    else:
        win_rate = 0.0

    return {
        "total": total,
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "pnl": pnl,
        "win_rate": win_rate,
    }


# ============================================================
# TELEGRAM
# ============================================================

def telegram_request(method, data=None, files=None):

    if not BOT_TOKEN or not CHAT_ID:
        print("[TELEGRAM] Credentials missing")
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    try:

        if files:

            r = requests.post(
                url,
                data=data,
                files=files,
                timeout=30
            )

        else:

            r = requests.post(
                url,
                data=data,
                timeout=20
            )

        if not r.ok:

            print(
                f"[TELEGRAM ERROR] "
                f"{r.status_code}: {r.text[:500]}"
            )

            return False

        return True

    except Exception as e:

        print(
            f"[TELEGRAM ERROR] {e}"
        )

        return False


def send_telegram(text):

    return telegram_request(
        "sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    )


def send_telegram_photo(path, caption):

    if not os.path.exists(path):
        return False

    try:

        with open(path, "rb") as f:

            return telegram_request(
                "sendPhoto",
                data={
                    "chat_id": CHAT_ID,
                    "caption": caption[:1000],
                    "parse_mode": "HTML",
                },
                files={
                    "photo": f
                }
            )

    except Exception as e:

        print(
            f"[TELEGRAM PHOTO ERROR] {e}"
        )

        return False


# ============================================================
# NEW SIGNAL TELEGRAM
# ============================================================

def format_signal(symbol, setup, breakout, sl, tp):

    side = setup["side"]

    emoji = "🟢" if side == "LONG" else "🔴"

    entry = breakout["price"]

    if side == "LONG":

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

    return (
        f"<b>{emoji} NDS NEW SIGNAL</b>\n"
        f"\n"
        f"<b>{clean_symbol(symbol)}</b>  "
        f"<b>{side}</b>\n"
        f"\n"
        f"Entry: <code>{entry:.8g}</code>\n"
        f"SL: <code>{sl:.8g}</code> "
        f"({sl_pct:+.2f}%)\n"
        f"TP: <code>{tp:.8g}</code> "
        f"({tp_pct:+.2f}%)\n"
        f"\n"
        f"Rally: {setup['rally_pct']*100:.2f}%\n"
        f"Hook 1: {setup['hook1_retrace']*100:.1f}%\n"
        f"Hook 2: {setup['hook2_retrace']*100:.1f}%\n"
        f"Price Sym: {setup['price_symmetry']:.2f}\n"
        f"Time Sym: {setup['time_symmetry']:.2f}\n"
        f"\n"
        f"Break: {fmt_time(breakout['time'])}\n"
        f"Mode: PAPER ONLY"
    )


# ============================================================
# CLOSED TRADE TELEGRAM
# ============================================================

def format_closed_trade(trade):

    emoji = (
        "🟢"
        if trade["pnl"] > 0
        else "🔴"
    )

    return (
        f"{emoji} <b>TRADE CLOSED</b>\n"
        f"\n"
        f"{clean_symbol(trade['symbol'])} "
        f"{trade['side']}\n"
        f"Entry: <code>{trade['entry']:.8g}</code>\n"
        f"Exit: <code>{trade['exit']:.8g}</code>\n"
        f"Result: <b>{trade['pnl']:+.2f}%</b>\n"
        f"Reason: {trade['reason']}\n"
        f"Time: {fmt_time(trade['time'])}"
    )


# ============================================================
# OPEN TRADES REPORT
# ============================================================

def format_open_trades(trades):

    lines = [
        "<b>📌 OPEN TRADES</b>",
        ""
    ]

    if not trades:

        lines.append("No open trades")
        return "\n".join(lines)

    total_pnl = 0.0

    for t in trades:

        emoji = (
            "🟢"
            if t["side"] == "LONG"
            else "🔴"
        )

        pnl_emoji = (
            "🟢"
            if t["pnl"] >= 0
            else "🔴"
        )

        total_pnl += t["pnl"]

        lines.extend([
            (
                f"{emoji} <b>{clean_symbol(t['symbol'])}</b> "
                f"{t['side']}"
            ),
            (
                f"Entry: <code>{t['entry']:.8g}</code>"
            ),
            (
                f"Current: <b>{t['current']:.8g}</b>"
            ),
            (
                f"P/L: {pnl_emoji} "
                f"<b>{t['pnl']:+.2f}%</b>"
            ),
            (
                f"SL: <code>{t['sl']:.8g}</code> | "
                f"TP: <code>{t['tp']:.8g}</code>"
            ),
            ""
        ])

    lines.append(
        f"<b>Total Open P/L: "
        f"{total_pnl:+.2f}%</b>"
    )

    return "\n".join(lines)


# ============================================================
# PERFORMANCE REPORT
# ============================================================

def format_performance(perf):

    pnl_emoji = (
        "🟢"
        if perf["pnl"] >= 0
        else "🔴"
    )

    return (
        "<b>📊 NDS PERFORMANCE</b>\n"
        "\n"
        f"Total signals: {perf['total']}\n"
        f"Closed trades: {perf['closed']}\n"
        f"🟢 Wins: {perf['wins']}\n"
        f"🔴 Losses: {perf['losses']}\n"
        f"Win rate: <b>{perf['win_rate']:.2f}%</b>\n"
        f"Total P/L: {pnl_emoji} "
        f"<b>{perf['pnl']:+.2f}%</b>\n"
        "\n"
        "Mode: PAPER ONLY"
    )


# ============================================================
# CHART
# ============================================================

def create_nds_chart(
    symbol,
    candles,
    setup,
    breakout,
    sl,
    tp
):

    os.makedirs(
        CHART_DIR,
        exist_ok=True
    )

    p1 = setup["p1"]
    p2 = setup["p2"]
    p3 = setup["p3"]
    p4 = setup["p4"]

    break_index = breakout["index"]

    start = max(
        0,
        break_index - CHART_LEFT
    )

    end = min(
        len(candles),
        break_index + CHART_RIGHT + 1
    )

    view = candles[start:end]

    fig, ax = plt.subplots(
        figsize=(14, 8)
    )

    # --------------------------------------------------------
    # Candles
    # --------------------------------------------------------

    for i, c in enumerate(view):

        x = i

        o = c["open"]
        h = c["high"]
        l = c["low"]
        cl = c["close"]

        if cl >= o:
            body_bottom = o
            body_height = cl - o
        else:
            body_bottom = cl
            body_height = o - cl

        ax.plot(
            [x, x],
            [l, h],
            linewidth=1
        )

        rect = Rectangle(
            (
                x - 0.30,
                body_bottom
            ),
            0.60,
            max(body_height, 1e-12),
            fill=False,
            linewidth=1
        )

        ax.add_patch(rect)

    # --------------------------------------------------------
    # Map pivot indexes
    # --------------------------------------------------------

    def vx(index):
        return index - start

    # --------------------------------------------------------
    # Rally
    # --------------------------------------------------------

    ax.plot(
        [vx(p1["index"]), vx(p2["index"])],
        [p1["price"], p2["price"]],
        linewidth=2,
        label="RALLY"
    )

    # --------------------------------------------------------
    # Hook 1
    # --------------------------------------------------------

    ax.plot(
        [vx(p2["index"]), vx(p3["index"])],
        [p2["price"], p3["price"]],
        linewidth=2,
        label="HOOK 1"
    )

    # --------------------------------------------------------
    # Hook 2
    # --------------------------------------------------------

    ax.plot(
        [vx(p3["index"]), vx(p4["index"])],
        [p3["price"], p4["price"]],
        linewidth=2,
        label="HOOK 2"
    )

    # --------------------------------------------------------
    # Nodes
    # --------------------------------------------------------

    points = [
        ("P1", p1),
        ("P2", p2),
        ("P3", p3),
        ("P4", p4),
    ]

    for name, p in points:

        ax.scatter(
            vx(p["index"]),
            p["price"],
            s=60
        )

        ax.annotate(
            name,
            (
                vx(p["index"]),
                p["price"]
            ),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            fontsize=10
        )

    # --------------------------------------------------------
    # Breakout
    # --------------------------------------------------------

    ax.scatter(
        vx(breakout["index"]),
        breakout["price"],
        s=100,
        marker="*",
        label="BREAK / ENTRY"
    )

    ax.axhline(
        sl,
        linestyle="--",
        linewidth=1.5,
        label=f"SL {sl:.8g}"
    )

    ax.axhline(
        tp,
        linestyle="--",
        linewidth=1.5,
        label=f"TP {tp:.8g}"
    )

    # --------------------------------------------------------
    # Expected Rally
    # --------------------------------------------------------

    if setup["side"] == "LONG":

        rally_size = (
            p2["price"]
            - p1["price"]
        )

        expected = (
            p4["price"]
            + rally_size
        )

    else:

        rally_size = (
            p1["price"]
            - p2["price"]
        )

        expected = (
            p4["price"]
            - rally_size
        )

    ax.axhline(
        expected,
        linestyle=":",
        linewidth=1,
        label="Expected Rally"
    )

    # --------------------------------------------------------
    # Title
    # --------------------------------------------------------

    title = (
        f"NDS {clean_symbol(symbol)} "
        f"{setup['side']} | "
        f"Rally={setup['rally_pct']*100:.2f}% | "
        f"Hook1={setup['hook1_retrace']*100:.1f}% | "
        f"Hook2={setup['hook2_retrace']*100:.1f}% | "
        f"PS={setup['price_symmetry']:.2f} | "
        f"TS={setup['time_symmetry']:.2f}"
    )

    ax.set_title(title)

    ax.set_xlabel("1H candles")
    ax.set_ylabel("Price")

    ax.grid(
        alpha=0.20
    )

    ax.legend(
        loc="best"
    )

    filename = (
        f"{CHART_DIR}/"
        f"{symbol}_"
        f"{setup['side']}_"
        f"{breakout['time']}.png"
    )

    plt.tight_layout()

    plt.savefig(
        filename,
        dpi=140
    )

    plt.close(fig)

    return filename


# ============================================================
# SCAN ONE SYMBOL
# ============================================================

def scan_symbol(symbol):

    print(
        f"[SCAN] {symbol}"
    )

    candles = fetch_candles(symbol)

    if len(candles) < 50:

        print(
            f"[NO DATA] {symbol}"
        )

        return None

    pivots = detect_pivots(candles)

    setup = build_nds_setup(
        candles,
        pivots
    )

    if setup is None:

        print(
            f"[NO NDS] {symbol}"
        )

        return None

    print(
        f"[NDS] {symbol} "
        f"{setup['side']} "
        f"Rally={setup['rally_pct']*100:.2f}% "
        f"Hook={setup['hook2_retrace']*100:.1f}% "
        f"PS={setup['price_symmetry']:.2f} "
        f"TS={setup['time_symmetry']:.2f}"
    )

    breakout = find_breakout(
        candles,
        setup
    )

    if breakout is None:

        print(
            f"[NO BREAK] {symbol}"
        )

        return None

    age_hours = (
        time.time()
        - breakout["time"]
    ) / 3600.0

    print(
        f"[BREAK] {symbol} "
        f"{setup['side']} "
        f"age={age_hours:.2f}h"
    )

    if age_hours > MAX_SIGNAL_AGE_HOURS:

        print(
            f"[OLD] {symbol} "
            f"signal rejected"
        )

        return None

    # --------------------------------------------------------
    # Duplicate protection
    # --------------------------------------------------------

    if signal_exists(
        symbol,
        setup["side"],
        breakout["time"]
    ):

        print(
            f"[DUPLICATE] {symbol}"
        )

        return None

    # --------------------------------------------------------
    # Maximum open trades
    # --------------------------------------------------------

    if get_open_count() >= MAX_OPEN_TRADES:

        print(
            f"[MAX OPEN] {symbol}"
        )

        return None

    entry = breakout["price"]

    sl, tp = find_levels(
        candles,
        setup,
        entry
    )

    if sl is None or tp is None:

        print(
            f"[INVALID LEVELS] {symbol}"
        )

        return None

    # --------------------------------------------------------
    # Final sanity checks
    # --------------------------------------------------------

    if setup["side"] == "LONG":

        if not (
            sl < entry < tp
        ):
            print(
                f"[INVALID LONG LEVELS] {symbol}"
            )
            return None

    else:

        if not (
            tp < entry < sl
        ):
            print(
                f"[INVALID SHORT LEVELS] {symbol}"
            )
            return None

    # --------------------------------------------------------
    # Insert DB
    # --------------------------------------------------------

    signal_id = insert_signal(
        symbol,
        setup,
        breakout,
        sl,
        tp
    )

    print(
        f"[NEW SIGNAL] {symbol} "
        f"{setup['side']} "
        f"id={signal_id}"
    )

    chart = create_nds_chart(
        symbol,
        candles,
        setup,
        breakout,
        sl,
        tp
    )

    return {
        "id": signal_id,
        "symbol": symbol,
        "setup": setup,
        "breakout": breakout,
        "sl": sl,
        "tp": tp,
        "chart": chart,
    }


# ============================================================
# MAIN SCAN
# ============================================================

def run_scan():

    print("=" * 70)
    print(
        f"NDS LIVE SCANNER v{VERSION}"
    )
    print("=" * 70)

    print(
        f"Assets: {len(SYMBOLS)}"
    )

    print(
        f"Timeframe: 1H"
    )

    print(
        "Mode: PAPER ONLY"
    )

    print("=" * 70)

    # --------------------------------------------------------
    # Fetch latest candles first
    # --------------------------------------------------------

    latest_prices = {}

    for symbol in SYMBOLS:

        candles = fetch_candles(symbol)

        if candles:

            latest_prices[symbol] = candles[-1]

    # --------------------------------------------------------
    # Monitor open trades
    # --------------------------------------------------------

    print(
        f"[MONITOR] "
        f"{get_open_count()} open trades"
    )

    closed_trades = monitor_open_trades(
        latest_prices
    )

    # --------------------------------------------------------
    # Send close alerts
    # --------------------------------------------------------

    for trade in closed_trades:

        print(
            f"[CLOSED] "
            f"{trade['symbol']} "
            f"{trade['reason']} "
            f"{trade['pnl']:+.2f}%"
        )

        send_telegram(
            format_closed_trade(
                trade
            )
        )

    # --------------------------------------------------------
    # Scan
    # --------------------------------------------------------

    scanned = 0
    errors = 0

    new_long = 0
    new_short = 0

    new_signals = []

    for symbol in SYMBOLS:

        scanned += 1

        try:

            result = scan_symbol(
                symbol
            )

            if result:

                new_signals.append(
                    result
                )

                if (
                    result["setup"]["side"]
                    == "LONG"
                ):
                    new_long += 1

                else:
                    new_short += 1

        except Exception as e:

            errors += 1

            print(
                f"[ERROR] {symbol}: {e}"
            )

            traceback.print_exc()

    # --------------------------------------------------------
    # Send NEW SIGNALS
    # --------------------------------------------------------

    for signal in new_signals:

        message = format_signal(
            signal["symbol"],
            signal["setup"],
            signal["breakout"],
            signal["sl"],
            signal["tp"]
        )

        send_telegram(
            message
        )

        # Send chart separately
        if signal.get("chart"):

            chart_caption = (
                f"NDS {clean_symbol(signal['symbol'])} "
                f"{signal['setup']['side']} | "
                f"Rally "
                f"{signal['setup']['rally_pct']*100:.2f}%"
            )

            send_telegram_photo(
                signal["chart"],
                chart_caption
            )

    # --------------------------------------------------------
    # Open trades after scan
    # --------------------------------------------------------

    open_trades = get_open_trades(
        latest_prices
    )

    # --------------------------------------------------------
    # Performance
    # --------------------------------------------------------

    performance = get_performance()

    # --------------------------------------------------------
    # Console report
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("SCAN COMPLETE")
    print("=" * 70)

    print(
        f"Scanned: {scanned}/{len(SYMBOLS)}"
    )

    print(
        f"Errors: {errors}"
    )

    print(
        f"New LONG: {new_long}"
    )

    print(
        f"New SHORT: {new_short}"
    )

    print()
    print("=" * 60)
    print("NDS LIVE REPORT")
    print("=" * 60)

    print(
        f"Version: {VERSION}"
    )

    print(
        f"Assets scanned: {len(SYMBOLS)}"
    )

    print(
        f"Open trades: {len(open_trades)}"
    )

    print(
        f"Closed trades: {performance['closed']}"
    )

    print(
        f"Wins: {performance['wins']}"
    )

    print(
        f"Losses: {performance['losses']}"
    )

    print(
        f"Win rate: "
        f"{performance['win_rate']:.2f}%"
    )

    print(
        f"Total P/L: "
        f"{performance['pnl']:+.2f}%"
    )

    print(
        "=" * 60
    )

    # --------------------------------------------------------
    # Telegram: OPEN TRADES
    # --------------------------------------------------------

    send_telegram(
        format_open_trades(
            open_trades
        )
    )

    # --------------------------------------------------------
    # Telegram: PERFORMANCE
    # --------------------------------------------------------

    send_telegram(
        format_performance(
            performance
        )
    )

    print()
    print(
        "[DONE] Scanner finished successfully."
    )

    return {
        "scanned": scanned,
        "errors": errors,
        "new_long": new_long,
        "new_short": new_short,
        "open_trades": len(open_trades),
        "closed": performance["closed"],
    }


# ============================================================
# MAIN
# ============================================================

def main():

    if REAL_TRADING:

        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    os.makedirs(
        CHART_DIR,
        exist_ok=True
    )

    init_db()
    migrate_db()

    if not BOT_TOKEN or not CHAT_ID:

        print(
            "[WARNING] Telegram credentials "
            "are missing."
        )

    run_scan()


if __name__ == "__main__":
    main()
