# ============================================================
# KRAKEN FUTURES NDS LIVE SCANNER
# VERSION 1.1
# ============================================================
#
# PAPER ONLY
#
# NDS STRUCTURE:
#
#   LONG:
#       P1 LOW
#        |
#        |  RALLY
#        v
#       P2 HIGH
#        |
#        |  HOOK 1
#        v
#       P3 LOW
#        |
#        |  HOOK 2
#        v
#       P4 HIGH
#        |
#        |  EXPECTED RALLY
#        v
#       BREAKOUT
#        |
#       ENTRY
#
#   SHORT:
#       P1 HIGH
#        |
#        |  RALLY
#        v
#       P2 LOW
#        |
#        |  HOOK 1
#        v
#       P3 HIGH
#        |
#        |  HOOK 2
#        v
#       P4 LOW
#        |
#        |  EXPECTED RALLY
#        v
#       BREAKDOWN
#        |
#       ENTRY
#
# FEATURES:
#   - Kraken Futures 1H
#   - NDS Node detection
#   - Rally / Hook 1 / Hook 2
#   - Price symmetry
#   - Time symmetry
#   - Closed candle confirmation
#   - Structural SL
#   - Rally-based TP
#   - SQLite database
#   - Telegram alerts
#   - NDS chart for every new signal
#   - Open trade monitoring
#   - TP / SL detection
#   - Duplicate protection
#
# ============================================================

import os
import time
import sqlite3
import math
import traceback
from datetime import datetime, timezone

import requests

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


# ============================================================
# CONFIG
# ============================================================

VERSION = "1.1"

BASE_URL = "https://futures.kraken.com"

TIMEFRAME = "1h"

CANDLE_LIMIT = 500

DB_FILE = "nds_v11.db"

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

# Only recent setups can generate signals.
MAX_SIGNAL_AGE_HOURS = 2

# Number of candles displayed in chart around setup.
CHART_LEFT_CANDLES = 35
CHART_RIGHT_CANDLES = 15

# ------------------------------------------------------------
# SCAN SETTINGS
# ------------------------------------------------------------

SCAN_INTERVAL_SECONDS = 300

# ------------------------------------------------------------
# ASSETS
# ------------------------------------------------------------

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

TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("TELEGRAM_TOKEN")
)

TELEGRAM_CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or os.getenv("TELEGRAM_CHAT")
)


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat()


def fmt_time(ts):
    try:
        return datetime.fromtimestamp(
            float(ts),
            tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return "-"


# ============================================================
# NUMBER HELPERS
# ============================================================

def safe_float(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def pct_change(a, b):
    if a is None or b is None or a == 0:
        return 0.0
    return abs(b - a) / abs(a)


def ratio(a, b):
    if b is None or b == 0:
        return None
    return abs(a) / abs(b)


# ============================================================
# KRAKEN DATA
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "NDS-Live-Scanner/1.1"
})


