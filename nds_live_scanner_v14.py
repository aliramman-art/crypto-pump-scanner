# ============================================================
# NDS LIVE SCANNER
# VERSION 1.4
# ============================================================
#
# PAPER ONLY
#
# STRATEGY
#   Rally -> Hook 1 -> Hook 2 -> Breakout
#
# TIMEFRAME:
#   1H CLOSED CANDLES ONLY
#
# FEATURES
#   - NDS pattern detection
#   - Rally / Hook 1 / Hook 2
#   - Price symmetry
#   - Time symmetry
#   - Breakout detection
#   - Structural SL / TP
#   - SQLite persistent database
#   - Automatic DB schema migration
#   - Telegram alerts
#   - Telegram performance report
#   - Charts
#
# ============================================================

import os
import sqlite3
import time
import math
from datetime import datetime, timezone

import requests
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


# ============================================================
# CONFIG
# ============================================================

VERSION = "1.4"

BASE_URL = "https://futures.kraken.com"

TIMEFRAME = "1h"
CANDLE_LIMIT = 500

DB_FILE = "nds_v11.db"
CHART_DIR = "charts"

REAL_TRADING = False

# ------------------------------------------------------------
# NDS PARAMETERS
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
# KRAKEN SYMBOLS
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

TELEGRAM_BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("TELEGRAM_TOKEN")
)

TELEGRAM_CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or os.getenv("TELEGRAM_CHAT")
)


# ============================================================
# DATABASE SCHEMA
# ============================================================

CANONICAL_COLUMNS = {
    "symbol": "TEXT",
    "side": "TEXT",
    "rally_pct": "REAL",
    "hook1_retrace": "REAL",
    "hook2_retrace": "REAL",
    "price_symmetry": "REAL",
    "time_symmetry": "REAL",

    "p1_time": "INTEGER",
    "p2_time": "INTEGER",
    "p3_time": "INTEGER",
    "p4_time": "INTEGER",

    "p1_price": "REAL",
    "p2_price": "REAL",
    "p3_price": "REAL",
    "p4_price": "REAL",

    "break_time": "INTEGER",

    "entry": "REAL",
    "sl": "REAL",
    "tp": "REAL",

    "status": "TEXT",

    "exit_price": "REAL",
    "exit_time": "INTEGER",
    "exit_reason": "TEXT",
    "pnl_pct": "REAL",

    "created_at": "INTEGER",
}


# ============================================================
# DB HELPERS
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn, table_name="signals"):
    rows = conn.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    return [row["name"] for row in rows]


def create_canonical_table(conn, table_name="signals"):
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
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

            status TEXT,

            exit_price REAL,
            exit_time INTEGER,
            exit_reason TEXT,
            pnl_pct REAL,

            created_at INTEGER
        )
        """
    )

    conn.commit()


def rebuild_database(conn, old_columns):
    """
    Rebuild signals table if its structure is badly incompatible.

    Existing columns that match the new schema are preserved.
    """

    print("[DB] Rebuilding incompatible signals table...")

    conn.execute("DROP TABLE IF EXISTS signals_new")

    create_canonical_table(conn, "signals_new")

    new_columns = ["id"] + list(CANONICAL_COLUMNS.keys())

    common = [
        col for col in new_columns
        if col in old_columns
    ]

    if common:
        cols = ", ".join(common)

        conn.execute(
            f"""
            INSERT INTO signals_new ({cols})
            SELECT {cols}
            FROM signals
            """
        )

    conn.execute("DROP TABLE signals")
    conn.execute("ALTER TABLE signals_new RENAME TO signals")

    conn.commit()

    print("[DB] Database rebuild completed.")


def migrate_db():
    """
    Robust SQLite migration.

    Important:
    The old version could have a signals table without break_time.
    ALTER TABLE is used whenever possible so existing trades survive.
    """

    conn = db_connect()

    existing_tables = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        AND name='signals'
        """
    ).fetchone()

    if not existing_tables:
        print("[DB] Creating new database...")
        create_canonical_table(conn)
        conn.close()
        return

    columns = table_columns(conn)

    # id is special because it is the primary key.
    # If id is missing, rebuild the table.
    if "id" not in columns:
        rebuild_database(conn, columns)
        conn.close()
        return

    # Add all missing normal columns.
    for column, column_type in CANONICAL_COLUMNS.items():

        if column not in columns:

            print(
                f"[DB] Adding missing column: "
                f"{column}"
            )

            conn.execute(
                f"""
                ALTER TABLE signals
                ADD COLUMN {column} {column_type}
                """
            )

    conn.commit()

    # Verify migration.
    final_columns = table_columns(conn)

    missing = [
        column
        for column in CANONICAL_COLUMNS
        if column not in final_columns
    ]

    if missing:
        print(
            "[DB] Migration incomplete. "
            "Rebuilding..."
        )

        rebuild_database(
            conn,
            final_columns
        )

    conn.close()

    print("[DB] Schema OK.")