def fetch_candles(symbol, interval="1h", limit=500):

    url = (
        f"{BASE_URL}/api/charts/v1/trade/"
        f"{symbol}/{interval}"
    )

    try:

        response = session.get(
            url,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

    except Exception as e:

        print(
            f"[ERROR] candle fetch "
            f"{symbol}: {e}"
        )

        return []


    rows = data.get("candles")

    if rows is None:

        if isinstance(data, list):
            rows = data
        else:
            return []


    candles = []

    for row in rows:

        try:

            if isinstance(row, dict):

                ts = (
                    row.get("time")
                    or row.get("timestamp")
                    or row.get("t")
                )

                o = (
                    row.get("open")
                    or row.get("o")
                )

                h = (
                    row.get("high")
                    or row.get("h")
                )

                l = (
                    row.get("low")
                    or row.get("l")
                )

                c = (
                    row.get("close")
                    or row.get("c")
                )

                volume = (
                    row.get("volume")
                    or row.get("v")
                    or 0
                )

            else:

                if len(row) < 5:
                    continue

                ts = row[0]
                o = row[1]
                h = row[2]
                l = row[3]
                c = row[4]

                volume = (
                    row[5]
                    if len(row) > 5
                    else 0
                )


            ts = safe_float(ts)

            if ts is None:
                continue

            # Kraken can return milliseconds.
            if ts > 10_000_000_000:
                ts /= 1000.0


            candle = {
                "ts": ts,
                "open": safe_float(o),
                "high": safe_float(h),
                "low": safe_float(l),
                "close": safe_float(c),
                "volume": safe_float(volume, 0.0),
            }


            if None in (
                candle["open"],
                candle["high"],
                candle["low"],
                candle["close"]
            ):
                continue


            candles.append(candle)

        except Exception:
            continue


    candles.sort(
        key=lambda x: x["ts"]
    )


    # --------------------------------------------------------
    # Remove current unfinished candle.
    # --------------------------------------------------------

    now = time.time()

    closed = []

    for candle in candles:

        # 1H candle must be completely closed.
        if candle["ts"] + 3600 <= now:

            closed.append(candle)


    return closed[-limit:]


# ============================================================
# PIVOTS / NODES
# ============================================================

def detect_raw_nodes(candles):

    nodes = []

    n = len(candles)

    start = PIVOT_LEFT

    end = n - PIVOT_RIGHT

    for i in range(start, end):

        current = candles[i]

        is_high = True
        is_low = True


        for j in range(
            i - PIVOT_LEFT,
            i + PIVOT_RIGHT + 1
        ):

            if j == i:
                continue

            if candles[j]["high"] >= current["high"]:
                is_high = False

            if candles[j]["low"] <= current["low"]:
                is_low = False


        if is_high:

            nodes.append({
                "index": i,
                "ts": current["ts"],
                "price": current["high"],
                "type": "HIGH",
            })


        elif is_low:

            nodes.append({
                "index": i,
                "ts": current["ts"],
                "price": current["low"],
                "type": "LOW",
            })


    nodes.sort(
        key=lambda x: x["index"]
    )

    return nodes


def clean_nodes(nodes):

    if not nodes:
        return []


    result = []

    for node in nodes:

        if not result:

            result.append(node)
            continue


        previous = result[-1]


        # ----------------------------------------------------
        # Two consecutive highs:
        # keep higher high.
        # ----------------------------------------------------

        if (
            previous["type"] == "HIGH"
            and node["type"] == "HIGH"
        ):

            if node["price"] > previous["price"]:
                result[-1] = node

            continue


        # ----------------------------------------------------
        # Two consecutive lows:
        # keep lower low.
        # ----------------------------------------------------

        if (
            previous["type"] == "LOW"
            and node["type"] == "LOW"
        ):

            if node["price"] < previous["price"]:
                result[-1] = node

            continue


        result.append(node)


    return result


# ============================================================
# LEG INFORMATION
# ============================================================

def leg_pct(a, b):

    if a == 0:
        return 0.0

    return abs(b - a) / abs(a)


def bars_between(a, b):

    return abs(
        b["index"] - a["index"]
    )


# ============================================================
# NDS SETUP VALIDATION
# ============================================================

def validate_long_pattern(
    p1,
    p2,
    p3,
    p4
):

    # --------------------------------------------------------
    # Structure:
    #
    # P1 LOW
    # P2 HIGH
    # P3 LOW
    # P4 HIGH
    # --------------------------------------------------------

    if not (
        p1["type"] == "LOW"
        and p2["type"] == "HIGH"
        and p3["type"] == "LOW"
        and p4["type"] == "HIGH"
    ):
        return None


    rally = p2["price"] - p1["price"]

    if rally <= 0:
        return None


    rally_pct = rally / p1["price"]

    if rally_pct < MIN_RALLY_PCT:
        return None


    # --------------------------------------------------------
    # Hook 1:
    # P2 -> P3
    # --------------------------------------------------------

    hook1 = p2["price"] - p3["price"]

    if hook1 <= 0:
        return None


    hook1_retrace = (
        hook1 / rally
    )


    if not (
        MIN_HOOK_RETRACE
        <= hook1_retrace
        <= MAX_HOOK_RETRACE
    ):
        return None


    # --------------------------------------------------------
    # Hook 2:
    # P3 -> P4
    #
    # This is measured against Hook 1.
    # --------------------------------------------------------

    hook2 = p4["price"] - p3["price"]

    if hook2 <= 0:
        return None


    price_sym = ratio(
        hook2,
        hook1
    )

    if price_sym is None:
        return None


    if not (
        MIN_PRICE_SYMMETRY
        <= price_sym
        <= MAX_PRICE_SYMMETRY
    ):
        return None


    time1 = bars_between(
        p2,
        p3
    )

    time2 = bars_between(
        p3,
        p4
    )


    if time1 <= 0:
        return None


    time_sym = time2 / time1


    if not (
        MIN_TIME_SYMMETRY
        <= time_sym
        <= MAX_TIME_SYMMETRY
    ):
        return None


    return {
        "side": "LONG",

        "p1": p1,
        "p2": p2,
        "p3": p3,
        "p4": p4,

        "rally_pct": rally_pct,

        "rally_size": rally,

        "hook1_size": hook1,

        "hook2_size": hook2,

        "hook1_retrace": hook1_retrace,

        "price_symmetry": price_sym,

        "time_symmetry": time_sym,
    }


def validate_short_pattern(
    p1,
    p2,
    p3,
    p4
):

    # --------------------------------------------------------
    # Structure:
    #
    # P1 HIGH
    # P2 LOW
    # P3 HIGH
    # P4 LOW
    # --------------------------------------------------------

    if not (
        p1["type"] == "HIGH"
        and p2["type"] == "LOW"
        and p3["type"] == "HIGH"
        and p4["type"] == "LOW"
    ):
        return None


    rally = p1["price"] - p2["price"]

    if rally <= 0:
        return None


    rally_pct = rally / p1["price"]

    if rally_pct < MIN_RALLY_PCT:
        return None


    # --------------------------------------------------------
    # Hook 1:
    # P2 -> P3
    # --------------------------------------------------------

    hook1 = p3["price"] - p2["price"]

    if hook1 <= 0:
        return None


    hook1_retrace = (
        hook1 / rally
    )


    if not (
        MIN_HOOK_RETRACE
        <= hook1_retrace
        <= MAX_HOOK_RETRACE
    ):
        return None


    # --------------------------------------------------------
    # Hook 2:
    # P3 -> P4
    # --------------------------------------------------------

    hook2 = p3["price"] - p4["price"]

    if hook2 <= 0:
        return None


    price_sym = ratio(
        hook2,
        hook1
    )


    if price_sym is None:
        return None


    if not (
        MIN_PRICE_SYMMETRY
        <= price_sym
        <= MAX_PRICE_SYMMETRY
    ):
        return None


    time1 = bars_between(
        p2,
        p3
    )

    time2 = bars_between(
        p3,
        p4
    )


    if time1 <= 0:
        return None


    time_sym = time2 / time1


    if not (
        MIN_TIME_SYMMETRY
        <= time_sym
        <= MAX_TIME_SYMMETRY
    ):
        return None


    return {
        "side": "SHORT",

        "p1": p1,
        "p2": p2,
        "p3": p3,
        "p4": p4,

        "rally_pct": rally_pct,

        "rally_size": rally,

        "hook1_size": hook1,

        "hook2_size": hook2,

        "hook1_retrace": hook1_retrace,

        "price_symmetry": price_sym,

        "time_symmetry": time_sym,
    }


# ============================================================
# FIND MOST RECENT NDS SETUP
# ============================================================

def find_nds_setup(candles):

    nodes = detect_raw_nodes(candles)

    nodes = clean_nodes(nodes)

    if len(nodes) < 4:
        return None


    # --------------------------------------------------------
    # Search backwards.
    # Most recent valid setup gets priority.
    # --------------------------------------------------------

    for i in range(
        len(nodes) - 4,
        -1,
        -1
    ):

        p1 = nodes[i]
        p2 = nodes[i + 1]
        p3 = nodes[i + 2]
        p4 = nodes[i + 3]


        long_setup = validate_long_pattern(
            p1,
            p2,
            p3,
            p4
        )


        if long_setup:

            return long_setup


        short_setup = validate_short_pattern(
            p1,
            p2,
            p3,
            p4
        )


        if short_setup:

            return short_setup


    return None


# ============================================================
# BREAKOUT CONFIRMATION
# ============================================================

def confirm_setup(
    setup,
    candles
):

    if setup is None:
        return None


    p4 = setup["p4"]

    side = setup["side"]


    # --------------------------------------------------------
    # Only candles after P4 can confirm the expected Rally.
    # --------------------------------------------------------

    candidates = [
        c for c in candles
        if c["ts"] > p4["ts"]
    ]


    if not candidates:
        return None


    for candle in candidates:

        close = candle["close"]


        if side == "LONG":

            trigger = (
                p4["price"]
                * (1 + BREAK_BUFFER)
            )


            if close > trigger:

                entry = close

                return {
                    **setup,

                    "signal_ts": candle["ts"],

                    "entry": entry,

                    "break_price": p4["price"],

                    "break_candle": candle,
                }


        else:

            trigger = (
                p4["price"]
                * (1 - BREAK_BUFFER)
            )


            if close < trigger:

                entry = close

                return {
                    **setup,

                    "signal_ts": candle["ts"],

                    "entry": entry,

                    "break_price": p4["price"],

                    "break_candle": candle,
                }


    return None


# ============================================================
# TARGET / STOP
# ============================================================

def calculate_trade_levels(
    signal,
    candles
):

    side = signal["side"]

    entry = signal["entry"]

    p1 = signal["p1"]
    p2 = signal["p2"]
    p3 = signal["p3"]
    p4 = signal["p4"]


    if side == "LONG":

        # ----------------------------------------------------
        # Structural SL:
        # below Hook 2 low / lowest meaningful node.
        # ----------------------------------------------------

        structural_low = min(
            p1["price"],
            p3["price"]
        )


        sl = (
            structural_low
            * (1 - SL_BUFFER)
        )


        # ----------------------------------------------------
        # TP:
        # initial Rally size projected from entry.
        # ----------------------------------------------------

        tp = (
            entry
            + signal["rally_size"]
        )


        risk = entry - sl

        reward = tp - entry


    else:

        structural_high = max(
            p1["price"],
            p3["price"]
        )


        sl = (
            structural_high
            * (1 + SL_BUFFER)
        )


        tp = (
            entry
            - signal["rally_size"]
        )


        risk = sl - entry

        reward = entry - tp


    if risk <= 0:
        return None


    sl_distance = risk / entry


    if not (
        MIN_SL_DISTANCE
        <= sl_distance
        <= MAX_SL_DISTANCE
    ):
        return None


    rr = (
        reward / risk
        if risk > 0
        else 0
    )


    signal["sl"] = sl
    signal["tp"] = tp
    signal["risk"] = risk
    signal["reward"] = reward
    signal["rr"] = rr

    return signal


# ============================================================
# DATABASE
# ============================================================

def db():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = db()

    cur = conn.cursor()


    cur.execute("""
        CREATE TABLE IF NOT EXISTS signals (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT NOT NULL,

            side TEXT NOT NULL,

            signal_time REAL NOT NULL,

            entry REAL NOT NULL,

            sl REAL NOT NULL,

            tp REAL NOT NULL,

            rr REAL,

            rally_pct REAL,

            hook1_retrace REAL,

            price_symmetry REAL,

            time_symmetry REAL,

            p1_time REAL,
            p1_price REAL,

            p2_time REAL,
            p2_price REAL,

            p3_time REAL,
            p3_price REAL,

            p4_time REAL,
            p4_price REAL,

            break_price REAL,

            status TEXT DEFAULT 'OPEN',

            exit_time REAL,

            exit_price REAL,

            exit_reason TEXT,

            pnl_pct REAL,

            chart_file TEXT,

            created_at TEXT
        )
    """)


    cur.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_signals_status
        ON signals(status)
    """)


    cur.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_signals_symbol_time
        ON signals(symbol, signal_time)
    """)


    conn.commit()

    conn.close()


# ============================================================
# DUPLICATE CHECK
# ============================================================

def signal_exists(
    symbol,
    side,
    signal_time
):

    conn = db()

    cur = conn.cursor()


    cur.execute("""
        SELECT id
        FROM signals
        WHERE symbol = ?
          AND side = ?
          AND signal_time = ?
        LIMIT 1
    """, (
        symbol,
        side,
        signal_time
    ))


    row = cur.fetchone()

    conn.close()

    return row is not None


# ============================================================
# OPEN TRADE CHECK
# ============================================================

def has_open_trade(symbol):

    conn = db()

    cur = conn.cursor()


    cur.execute("""
        SELECT id
        FROM signals
        WHERE symbol = ?
          AND status = 'OPEN'
        LIMIT 1
    """, (
        symbol,
    ))


    row = cur.fetchone()

    conn.close()

    return row is not None


def count_open_trades():

    conn = db()

    cur = conn.cursor()


    cur.execute("""
        SELECT COUNT(*)
        FROM signals
        WHERE status = 'OPEN'
    """)


    count = cur.fetchone()[0]

    conn.close()

    return int(count)


# ============================================================
# INSERT SIGNAL
# ============================================================

def insert_signal(
    symbol,
    signal,
    chart_file=None
):

    conn = db()

    cur = conn.cursor()


    p1 = signal["p1"]
    p2 = signal["p2"]
    p3 = signal["p3"]
    p4 = signal["p4"]


    cur.execute("""
        INSERT INTO signals (

            symbol,
            side,
            signal_time,

            entry,
            sl,
            tp,
            rr,

            rally_pct,
            hook1_retrace,

            price_symmetry,
            time_symmetry,

            p1_time,
            p1_price,

            p2_time,
            p2_price,

            p3_time,
            p3_price,

            p4_time,
            p4_price,

            break_price,

            status,

            chart_file,

            created_at
        )

        VALUES (
            ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?,
            'OPEN',
            ?,
            ?
        )
    """, (

        symbol,
        signal["side"],
        signal["signal_ts"],

        signal["entry"],
        signal["sl"],
        signal["tp"],
        signal["rr"],

        signal["rally_pct"],
        signal["hook1_retrace"],

        signal["price_symmetry"],
        signal["time_symmetry"],

        p1["ts"],
        p1["price"],

        p2["ts"],
        p2["price"],

        p3["ts"],
        p3["price"],

        p4["ts"],
        p4["price"],

        signal["break_price"],

        chart_file,

        iso_now()
    ))


    signal_id = cur.lastrowid

    conn.commit()

    conn.close()

    return signal_id