# ============================================================
# TIME
# ============================================================

def now_ts():
    return int(time.time())


def fmt_time(ts):
    if not ts:
        return "-"

    try:
        return datetime.fromtimestamp(
            int(ts),
            tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return "-"


# ============================================================
# KRAKEN API
# ============================================================

def fetch_candles(symbol):
    url = (
        f"{BASE_URL}/api/charts/v1/trade/"
        f"{symbol}/{TIMEFRAME}"
    )

    params = {
        "last": CANDLE_LIMIT
    }

    r = requests.get(
        url,
        params=params,
        timeout=20
    )

    r.raise_for_status()

    data = r.json()

    candles = (
        data.get("candles")
        or data.get("result")
        or []
    )

    parsed = []

    for c in candles:

        try:

            if isinstance(c, dict):

                ts = (
                    c.get("time")
                    or c.get("timestamp")
                    or c.get("t")
                )

                o = (
                    c.get("open")
                    or c.get("o")
                )

                h = (
                    c.get("high")
                    or c.get("h")
                )

                l = (
                    c.get("low")
                    or c.get("l")
                )

                close = (
                    c.get("close")
                    or c.get("c")
                )

            else:

                # Fallback for array format
                ts = c[0]
                o = c[1]
                h = c[2]
                l = c[3]
                close = c[4]

            ts = int(float(ts))

            # Kraken may return milliseconds.
            if ts > 10_000_000_000:
                ts //= 1000

            parsed.append({
                "time": ts,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(close),
            })

        except Exception:
            continue

    parsed.sort(key=lambda x: x["time"])

    # --------------------------------------------------------
    # Remove currently forming 1H candle.
    # --------------------------------------------------------

    current_hour = int(time.time() // 3600) * 3600

    parsed = [
        c for c in parsed
        if c["time"] < current_hour
    ]

    return parsed[-CANDLE_LIMIT:]


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(candles):
    highs = []
    lows = []

    n = len(candles)

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT
    ):

        h = candles[i]["high"]
        l = candles[i]["low"]

        left_highs = [
            candles[j]["high"]
            for j in range(
                i - PIVOT_LEFT,
                i
            )
        ]

        right_highs = [
            candles[j]["high"]
            for j in range(
                i + 1,
                i + PIVOT_RIGHT + 1
            )
        ]

        left_lows = [
            candles[j]["low"]
            for j in range(
                i - PIVOT_LEFT,
                i
            )
        ]

        right_lows = [
            candles[j]["low"]
            for j in range(
                i + 1,
                i + PIVOT_RIGHT + 1
            )
        ]

        if all(h >= x for x in left_highs + right_highs):
            highs.append({
                "index": i,
                "time": candles[i]["time"],
                "price": h,
                "type": "HIGH",
            })

        if all(l <= x for x in left_lows + right_lows):
            lows.append({
                "index": i,
                "time": candles[i]["time"],
                "price": l,
                "type": "LOW",
            })

    return highs, lows


def clean_pivots(pivots):
    """
    Prevent consecutive same-type pivots.

    If two consecutive highs exist, retain the stronger one.
    """

    if not pivots:
        return []

    pivots = sorted(
        pivots,
        key=lambda x: x["index"]
    )

    result = []

    for p in pivots:

        if not result:
            result.append(p)
            continue

        prev = result[-1]

        if p["type"] != prev["type"]:
            result.append(p)
            continue

        if p["type"] == "HIGH":

            if p["price"] > prev["price"]:
                result[-1] = p

        else:

            if p["price"] < prev["price"]:
                result[-1] = p

    return result


# ============================================================
# NDS PATTERN
# ============================================================

def calculate_pattern(pivots):
    """
    Detect:

    LONG:
        LOW -> HIGH -> LOW -> HIGH

    SHORT:
        HIGH -> LOW -> HIGH -> LOW
    """

    if len(pivots) < 4:
        return None

    best = None

    # Search from newest area backwards.
    for i in range(len(pivots) - 4, -1, -1):

        p1, p2, p3, p4 = pivots[i:i + 4]

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if (
            p1["type"] == "LOW"
            and p2["type"] == "HIGH"
            and p3["type"] == "LOW"
            and p4["type"] == "HIGH"
        ):

            rally = p2["price"] - p1["price"]

            if rally <= 0:
                continue

            rally_pct = (
                rally / p1["price"]
            )

            if rally_pct < MIN_RALLY_PCT:
                continue

            hook1 = (
                p2["price"] - p3["price"]
            ) / rally

            if not (
                MIN_HOOK_RETRACE
                <= hook1
                <= MAX_HOOK_RETRACE
            ):
                continue

            hook2_move = (
                p4["price"] - p3["price"]
            )

            if hook2_move <= 0:
                continue

            price_symmetry = (
                hook2_move / rally
            )

            t_rally = p2["time"] - p1["time"]
            t_hook = p4["time"] - p3["time"]

            if t_rally <= 0 or t_hook <= 0:
                continue

            time_symmetry = (
                t_hook / t_rally
            )

            price_symmetry = max(
                0.000001,
                price_symmetry
            )

            time_symmetry = max(
                0.000001,
                time_symmetry
            )

            if not (
                MIN_PRICE_SYMMETRY
                <= price_symmetry
                <= MAX_PRICE_SYMMETRY
            ):
                continue

            if not (
                MIN_TIME_SYMMETRY
                <= time_symmetry
                <= MAX_TIME_SYMMETRY
            ):
                continue

            best = {
                "side": "LONG",
                "p1": p1,
                "p2": p2,
                "p3": p3,
                "p4": p4,
                "rally_pct": rally_pct,
                "hook1_retrace": hook1,
                "hook2_retrace": (
                    hook2_move / rally
                ),
                "price_symmetry": price_symmetry,
                "time_symmetry": time_symmetry,
            }

            break

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        if (
            p1["type"] == "HIGH"
            and p2["type"] == "LOW"
            and p3["type"] == "HIGH"
            and p4["type"] == "LOW"
        ):

            rally = p1["price"] - p2["price"]

            if rally <= 0:
                continue

            rally_pct = (
                rally / p1["price"]
            )

            if rally_pct < MIN_RALLY_PCT:
                continue

            hook1 = (
                p3["price"] - p2["price"]
            ) / rally

            if not (
                MIN_HOOK_RETRACE
                <= hook1
                <= MAX_HOOK_RETRACE
            ):
                continue

            hook2_move = (
                p3["price"] - p4["price"]
            )

            if hook2_move <= 0:
                continue

            price_symmetry = (
                hook2_move / rally
            )

            t_rally = p2["time"] - p1["time"]
            t_hook = p4["time"] - p3["time"]

            if t_rally <= 0 or t_hook <= 0:
                continue

            time_symmetry = (
                t_hook / t_rally
            )

            price_symmetry = max(
                0.000001,
                price_symmetry
            )

            time_symmetry = max(
                0.000001,
                time_symmetry
            )

            if not (
                MIN_PRICE_SYMMETRY
                <= price_symmetry
                <= MAX_PRICE_SYMMETRY
            ):
                continue

            if not (
                MIN_TIME_SYMMETRY
                <= time_symmetry
                <= MAX_TIME_SYMMETRY
            ):
                continue

            best = {
                "side": "SHORT",
                "p1": p1,
                "p2": p2,
                "p3": p3,
                "p4": p4,
                "rally_pct": rally_pct,
                "hook1_retrace": hook1,
                "hook2_retrace": (
                    hook2_move / rally
                ),
                "price_symmetry": price_symmetry,
                "time_symmetry": time_symmetry,
            }

            break

    return best


# ============================================================
# TRENDLINE
# ============================================================

def trendline_price(pattern, timestamp):
    """
    P2 -> P4 trendline.
    """

    p2 = pattern["p2"]
    p4 = pattern["p4"]

    dt = p4["time"] - p2["time"]

    if dt == 0:
        return None

    slope = (
        p4["price"] - p2["price"]
    ) / dt

    return (
        p2["price"]
        + slope * (timestamp - p2["time"])
    )


def find_breakout(candles, pattern):
    """
    Search candles after P4 for the first closed-candle breakout.
    """

    side = pattern["side"]

    p4_time = pattern["p4"]["time"]

    candidates = [
        c for c in candles
        if c["time"] > p4_time
    ]

    if not candidates:
        return None

    for c in candidates:

        line = trendline_price(
            pattern,
            c["time"]
        )

        if line is None:
            continue

        if side == "LONG":

            trigger = (
                line * (1 + BREAK_BUFFER)
            )

            if c["close"] > trigger:

                return {
                    "time": c["time"],
                    "price": c["close"],
                    "trendline": line,
                }

        else:

            trigger = (
                line * (1 - BREAK_BUFFER)
            )

            if c["close"] < trigger:

                return {
                    "time": c["time"],
                    "price": c["close"],
                    "trendline": line,
                }

    return None


# ============================================================
# STRUCTURAL LEVELS
# ============================================================

def find_levels(candles, pattern, breakout):
    """
    Structural SL and projected TP.

    SL is based around P3.
    TP uses expected rally projection from P4.
    """

    side = pattern["side"]

    entry = breakout["price"]

    p1 = pattern["p1"]
    p2 = pattern["p2"]
    p3 = pattern["p3"]
    p4 = pattern["p4"]

    rally_size = abs(
        p2["price"] - p1["price"]
    )

    if side == "LONG":

        structural_sl = (
            p3["price"]
            * (1 - SL_BUFFER)
        )

        sl_distance = (
            entry - structural_sl
        ) / entry

        if sl_distance < MIN_SL_DISTANCE:

            structural_sl = (
                entry
                * (1 - MIN_SL_DISTANCE)
            )

        if sl_distance > MAX_SL_DISTANCE:

            structural_sl = (
                entry
                * (1 - MAX_SL_DISTANCE)
            )

        tp = p4["price"] + rally_size

        if tp <= entry:
            tp = entry * 1.01

        return {
            "entry": entry,
            "sl": structural_sl,
            "tp": tp,
        }

    else:

        structural_sl = (
            p3["price"]
            * (1 + SL_BUFFER)
        )

        sl_distance = (
            structural_sl - entry
        ) / entry

        if sl_distance < MIN_SL_DISTANCE:

            structural_sl = (
                entry
                * (1 + MIN_SL_DISTANCE)
            )

        if sl_distance > MAX_SL_DISTANCE:

            structural_sl = (
                entry
                * (1 + MAX_SL_DISTANCE)
            )

        tp = p4["price"] - rally_size

        if tp >= entry or tp <= 0:
            tp = entry * 0.99

        return {
            "entry": entry,
            "sl": structural_sl,
            "tp": tp,
        }


# ============================================================
# DB SIGNAL FUNCTIONS
# ============================================================

def count_open_trades():
    conn = db_connect()

    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM signals
        WHERE status='OPEN'
        """
    ).fetchone()

    conn.close()

    return int(row["c"] or 0)


def signal_exists(symbol, side, break_time):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
        FROM signals
        WHERE symbol=?
          AND side=?
          AND break_time=?
        LIMIT 1
        """,
        (
            symbol,
            side,
            int(break_time),
        )
    ).fetchone()

    conn.close()

    return row is not None