# ============================================================
# UPDATE TRADE
# ============================================================

def close_trade(
    trade_id,
    exit_time,
    exit_price,
    reason,
    pnl_pct
):

    conn = db()

    cur = conn.cursor()


    cur.execute("""
        UPDATE signals

        SET
            status = 'CLOSED',
            exit_time = ?,
            exit_price = ?,
            exit_reason = ?,
            pnl_pct = ?

        WHERE id = ?
          AND status = 'OPEN'
    """, (
        exit_time,
        exit_price,
        reason,
        pnl_pct,
        trade_id
    ))


    conn.commit()

    conn.close()


# ============================================================
# GET OPEN TRADES
# ============================================================

def get_open_trades():

    conn = db()

    cur = conn.cursor()


    cur.execute("""
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY signal_time ASC
    """)


    rows = cur.fetchall()

    conn.close()

    return rows


# ============================================================
# TRADE MONITOR
# ============================================================

def monitor_open_trades():

    trades = get_open_trades()

    if not trades:
        return


    print(
        f"\n[MONITOR] "
        f"Open trades: {len(trades)}"
    )


    for trade in trades:

        symbol = trade["symbol"]

        candles = fetch_candles(
            symbol,
            TIMEFRAME,
            10
        )


        if not candles:
            continue


        latest = candles[-1]

        high = latest["high"]
        low = latest["low"]

        entry = trade["entry"]
        sl = trade["sl"]
        tp = trade["tp"]

        side = trade["side"]


        hit = None
        exit_price = None


        if side == "LONG":

            hit_sl = low <= sl
            hit_tp = high >= tp


            # Conservative rule:
            # if both are touched inside the same candle,
            # assume SL happened first.

            if hit_sl:

                hit = "SL"
                exit_price = sl

            elif hit_tp:

                hit = "TP"
                exit_price = tp


        else:

            hit_sl = high >= sl
            hit_tp = low <= tp


            if hit_sl:

                hit = "SL"
                exit_price = sl

            elif hit_tp:

                hit = "TP"
                exit_price = tp


        if hit is None:

            if side == "LONG":

                pnl = (
                    (latest["close"] - entry)
                    / entry
                ) * 100

            else:

                pnl = (
                    (entry - latest["close"])
                    / entry
                ) * 100


            print(
                f"{symbol} {side} "
                f"P/L {pnl:+.2f}%"
            )

            continue


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


        close_trade(
            trade["id"],
            latest["ts"],
            exit_price,
            hit,
            pnl_pct
        )


        message = (
            f"{'🟢' if side == 'LONG' else '🔴'} "
            f"NDS {side} CLOSED\n\n"

            f"#{symbol}\n"

            f"Exit: {exit_price:g}\n"
            f"Reason: {hit}\n"
            f"P/L: {pnl_pct:+.2f}%"
        )


        send_telegram_message(message)


# ============================================================
# CHART HELPERS
# ============================================================

def chart_price_range(candles):

    prices = []

    for c in candles:

        prices.extend([
            c["high"],
            c["low"]
        ])


    if not prices:
        return 0, 1


    low = min(prices)
    high = max(prices)

    margin = (
        high - low
    ) * 0.08


    if margin == 0:
        margin = high * 0.02


    return (
        low - margin,
        high + margin
    )


def draw_segment(
    ax,
    a,
    b,
    label,
    linestyle="-"
):

    xs = [
        datetime.fromtimestamp(
            a["ts"],
            tz=timezone.utc
        ),
        datetime.fromtimestamp(
            b["ts"],
            tz=timezone.utc
        )
    ]

    ys = [
        a["price"],
        b["price"]
    ]


    ax.plot(
        xs,
        ys,
        linewidth=2,
        linestyle=linestyle
    )


    mid_x = xs[0] + (
        xs[1] - xs[0]
    ) / 2


    mid_y = (
        ys[0] + ys[1]
    ) / 2


    ax.annotate(
        label,
        (
            mid_x,
            mid_y
        ),
        xytext=(0, 10),
        textcoords="offset points",
        ha="center",
        fontsize=10,
        fontweight="bold"
    )


# ============================================================
# CREATE NDS CHART
# ============================================================