def insert_signal(
    symbol,
    pattern,
    breakout,
    levels
):
    conn = db_connect()

    p1 = pattern["p1"]
    p2 = pattern["p2"]
    p3 = pattern["p3"]
    p4 = pattern["p4"]

    conn.execute(
        """
        INSERT INTO signals (
            symbol,
            side,

            rally_pct,
            hook1_retrace,
            hook2_retrace,
            price_symmetry,
            time_symmetry,

            p1_time,
            p2_time,
            p3_time,
            p4_time,

            p1_price,
            p2_price,
            p3_price,
            p4_price,

            break_time,

            entry,
            sl,
            tp,

            status,

            exit_price,
            exit_time,
            exit_reason,
            pnl_pct,

            created_at
        )
        VALUES (
            ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?,
            ?, ?, ?,
            'OPEN',
            NULL, NULL, NULL, NULL,
            ?
        )
        """,
        (
            symbol,
            pattern["side"],

            pattern["rally_pct"],
            pattern["hook1_retrace"],
            pattern["hook2_retrace"],
            pattern["price_symmetry"],
            pattern["time_symmetry"],

            p1["time"],
            p2["time"],
            p3["time"],
            p4["time"],

            p1["price"],
            p2["price"],
            p3["price"],
            p4["price"],

            breakout["time"],

            levels["entry"],
            levels["sl"],
            levels["tp"],

            now_ts(),
        )
    )

    conn.commit()

    row_id = conn.execute(
        "SELECT last_insert_rowid()"
    ).fetchone()[0]

    conn.close()

    return row_id


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():
    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM signals
        WHERE status='OPEN'
        ORDER BY created_at ASC
        """
    ).fetchall()

    conn.close()

    return rows


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade_id,
    exit_price,
    exit_time,
    exit_reason
):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT side, entry
        FROM signals
        WHERE id=?
        """,
        (trade_id,)
    ).fetchone()

    if not row:
        conn.close()
        return None

    entry = float(row["entry"])
    side = row["side"]

    if side == "LONG":

        pnl_pct = (
            (exit_price - entry)
            / entry
        ) * 100

    else:

        pnl_pct = (
            (entry - exit_price)
            / entry
        ) * 100

    conn.execute(
        """
        UPDATE signals
        SET
            status='CLOSED',
            exit_price=?,
            exit_time=?,
            exit_reason=?,
            pnl_pct=?
        WHERE id=?
        """,
        (
            exit_price,
            exit_time,
            exit_reason,
            pnl_pct,
            trade_id,
        )
    )

    conn.commit()

    conn.close()

    return pnl_pct


# ============================================================
# MONITOR OPEN TRADES
# ============================================================

def monitor_open_trades(market_data):
    open_trades = get_open_trades()

    print(
        f"[MONITOR] "
        f"{len(open_trades)} open trades"
    )

    closed = []

    for trade in open_trades:

        symbol = trade["symbol"]

        candles = market_data.get(symbol)

        if not candles:
            continue

        candle = candles[-1]

        high = candle["high"]
        low = candle["low"]

        side = trade["side"]

        entry = float(trade["entry"])
        sl = float(trade["sl"])
        tp = float(trade["tp"])

        exit_price = None
        reason = None

        # ----------------------------------------------------
        # Conservative rule:
        # If SL and TP both touched in same candle,
        # assume SL first.
        # ----------------------------------------------------

        if side == "LONG":

            sl_hit = low <= sl
            tp_hit = high >= tp

            if sl_hit:
                exit_price = sl
                reason = "SL"

            elif tp_hit:
                exit_price = tp
                reason = "TP"

        else:

            sl_hit = high >= sl
            tp_hit = low <= tp

            if sl_hit:
                exit_price = sl
                reason = "SL"

            elif tp_hit:
                exit_price = tp
                reason = "TP"

        if exit_price is not None:

            pnl = close_trade(
                trade["id"],
                exit_price,
                candle["time"],
                reason
            )

            closed.append({
                "symbol": symbol,
                "side": side,
                "entry": entry,
                "exit": exit_price,
                "reason": reason,
                "pnl": pnl,
            })

            print(
                f"[CLOSE] {symbol} {side} "
                f"{reason} "
                f"P/L={pnl:+.2f}%"
            )

            send_closed_trade(
                symbol,
                side,
                entry,
                exit_price,
                reason,
                pnl
            )

    return closed


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text_message):
    if not TELEGRAM_BOT_TOKEN:
        print("[TELEGRAM] Bot token not configured")
        return False

    if not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Chat ID not configured")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:

        r = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text_message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20
        )

        if not r.ok:
            print(
                "[TELEGRAM ERROR]",
                r.text[:500]
            )
            return False

        return True

    except Exception as e:

        print(
            "[TELEGRAM ERROR]",
            e
        )

        return False