def create_nds_chart(
    symbol,
    signal,
    candles
):

    p1 = signal["p1"]
    p2 = signal["p2"]
    p3 = signal["p3"]
    p4 = signal["p4"]


    signal_index = None

    for i, c in enumerate(candles):

        if (
            c["ts"]
            == signal["signal_ts"]
        ):
            signal_index = i
            break


    if signal_index is None:

        signal_index = len(candles) - 1


    start = max(
        0,
        signal_index - CHART_LEFT_CANDLES
    )


    end = min(
        len(candles),
        signal_index + CHART_RIGHT_CANDLES + 1
    )


    chart_candles = candles[
        start:end
    ]


    if not chart_candles:
        return None


    fig, ax = plt.subplots(
        figsize=(15, 8)
    )


    # --------------------------------------------------------
    # Candles
    # --------------------------------------------------------

    for candle in chart_candles:

        x = datetime.fromtimestamp(
            candle["ts"],
            tz=timezone.utc
        )

        o = candle["open"]
        h = candle["high"]
        l = candle["low"]
        c = candle["close"]


        # Candle body

        ax.plot(
            [x, x],
            [l, h],
            linewidth=1
        )


        body_low = min(o, c)
        body_high = max(o, c)


        width = 0.025


        rect = plt.Rectangle(
            (
                mdates.date2num(x) - width / 2,
                body_low
            ),
            width,
            max(
                body_high - body_low,
                (h - l) * 0.01
            ),
            fill=False,
            linewidth=1.2
        )


        ax.add_patch(rect)


    # --------------------------------------------------------
    # NDS segments
    # --------------------------------------------------------

    draw_segment(
        ax,
        p1,
        p2,
        "RALLY"
    )


    draw_segment(
        ax,
        p2,
        p3,
        "HOOK 1",
        "--"
    )


    draw_segment(
        ax,
        p3,
        p4,
        "HOOK 2",
        "--"
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


    for label, point in points:

        x = datetime.fromtimestamp(
            point["ts"],
            tz=timezone.utc
        )


        ax.scatter(
            [x],
            [point["price"]],
            s=90,
            zorder=5
        )


        offset = (
            12
            if point["type"] == "LOW"
            else -18
        )


        ax.annotate(
            label,
            (
                x,
                point["price"]
            ),
            xytext=(0, offset),
            textcoords="offset points",
            ha="center",
            fontsize=11,
            fontweight="bold"
        )


    # --------------------------------------------------------
    # Breakout
    # --------------------------------------------------------

    break_candle = signal[
        "break_candle"
    ]


    bx = datetime.fromtimestamp(
        break_candle["ts"],
        tz=timezone.utc
    )


    ax.axhline(
        signal["break_price"],
        linestyle=":",
        linewidth=1.5
    )


    ax.scatter(
        [bx],
        [signal["entry"]],
        s=120,
        zorder=6
    )


    ax.annotate(
        "BREAK / ENTRY",
        (
            bx,
            signal["entry"]
        ),
        xytext=(8, 15),
        textcoords="offset points",
        fontsize=11,
        fontweight="bold"
    )


    # --------------------------------------------------------
    # Entry
    # --------------------------------------------------------

    ax.axhline(
        signal["entry"],
        linestyle="-.",
        linewidth=1
    )


    # --------------------------------------------------------
    # SL
    # --------------------------------------------------------

    ax.axhline(
        signal["sl"],
        linestyle="--",
        linewidth=1.5
    )


    ax.annotate(
        f"SL {signal['sl']:.6g}",
        (
            chart_candles[-1]["ts"],
            signal["sl"]
        ),
        xytext=(-80, 0),
        textcoords="offset points",
        fontsize=10,
        fontweight="bold"
    )


    # --------------------------------------------------------
    # TP
    # --------------------------------------------------------

    ax.axhline(
        signal["tp"],
        linestyle="--",
        linewidth=1.5
    )


    ax.annotate(
        f"TP {signal['tp']:.6g}",
        (
            chart_candles[-1]["ts"],
            signal["tp"]
        ),
        xytext=(-80, 0),
        textcoords="offset points",
        fontsize=10,
        fontweight="bold"
    )


    # --------------------------------------------------------
    # Expected Rally arrow / line
    # --------------------------------------------------------

    entry_x = datetime.fromtimestamp(
        signal["signal_ts"],
        tz=timezone.utc
    )


    ax.annotate(
        "EXPECTED RALLY",
        (
            entry_x,
            signal["entry"]
        ),
        xytext=(-100, 40),
        textcoords="offset points",
        arrowprops={
            "arrowstyle": "->",
            "linewidth": 1.5
        },
        fontsize=10,
        fontweight="bold"
    )


    # --------------------------------------------------------
    # Title
    # --------------------------------------------------------

    side = signal["side"]


    title = (
        f"NDS {side} | {symbol} | 1H\n"
        f"Rally {signal['rally_pct'] * 100:.2f}% | "
        f"Hook Retrace "
        f"{signal['hook1_retrace'] * 100:.1f}% | "
        f"Price Sym "
        f"{signal['price_symmetry']:.2f} | "
        f"Time Sym "
        f"{signal['time_symmetry']:.2f}"
    )


    ax.set_title(
        title,
        fontsize=14,
        fontweight="bold"
    )


    ax.set_ylabel(
        "Price"
    )


    ax.grid(
        True,
        alpha=0.25
    )


    ax.xaxis.set_major_formatter(
        mdates.DateFormatter(
            "%m-%d %H:%M",
            tz=timezone.utc
        )
    )


    fig.autofmt_xdate()


    # --------------------------------------------------------
    # Price range
    # --------------------------------------------------------

    low, high = chart_price_range(
        chart_candles
    )


    extra_low = min(
        low,
        signal["sl"]
    )


    extra_high = max(
        high,
        signal["tp"]
    )


    ax.set_ylim(
        extra_low,
        extra_high
    )


    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    os.makedirs(
        "charts",
        exist_ok=True
    )


    safe_symbol = symbol.replace(
        "/",
        "_"
    )


    filename = (
        f"charts/"
        f"NDS_{safe_symbol}_"
        f"{side}_"
        f"{int(signal['signal_ts'])}.png"
    )


    plt.tight_layout()

    plt.savefig(
        filename,
        dpi=150,
        bbox_inches="tight"
    )


    plt.close(fig)


    return filename


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def send_telegram_message(
    message
):

    if not TELEGRAM_TOKEN:
        print("[TELEGRAM] Token missing")
        return False


    if not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Chat ID missing")
        return False


    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )


    try:

        response = session.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
            },
            timeout=20
        )


        if response.ok:
            return True


        print(
            "[TELEGRAM ERROR]",
            response.text
        )

        return False


    except Exception as e:

        print(
            "[TELEGRAM ERROR]",
            e
        )

        return False