def telegram_send_photo(
    image_path,
    caption
):
    if not TELEGRAM_BOT_TOKEN:
        return False

    if not TELEGRAM_CHAT_ID:
        return False

    if not os.path.exists(image_path):
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    try:

        with open(
            image_path,
            "rb"
        ) as photo:

            r = requests.post(
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

        return r.ok

    except Exception as e:

        print(
            "[TELEGRAM PHOTO ERROR]",
            e
        )

        return False


def send_new_signal(
    symbol,
    pattern,
    breakout,
    levels
):

    side = pattern["side"]

    if side == "LONG":
        emoji = "🟢"
    else:
        emoji = "🔴"

    message = (
        f"<b>NDS NEW SIGNAL</b>\n\n"
        f"{emoji} <b>{symbol}</b> "
        f"{side}\n\n"

        f"Entry: "
        f"<code>{levels['entry']:.8g}</code>\n"

        f"SL: "
        f"<code>{levels['sl']:.8g}</code>\n"

        f"TP: "
        f"<code>{levels['tp']:.8g}</code>\n\n"

        f"Rally: "
        f"{pattern['rally_pct'] * 100:.2f}%\n"

        f"Hook: "
        f"{pattern['hook1_retrace'] * 100:.1f}%\n"

        f"Price Symmetry: "
        f"{pattern['price_symmetry']:.2f}\n"

        f"Time Symmetry: "
        f"{pattern['time_symmetry']:.2f}\n\n"

        f"Break: "
        f"{fmt_time(breakout['time'])}\n"

        f"<b>PAPER ONLY</b>"
    )

    telegram_send(message)


def send_closed_trade(
    symbol,
    side,
    entry,
    exit_price,
    reason,
    pnl
):

    if pnl >= 0:
        emoji = "✅"
    else:
        emoji = "❌"

    message = (
        f"<b>NDS CLOSED TRADE</b>\n\n"
        f"{emoji} {symbol} {side}\n\n"
        f"Entry: <code>{entry:.8g}</code>\n"
        f"Exit: <code>{exit_price:.8g}</code>\n"
        f"Reason: <b>{reason}</b>\n"
        f"P/L: <b>{pnl:+.2f}%</b>"
    )

    telegram_send(message)


# ============================================================
# PERFORMANCE
# ============================================================

def performance_stats():
    conn = db_connect()

    total = conn.execute(
        """
        SELECT COUNT(*)
        FROM signals
        WHERE status='CLOSED'
        """
    ).fetchone()[0]

    wins = conn.execute(
        """
        SELECT COUNT(*)
        FROM signals
        WHERE status='CLOSED'
        AND pnl_pct > 0
        """
    ).fetchone()[0]

    losses = conn.execute(
        """
        SELECT COUNT(*)
        FROM signals
        WHERE status='CLOSED'
        AND pnl_pct <= 0
        """
    ).fetchone()[0]

    pnl = conn.execute(
        """
        SELECT COALESCE(SUM(pnl_pct), 0)
        FROM signals
        WHERE status='CLOSED'
        """
    ).fetchone()[0]

    conn.close()

    win_rate = (
        (wins / total) * 100
        if total > 0
        else 0
    )

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "pnl": float(pnl or 0),
        "win_rate": win_rate,
    }


# ============================================================
# OPEN TRADE REPORT
# ============================================================

def send_live_report():
    trades = get_open_trades()
    stats = performance_stats()

    lines = [
        "<b>NDS LIVE REPORT</b>",
        "",
        f"Version: {VERSION}",
        f"Open trades: {len(trades)}",
        "",
    ]

    if trades:

        lines.append(
            "<b>OPEN TRADES</b>"
        )

        for t in trades:

            symbol = t["symbol"]
            side = t["side"]

            entry = float(t["entry"])
            sl = float(t["sl"])
            tp = float(t["tp"])

            lines.append(
                f"{symbol} {side}\n"
                f"Entry: {entry:.8g}\n"
                f"SL: {sl:.8g} | "
                f"TP: {tp:.8g}"
            )

    else:

        lines.append(
            "<b>OPEN TRADES: 0</b>"
        )

    lines.extend([
        "",
        "<b>PERFORMANCE</b>",
        f"Closed: {stats['total']}",
        f"Wins: {stats['wins']}",
        f"Losses: {stats['losses']}",
        f"Win rate: {stats['win_rate']:.2f}%",
        f"Total P/L: {stats['pnl']:+.2f}%",
    ])

    telegram_send(
        "\n".join(lines)
    )


# ============================================================
# CHART
# ============================================================