# ============================================================
# TELEGRAM PHOTO
# ============================================================

def send_telegram_photo(
    photo_file,
    caption
):

    if not TELEGRAM_TOKEN:
        return False


    if not TELEGRAM_CHAT_ID:
        return False


    if not photo_file:
        return False


    if not os.path.exists(
        photo_file
    ):
        return False


    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendPhoto"
    )


    try:

        with open(
            photo_file,
            "rb"
        ) as f:

            response = session.post(
                url,
                data={
                    "chat_id":
                        TELEGRAM_CHAT_ID,
                    "caption":
                        caption,
                },
                files={
                    "photo": f
                },
                timeout=30
            )


        if response.ok:
            return True


        print(
            "[TELEGRAM PHOTO ERROR]",
            response.text
        )


        return False


    except Exception as e:

        print(
            "[TELEGRAM PHOTO ERROR]",
            e
        )

        return False


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal_message(
    symbol,
    signal
):

    side = signal["side"]

    emoji = (
        "🟢"
        if side == "LONG"
        else "🔴"
    )


    return (
        f"{emoji} NDS {side}\n\n"

        f"#{symbol}\n\n"

        f"Entry: "
        f"{signal['entry']:.8g}\n"

        f"SL: "
        f"{signal['sl']:.8g}\n"

        f"TP: "
        f"{signal['tp']:.8g}\n"

        f"RR: "
        f"{signal['rr']:.2f}\n\n"

        f"Rally: "
        f"{signal['rally_pct'] * 100:.2f}%\n"

        f"Hook retrace: "
        f"{signal['hook1_retrace'] * 100:.1f}%\n"

        f"Price symmetry: "
        f"{signal['price_symmetry']:.2f}\n"

        f"Time symmetry: "
        f"{signal['time_symmetry']:.2f}\n\n"

        f"P1: "
        f"{signal['p1']['price']:.8g}\n"

        f"P2: "
        f"{signal['p2']['price']:.8g}\n"

        f"P3: "
        f"{signal['p3']['price']:.8g}\n"

        f"P4: "
        f"{signal['p4']['price']:.8g}\n\n"

        f"Signal: "
        f"{fmt_time(signal['signal_ts'])}\n\n"

        f"⚠️ PAPER ONLY"
    )


# ============================================================
# REPORT
# ============================================================

def print_report():

    conn = db()

    cur = conn.cursor()


    cur.execute("""
        SELECT
            COUNT(*)
        FROM signals
        WHERE status = 'OPEN'
    """)

    open_count = cur.fetchone()[0]


    cur.execute("""
        SELECT
            COUNT(*)
        FROM signals
        WHERE status = 'CLOSED'
    """)

    closed_count = cur.fetchone()[0]


    cur.execute("""
        SELECT
            COUNT(*)
        FROM signals
        WHERE status = 'CLOSED'
          AND pnl_pct > 0
    """)

    wins = cur.fetchone()[0]


    cur.execute("""
        SELECT
            COALESCE(
                SUM(pnl_pct),
                0
            )
        FROM signals
        WHERE status = 'CLOSED'
    """)

    total_pnl = cur.fetchone()[0]


    conn.close()


    win_rate = (
        wins / closed_count * 100
        if closed_count > 0
        else 0
    )


    print()
    print("=" * 60)
    print("NDS LIVE REPORT")
    print("=" * 60)

    print(
        f"Version: {VERSION}"
    )

    print(
        f"Assets scanned: "
        f"{len(SYMBOLS)}"
    )

    print(
        f"Open trades: "
        f"{open_count}"
    )

    print(
        f"Closed trades: "
        f"{closed_count}"
    )

    print(
        f"Wins: "
        f"{wins}"
    )

    print(
        f"Win rate: "
        f"{win_rate:.2f}%"
    )

    print(
        f"Total P/L: "
        f"{total_pnl:+.2f}%"
    )

    print("=" * 60)


# ============================================================
# SCAN ONE SYMBOL
# ============================================================