def make_chart(
    symbol,
    candles,
    pattern,
    breakout,
    levels
):

    os.makedirs(
        CHART_DIR,
        exist_ok=True
    )

    side = pattern["side"]

    p1 = pattern["p1"]
    p2 = pattern["p2"]
    p3 = pattern["p3"]
    p4 = pattern["p4"]

    break_time = breakout["time"]

    # --------------------------------------------------------
    # Only recent candles around setup.
    # --------------------------------------------------------

    selected = [
        c for c in candles
        if c["time"] >=
        p1["time"] - CHART_LEFT * 3600
    ]

    if len(selected) > CHART_LEFT + CHART_RIGHT:
        selected = selected[
            -(CHART_LEFT + CHART_RIGHT):
        ]

    if not selected:
        return None

    dates = [
        datetime.fromtimestamp(
            c["time"],
            tz=timezone.utc
        )
        for c in selected
    ]

    closes = [
        c["close"]
        for c in selected
    ]

    highs = [
        c["high"]
        for c in selected
    ]

    lows = [
        c["low"]
        for c in selected
    ]

    fig, ax = plt.subplots(
        figsize=(14, 7)
    )

    # --------------------------------------------------------
    # Candles
    # --------------------------------------------------------

    width = 0.025

    for d, c in zip(dates, selected):

        x = mdates.date2num(d)

        # Wick
        ax.plot(
            [x, x],
            [c["low"], c["high"]]
        )

        # Body
        bottom = min(
            c["open"],
            c["close"]
        )

        height = abs(
            c["close"] - c["open"]
        )

        if height == 0:
            height = max(
                c["close"] * 0.0001,
                1e-12
            )

        ax.bar(
            x,
            height,
            bottom=bottom,
            width=width,
            align="center"
        )

    # --------------------------------------------------------
    # Trendline P2 -> P4
    # --------------------------------------------------------

    line_times = [
        selected[0]["time"],
        selected[-1]["time"],
    ]

    line_prices = [
        trendline_price(
            pattern,
            t
        )
        for t in line_times
    ]

    ax.plot(
        [
            mdates.date2num(
                datetime.fromtimestamp(
                    t,
                    tz=timezone.utc
                )
            )
            for t in line_times
        ],
        line_prices,
        linewidth=2,
        label="NDS Trendline"
    )

    # --------------------------------------------------------
    # Expected rally
    # --------------------------------------------------------

    rally_size = abs(
        p2["price"] - p1["price"]
    )

    if side == "LONG":

        expected_tp = (
            p4["price"]
            + rally_size
        )

    else:

        expected_tp = (
            p4["price"]
            - rally_size
        )

    ax.axhline(
        expected_tp,
        linestyle="--",
        linewidth=1.2,
        label="Expected Rally TP"
    )

    # --------------------------------------------------------
    # Important points
    # --------------------------------------------------------

    points = [
        ("P1", p1),
        ("P2", p2),
        ("P3", p3),
        ("P4", p4),
    ]

    for label, p in points:

        x = mdates.date2num(
            datetime.fromtimestamp(
                p["time"],
                tz=timezone.utc
            )
        )

        ax.scatter(
            x,
            p["price"],
            s=60,
            zorder=5
        )

        offset = (
            10
            if p["type"] == "LOW"
            else -15
        )

        ax.annotate(
            label,
            (x, p["price"]),
            xytext=(0, offset),
            textcoords="offset points",
            ha="center",
            fontweight="bold"
        )

    # --------------------------------------------------------
    # Breakout
    # --------------------------------------------------------

    bx = mdates.date2num(
        datetime.fromtimestamp(
            breakout["time"],
            tz=timezone.utc
        )
    )

    ax.scatter(
        bx,
        breakout["price"],
        marker="*",
        s=180,
        zorder=8,
        label="BREAKOUT"
    )

    ax.annotate(
        "BREAK",
        (bx, breakout["price"]),
        xytext=(0, 15),
        textcoords="offset points",
        ha="center",
        fontweight="bold"
    )

    # --------------------------------------------------------
    # Entry / SL / TP
    # --------------------------------------------------------

    ax.axhline(
        levels["entry"],
        linestyle="-.",
        linewidth=1.2,
        label="Entry"
    )

    ax.axhline(
        levels["sl"],
        linestyle="--",
        linewidth=1.2,
        label="SL"
    )

    ax.axhline(
        levels["tp"],
        linestyle="--",
        linewidth=1.2,
        label="TP"
    )

    # --------------------------------------------------------
    # Titles
    # --------------------------------------------------------

    ax.set_title(
        f"NDS {symbol} {side} | "
        f"Rally={pattern['rally_pct'] * 100:.2f}% | "
        f"Hook={pattern['hook1_retrace'] * 100:.1f}% | "
        f"PS={pattern['price_symmetry']:.2f} | "
        f"TS={pattern['time_symmetry']:.2f}"
    )

    ax.set_ylabel("Price")

    ax.xaxis.set_major_formatter(
        mdates.DateFormatter(
            "%m-%d %H:%M",
            tz=timezone.utc
        )
    )

    plt.xticks(rotation=30)

    ax.grid(
        True,
        alpha=0.25
    )

    ax.legend(
        loc="best"
    )

    plt.tight_layout()

    safe_symbol = symbol.replace(
        "/",
        "_"
    )

    filename = (
        f"{safe_symbol}_"
        f"{side}_"
        f"{break_time}.png"
    )

    path = os.path.join(
        CHART_DIR,
        filename
    )

    plt.savefig(
        path,
        dpi=140
    )

    plt.close()

    return path


# ============================================================
# SCAN ONE SYMBOL
# ============================================================

def scan_symbol(
    symbol,
    candles
):

    print(
        f"[SCAN] {symbol}"
    )

    if not candles or len(candles) < 50:

        print(
            f"[NO DATA] {symbol}"
        )

        return None

    highs, lows = find_pivots(
        candles
    )

    pivots = clean_pivots(
        highs + lows
    )

    pivots.sort(
        key=lambda x: x["index"]
    )

    pattern = calculate_pattern(
        pivots
    )

    if not pattern:

        print(
            f"[NO NDS] {symbol}"
        )

        return None

    print(
        f"[NDS] {symbol} "
        f"{pattern['side']} "
        f"Rally={pattern['rally_pct'] * 100:.2f}% "
        f"Hook={pattern['hook1_retrace'] * 100:.1f}% "
        f"PS={pattern['price_symmetry']:.2f} "
        f"TS={pattern['time_symmetry']:.2f}"
    )

    breakout = find_breakout(
        candles,
        pattern
    )

    if not breakout:

        print(
            f"[NO BREAK] {symbol}"
        )

        return None

    age_hours = (
        now_ts()
        - breakout["time"]
    ) / 3600

    print(
        f"[BREAK] {symbol} "
        f"{pattern['side']} "
        f"age={age_hours:.2f}h"
    )

    if age_hours < 0:
        return None

    if age_hours > MAX_SIGNAL_AGE_HOURS:

        print(
            f"[OLD] {symbol} signal rejected"
        )

        return None

    # --------------------------------------------------------
    # Avoid duplicate signal.
    # --------------------------------------------------------

    if signal_exists(
        symbol,
        pattern["side"],
        breakout["time"]
    ):

        print(
            f"[DUPLICATE] {symbol}"
        )

        return None

    # --------------------------------------------------------
    # Max open trades.
    # --------------------------------------------------------

    current_open = count_open_trades()

    if current_open >= MAX_OPEN_TRADES:

        print(
            f"[MAX OPEN] {symbol}"
        )

        return None

    levels = find_levels(
        candles,
        pattern,
        breakout
    )

    print(
        f"[SIGNAL] {symbol} "
        f"{pattern['side']} "
        f"Entry={levels['entry']:.8g} "
        f"SL={levels['sl']:.8g} "
        f"TP={levels['tp']:.8g}"
    )

    # --------------------------------------------------------
    # Insert DB
    # --------------------------------------------------------

    signal_id = insert_signal(
        symbol,
        pattern,
        breakout,
        levels
    )

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    send_new_signal(
        symbol,
        pattern,
        breakout,
        levels
    )

    # --------------------------------------------------------
    # Chart
    # --------------------------------------------------------

    chart_path = make_chart(
        symbol,
        candles,
        pattern,
        breakout,
        levels
    )

    if chart_path:

        side = pattern["side"]

        if side == "LONG":
            emoji = "🟢"
        else:
            emoji = "🔴"

        caption = (
            f"NDS {emoji} "
            f"{symbol} {side}\n"
            f"Entry: {levels['entry']:.8g}\n"
            f"SL: {levels['sl']:.8g}\n"
            f"TP: {levels['tp']:.8g}"
        )

        telegram_send_photo(
            chart_path,
            caption
        )

    return {
        "id": signal_id,
        "symbol": symbol,
        "side": pattern["side"],
        "entry": levels["entry"],
        "sl": levels["sl"],
        "tp": levels["tp"],
    }


# ============================================================
# MAIN SCANNER
# ============================================================

def run_scanner():

    print("=" * 65)
    print(
        f"NDS LIVE SCANNER v{VERSION}"
    )
    print("=" * 65)

    print(
        "PAPER ONLY:",
        not REAL_TRADING
    )

    # --------------------------------------------------------
    # Database
    # --------------------------------------------------------

    migrate_db()

    # --------------------------------------------------------
    # Fetch market data ONCE.
    #
    # This fixes the previous problem where candles could be
    # downloaded twice for every asset.
    # --------------------------------------------------------

    market_data = {}

    errors = 0

    for symbol in SYMBOLS:

        try:

            candles = fetch_candles(
                symbol
            )

            market_data[symbol] = candles

            print(
                f"[DATA] {symbol}: "
                f"{len(candles)} candles"
            )

        except Exception as e:

            errors += 1

            print(
                f"[DATA ERROR] "
                f"{symbol}: {e}"
            )

    # --------------------------------------------------------
    # Monitor existing trades first.
    # --------------------------------------------------------

    monitor_open_trades(
        market_data
    )

    # --------------------------------------------------------
    # Scan all symbols.
    # --------------------------------------------------------

    new_long = 0
    new_short = 0
    scan_errors = 0

    for symbol in SYMBOLS:

        candles = market_data.get(
            symbol
        )

        if not candles:
            continue

        try:

            result = scan_symbol(
                symbol,
                candles
            )

            if result:

                if result["side"] == "LONG":
                    new_long += 1

                else:
                    new_short += 1

        except Exception as e:

            scan_errors += 1

            print(
                f"[ERROR] {symbol}: {e}"
            )

    # --------------------------------------------------------
    # Final Telegram report.
    # --------------------------------------------------------

    send_live_report()

    # --------------------------------------------------------
    # Console report.
    # --------------------------------------------------------

    stats = performance_stats()

    open_count = count_open_trades()

    print()
    print("=" * 65)
    print("NDS LIVE REPORT")
    print("=" * 65)

    print(
        f"Version: {VERSION}"
    )

    print(
        f"Assets scanned: "
        f"{len(SYMBOLS)}"
    )

    print(
        f"New LONG: "
        f"{new_long}"
    )

    print(
        f"New SHORT: "
        f"{new_short}"
    )

    print(
        f"Open trades: "
        f"{open_count}"
    )

    print(
        f"Closed trades: "
        f"{stats['total']}"
    )

    print(
        f"Wins: "
        f"{stats['wins']}"
    )

    print(
        f"Losses: "
        f"{stats['losses']}"
    )

    print(
        f"Win rate: "
        f"{stats['win_rate']:.2f}%"
    )

    print(
        f"Total P/L: "
        f"{stats['pnl']:+.2f}%"
    )

    print(
        f"API errors: "
        f"{errors}"
    )

    print(
        f"Scan errors: "
        f"{scan_errors}"
    )

    print("=" * 65)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        run_scanner()

    except Exception as e:

        print(
            "[FATAL ERROR]",
            repr(e)
        )

        raise