def scan_symbol(symbol):

    candles = fetch_candles(
        symbol,
        TIMEFRAME,
        CANDLE_LIMIT
    )


    if len(candles) < 100:

        print(
            f"[SKIP] {symbol}: "
            f"not enough candles"
        )

        return None


    setup = find_nds_setup(
        candles
    )


    if setup is None:

        return None


    signal = confirm_setup(
        setup,
        candles
    )


    if signal is None:

        return None


    # --------------------------------------------------------
    # Signal freshness
    # --------------------------------------------------------

    age_hours = (
        time.time()
        - signal["signal_ts"]
    ) / 3600


    if age_hours > MAX_SIGNAL_AGE_HOURS:

        print(
            f"[OLD] {symbol} "
            f"{signal['side']} "
            f"age={age_hours:.2f}h"
        )

        return None


    # --------------------------------------------------------
    # Duplicate
    # --------------------------------------------------------

    if signal_exists(
        symbol,
        signal["side"],
        signal["signal_ts"]
    ):

        return None


    # --------------------------------------------------------
    # One open trade per symbol
    # --------------------------------------------------------

    if has_open_trade(symbol):

        print(
            f"[OPEN EXISTS] {symbol}"
        )

        return None


    # --------------------------------------------------------
    # Max open trades
    # --------------------------------------------------------

    if (
        count_open_trades()
        >= MAX_OPEN_TRADES
    ):

        print(
            "[MAX OPEN TRADES]"
        )

        return None


    # --------------------------------------------------------
    # Trade levels
    # --------------------------------------------------------

    signal = calculate_trade_levels(
        signal,
        candles
    )


    if signal is None:

        print(
            f"[INVALID LEVELS] "
            f"{symbol}"
        )

        return None


    # --------------------------------------------------------
    # Chart
    # --------------------------------------------------------

    chart_file = create_nds_chart(
        symbol,
        signal,
        candles
    )


    # --------------------------------------------------------
    # DB
    # --------------------------------------------------------

    signal_id = insert_signal(
        symbol,
        signal,
        chart_file
    )


    print()
    print(
        "🔥 NEW NDS SIGNAL"
    )

    print(
        f"Symbol: {symbol}"
    )

    print(
        f"Side: {signal['side']}"
    )

    print(
        f"Entry: {signal['entry']}"
    )

    print(
        f"SL: {signal['sl']}"
    )

    print(
        f"TP: {signal['tp']}"
    )

    print(
        f"RR: {signal['rr']:.2f}"
    )

    print(
        f"Rally: "
        f"{signal['rally_pct'] * 100:.2f}%"
    )

    print(
        f"Hook retrace: "
        f"{signal['hook1_retrace'] * 100:.1f}%"
    )

    print(
        f"Price symmetry: "
        f"{signal['price_symmetry']:.2f}"
    )

    print(
        f"Time symmetry: "
        f"{signal['time_symmetry']:.2f}"
    )

    print(
        f"Chart: "
        f"{chart_file}"
    )


    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    message = format_signal_message(
        symbol,
        signal
    )


    send_telegram_message(
        message
    )


    if chart_file:

        send_telegram_photo(
            chart_file,
            f"NDS {signal['side']} | #{symbol}"
        )


    return signal_id


# ============================================================
# MAIN SCAN
# ============================================================

def run_scan():

    print()
    print("=" * 60)
    print(
        f"NDS SCAN "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    print("=" * 60)


    new_long = 0
    new_short = 0


    # --------------------------------------------------------
    # First monitor existing trades.
    # --------------------------------------------------------

    monitor_open_trades()


    # --------------------------------------------------------
    # Scan assets.
    # --------------------------------------------------------

    for symbol in SYMBOLS:

        try:

            signal_id = scan_symbol(
                symbol
            )


            if signal_id:

                # Determine side from DB.
                conn = db()
                cur = conn.cursor()

                cur.execute("""
                    SELECT side
                    FROM signals
                    WHERE id = ?
                """, (
                    signal_id,
                ))

                row = cur.fetchone()

                conn.close()


                if row:

                    if row["side"] == "LONG":
                        new_long += 1

                    else:
                        new_short += 1


        except Exception as e:

            print(
                f"[ERROR] {symbol}: {e}"
            )

            traceback.print_exc()


    print()
    print(
        f"NEW LONG: {new_long}"
    )

    print(
        f"NEW SHORT: {new_short}"
    )


    print_report()


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    print()
    print("=" * 60)
    print(
        f"KRAKEN NDS LIVE SCANNER "
        f"V{VERSION}"
    )
    print("=" * 60)

    print(
        "MODE: PAPER ONLY"
    )

    print(
        f"TIMEFRAME: {TIMEFRAME}"
    )

    print(
        f"ASSETS: {len(SYMBOLS)}"
    )

    print(
        f"DB: {DB_FILE}"
    )

    print(
        f"SCAN: every "
        f"{SCAN_INTERVAL_SECONDS}s"
    )

    print("=" * 60)


    if REAL_TRADING:

        raise RuntimeError(
            "REAL_TRADING must remain False."
        )


    init_db()


    # --------------------------------------------------------
    # First scan immediately.
    # --------------------------------------------------------

    run_scan()


    # --------------------------------------------------------
    # Continuous live scanner.
    # --------------------------------------------------------

    while True:

        try:

            print()
            print(
                f"[SLEEP] "
                f"{SCAN_INTERVAL_SECONDS}s"
            )


            time.sleep(
                SCAN_INTERVAL_SECONDS
            )


            run_scan()


        except KeyboardInterrupt:

            print(
                "\nStopped."
            )

            break


        except Exception as e:

            print(
                f"[MAIN ERROR] {e}"
            )

            traceback.print_exc()

            time.sleep(30)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
