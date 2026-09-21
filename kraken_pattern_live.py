# ============================================================
# KRAKEN FUTURES TRENDLINE LIVE SIGNAL SCANNER
# VERSION 7.1.0
# ============================================================
#
# PAPER ONLY
# REAL_TRADING = False
#
# STRATEGY
#
# 1H:
#   Valid pivots
#        ↓
#   Valid trendline
#        ↓
#   1H trendline breakout
#        ↓
#
# 5M:
#   Valid pivots
#        ↓
#   Valid trendline
#        ↓
#   5M trendline breakout
#        ↓
#   RVOL confirmation
#        ↓
#   ENTRY
#
# LEVELS
#
#   TP = confirmed 5M swing BEFORE entry
#   SL = confirmed opposite 5M swing BEFORE entry
#   RR > 1.0
#
# IMPORTANT
# - Closed candles only
# - No lookahead
# - Existing DB is preserved
# - Runtime statistics reset on every scan
# - Maximum 1 new signal per scan
# - PAPER ONLY
# ============================================================

import os
import sys
import sqlite3
import traceback
from datetime import datetime, timezone, timedelta

import requests

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "7.1.0"

REAL_TRADING = False
PAPER_TRADING = True

DB_FILE = "kraken_pattern_live_v52.db"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

KRAKEN_CHART_URL = (
    "https://futures.kraken.com/api/charts/v1/trade"
)

KRAKEN_TICKER_URL = (
    "https://futures.kraken.com/derivatives/api/v3/tickers"
)

TIMEOUT = 20

# ------------------------------------------------------------
# Universe
# ------------------------------------------------------------

ASSETS = [
    "XBT",
    "ETH",
    "SOL",
    "XRP",
    "LTC",
    "DOGE",
    "ADA",
    "LINK",
    "AVAX",
    "DOT",
    "BNB",
    "TRX",
    "UNI",
    "AAVE",
    "SUI",
    "NEAR",
    "ATOM",
    "FIL",
    "ARB",
    "OP",
    "BCH",
    "ETC",
    "XMR",
    "XLM",
    "ALGO",
    "ICP",
    "INJ",
    "TIA",
    "SEI",
    "RUNE",
    "CRV",
    "HBAR",
    "HYPE",
    "ENA",
    "FET",
    "KAS",
    "STX",
    "JUP",
    "PEPE",
    "WIF",
]

RESOLUTION_1H = 60
RESOLUTION_5M = 5

CANDLE_LIMIT_1H = 250
CANDLE_LIMIT_5M = 500

# ------------------------------------------------------------
# Pivot / Trendline
# ------------------------------------------------------------

PIVOT_LEFT = 3
PIVOT_RIGHT = 3

MIN_PIVOT_DISTANCE = 4

TRENDLINE_TOLERANCE_PCT = 0.20

MIN_TRENDLINE_BARS = 8

BREAKOUT_BUFFER_PCT = 0.05

# ------------------------------------------------------------
# Volume
# ------------------------------------------------------------

RVOL_LOOKBACK = 20
RVOL_MIN = 1.20

# ------------------------------------------------------------
# Risk
# ------------------------------------------------------------

MIN_RR = 1.01

SL_BUFFER_PCT = 0.05 / 100.0

MAX_HOLD_HOURS = 48

MAX_ENTRY_AGE_CANDLES = 1

# ------------------------------------------------------------
# Signal control
# ------------------------------------------------------------

MAX_SIGNALS_PER_SCAN = 1

PERIODIC_REPORT_SECONDS = 900

# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "KrakenTrendlineScanner/7.1.0"
    }
)


# ============================================================
# RUNTIME STATS
# ============================================================

def new_stats():
    return {
        "assets_scanned": 0,

        "trendlines_1h": 0,
        "breakouts_1h": 0,

        "trendlines_5m": 0,
        "breakouts_5m": 0,

        "volume_accepted": 0,

        "valid_levels": 0,

        "new_signals": 0,
        "closed_trades": 0,

        "level_rejects": 0,

        "errors": 0,
    }


stats = new_stats()


def reset_stats():
    global stats
    stats = new_stats()


# ============================================================
# TIME
# ============================================================

TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))


def utc_now():
    return datetime.now(timezone.utc)


def tehran_time(dt=None):
    if dt is None:
        dt = utc_now()

    return dt.astimezone(TEHRAN_TZ)


def format_tehran(dt):
    return dt.astimezone(TEHRAN_TZ).strftime(
        "%Y-%m-%d %H:%M"
    )


def format_duration(seconds):
    if seconds is None:
        return "-"

    try:
        seconds = int(max(0, seconds))
    except Exception:
        return "-"

    days = seconds // 86400
    seconds %= 86400

    hours = seconds // 3600
    seconds %= 3600

    minutes = seconds // 60

    if days:
        return f"{days}d {hours}h"

    if hours:
        return f"{hours}h {minutes}m"

    return f"{minutes}m"


# ============================================================
# CONTRACT
# ============================================================

def contract_for(asset):
    return f"PF_{asset}USD"


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }

    try:
        r = SESSION.post(
            url,
            json=payload,
            timeout=TIMEOUT,
        )

        return r.ok

    except Exception:
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
            }

            r = SESSION.post(
                url,
                data=data,
                files=files,
                timeout=TIMEOUT,
            )

        return r.ok

    except Exception:
        return False


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn, table):
    rows = conn.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    return {
        row["name"]
        for row in rows
    }


def ensure_column(conn, table, column, definition):

    columns = table_columns(conn, table)

    if column not in columns:
        conn.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN {column} {definition}"
        )


def init_db():

    conn = db_connect()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            asset TEXT NOT NULL,
            direction TEXT NOT NULL,

            signal_time TEXT NOT NULL,

            entry REAL NOT NULL,
            tp REAL NOT NULL,
            sl REAL NOT NULL,

            rr REAL,

            status TEXT DEFAULT 'OPEN',

            exit_time TEXT,
            exit_price REAL,
            exit_reason TEXT,

            pnl_pct REAL,

            duration_seconds INTEGER,

            trendline_1h TEXT,
            trendline_5m TEXT,

            breakout_1h_time TEXT,
            breakout_5m_time TEXT,

            tp_source TEXT,
            sl_source TEXT,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # --------------------------------------------------------
    # Migration for existing DB
    # --------------------------------------------------------

    ensure_column(
        conn,
        "signals",
        "exit_reason",
        "TEXT",
    )

    ensure_column(
        conn,
        "signals",
        "duration_seconds",
        "INTEGER",
    )

    ensure_column(
        conn,
        "signals",
        "trendline_1h",
        "TEXT",
    )

    ensure_column(
        conn,
        "signals",
        "trendline_5m",
        "TEXT",
    )

    ensure_column(
        conn,
        "signals",
        "breakout_1h_time",
        "TEXT",
    )

    ensure_column(
        conn,
        "signals",
        "breakout_5m_time",
        "TEXT",
    )

    ensure_column(
        conn,
        "signals",
        "tp_source",
        "TEXT",
    )

    ensure_column(
        conn,
        "signals",
        "sl_source",
        "TEXT",
    )

    conn.commit()
    conn.close()


# ============================================================
# MARKET DATA
# ============================================================

def fetch_candles(contract, resolution, limit):

    url = (
        f"{KRAKEN_CHART_URL}/"
        f"{contract}/"
        f"{resolution}"
    )

    try:

        r = SESSION.get(
            url,
            timeout=TIMEOUT,
        )

        if not r.ok:
            return []

        data = r.json()

    except Exception:
        return []

    raw = []

    if isinstance(data, dict):

        if isinstance(data.get("candles"), list):
            raw = data["candles"]

        elif isinstance(data.get("data"), list):
            raw = data["data"]

        elif isinstance(data.get("result"), list):
            raw = data["result"]

    elif isinstance(data, list):
        raw = data

    candles = []

    for x in raw:

        try:

            if isinstance(x, dict):

                ts = (
                    x.get("time")
                    or x.get("timestamp")
                    or x.get("ts")
                )

                o = x.get("open")
                h = x.get("high")
                l = x.get("low")
                c = x.get("close")
                v = (
                    x.get("volume")
                    or x.get("vol")
                    or 0
                )

            else:

                if len(x) < 6:
                    continue

                ts = x[0]
                o = x[1]
                h = x[2]
                l = x[3]
                c = x[4]
                v = x[5]

            ts = float(ts)

            if ts > 10_000_000_000:
                ts /= 1000.0

            candles.append(
                {
                    "time": datetime.fromtimestamp(
                        ts,
                        tz=timezone.utc,
                    ),

                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": float(v),
                }
            )

        except Exception:
            continue

    candles.sort(
        key=lambda x: x["time"]
    )

    # --------------------------------------------------------
    # Remove currently forming candle
    # --------------------------------------------------------

    now = utc_now()

    closed = []

    seconds = resolution * 60

    for c in candles:

        candle_end = (
            c["time"] +
            timedelta(seconds=seconds)
        )

        if candle_end <= now:
            closed.append(c)

    if len(closed) > limit:
        closed = closed[-limit:]

    return closed


# ============================================================
# TICKERS
# ============================================================

def fetch_tickers():

    try:

        r = SESSION.get(
            KRAKEN_TICKER_URL,
            timeout=TIMEOUT,
        )

        if not r.ok:
            return {}

        data = r.json()

    except Exception:
        return {}

    rows = []

    if isinstance(data, dict):

        rows = (
            data.get("tickers")
            or data.get("data")
            or data.get("result")
            or []
        )

    result = {}

    for row in rows:

        if not isinstance(row, dict):
            continue

        symbol = (
            row.get("symbol")
            or row.get("pair")
            or row.get("contract")
        )

        if not symbol:
            continue

        price = (
            row.get("last")
            or row.get("lastPrice")
            or row.get("markPrice")
            or row.get("price")
        )

        try:
            result[symbol] = float(price)
        except Exception:
            pass

    return result


def ticker_price(tickers, contract):

    if contract in tickers:
        return tickers[contract]

    return None


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(candles):

    highs = []
    lows = []

    n = len(candles)

    start = PIVOT_LEFT
    end = n - PIVOT_RIGHT

    for i in range(start, end):

        current = candles[i]

        is_high = True
        is_low = True

        for j in range(
            i - PIVOT_LEFT,
            i + PIVOT_RIGHT + 1,
        ):

            if j == i:
                continue

            if candles[j]["high"] >= current["high"]:
                is_high = False

            if candles[j]["low"] <= current["low"]:
                is_low = False

        if is_high:

            highs.append(
                {
                    "index": i,
                    "time": current["time"],
                    "price": current["high"],
                }
            )

        if is_low:

            lows.append(
                {
                    "index": i,
                    "time": current["time"],
                    "price": current["low"],
                }
            )

    return highs, lows


# ============================================================
# TRENDLINE
# ============================================================

def _line_price(p1, p2, index):

    x1 = p1["index"]
    x2 = p2["index"]

    y1 = p1["price"]
    y2 = p2["price"]

    if x2 == x1:
        return None

    slope = (
        (y2 - y1)
        /
        (x2 - x1)
    )

    return y1 + slope * (index - x1)


def validate_trendline(
    candles,
    p1,
    p2,
    direction,
):

    if p2["index"] <= p1["index"]:
        return False

    if (
        p2["index"] - p1["index"]
        <
        MIN_TRENDLINE_BARS
    ):
        return False

    line_start = p1["index"]
    line_end = len(candles) - 1

    if direction == "HIGH":

        for i in range(
            line_start + 1,
            line_end + 1,
        ):

            line = _line_price(
                p1,
                p2,
                i,
            )

            if line is None:
                return False

            # A resistance line must not be
            # clearly exceeded before breakout.
            if candles[i]["high"] > (
                line *
                (1 + TRENDLINE_TOLERANCE_PCT / 100)
            ):

                if i < line_end:
                    return False

    else:

        for i in range(
            line_start + 1,
            line_end + 1,
        ):

            line = _line_price(
                p1,
                p2,
                i,
            )

            if line is None:
                return False

            # A support line must not be
            # clearly broken before breakout.
            if candles[i]["low"] < (
                line *
                (1 - TRENDLINE_TOLERANCE_PCT / 100)
            ):

                if i < line_end:
                    return False

    return True


def find_valid_trendline(
    candles,
    pivots,
    direction,
):

    if len(pivots) < 2:
        return None

    candidates = []

    # --------------------------------------------------------
    # Try recent pivot pairs first
    # --------------------------------------------------------

    for i in range(
        len(pivots) - 1,
        0,
        -1,
    ):

        p2 = pivots[i]

        for j in range(
            i - 1,
            -1,
            -1,
        ):

            p1 = pivots[j]

            if (
                p2["index"] -
                p1["index"]
                <
                MIN_TRENDLINE_BARS
            ):
                continue

            if validate_trendline(
                candles,
                p1,
                p2,
                direction,
            ):

                candidates.append(
                    (
                        p1,
                        p2,
                    )
                )

                break

        if candidates:
            break

    if not candidates:
        return None

    p1, p2 = candidates[0]

    return {
        "direction": direction,
        "p1": p1,
        "p2": p2,
    }


# ============================================================
# TRENDLINE BREAKOUT
# ============================================================

def find_trendline_breakout(
    candles,
    trendline,
):

    if trendline is None:
        return None

    p1 = trendline["p1"]
    p2 = trendline["p2"]

    direction = trendline["direction"]

    start = max(
        p2["index"] + 1,
        1,
    )

    for i in range(
        start,
        len(candles),
    ):

        candle = candles[i]

        line = _line_price(
            p1,
            p2,
            i,
        )

        if line is None:
            continue

        if direction == "HIGH":

            threshold = (
                line *
                (1 + BREAKOUT_BUFFER_PCT / 100)
            )

            if candle["close"] > threshold:

                return {
                    "index": i,
                    "time": candle["time"],
                    "price": candle["close"],
                    "direction": "LONG",
                    "line_price": line,
                }

        else:

            threshold = (
                line *
                (1 - BREAKOUT_BUFFER_PCT / 100)
            )

            if candle["close"] < threshold:

                return {
                    "index": i,
                    "time": candle["time"],
                    "price": candle["close"],
                    "direction": "SHORT",
                    "line_price": line,
                }

    return None


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(candles, index):

    if index < RVOL_LOOKBACK:
        return None

    volumes = [
        candles[i]["volume"]
        for i in range(
            index - RVOL_LOOKBACK,
            index,
        )
    ]

    if not volumes:
        return None

    avg_volume = (
        sum(volumes)
        /
        len(volumes)
    )

    if avg_volume <= 0:
        return None

    return (
        candles[index]["volume"]
        /
        avg_volume
    )


def volume_confirmed(
    candles,
    index,
):

    rvol = calculate_rvol(
        candles,
        index,
    )

    if rvol is None:
        return False, None

    return (
        rvol >= RVOL_MIN,
        rvol,
    )


# ============================================================
# LEVEL SELECTION
# ============================================================

def choose_levels(
    candles_5m,
    highs_5m,
    lows_5m,
    break_index,
    direction,
    entry,
):

    # --------------------------------------------------------
    # VERY IMPORTANT:
    #
    # Only pivots confirmed BEFORE the breakout candle
    # are allowed.
    #
    # No lookahead.
    # --------------------------------------------------------

    confirmed_highs = [
        p
        for p in highs_5m
        if p["index"] < break_index
    ]

    confirmed_lows = [
        p
        for p in lows_5m
        if p["index"] < break_index
    ]

    if direction == "LONG":

        # ----------------------------------------------------
        # SL:
        # Latest confirmed 5M LOW before entry
        # ----------------------------------------------------

        if not confirmed_lows:
            return None

        sl_pivot = confirmed_lows[-1]

        sl = (
            sl_pivot["price"]
            *
            (1.0 - SL_BUFFER_PCT)
        )

        # ----------------------------------------------------
        # TP:
        # Nearest confirmed 5M HIGH above entry
        #
        # STRICTLY 5M
        # ----------------------------------------------------

        tp_candidates = [
            p
            for p in confirmed_highs
            if p["price"] > entry
        ]

        if not tp_candidates:
            return None

        tp_candidates.sort(
            key=lambda x: x["price"]
        )

        tp_pivot = tp_candidates[0]

        tp = tp_pivot["price"]

        risk = entry - sl
        reward = tp - entry

        if risk <= 0:
            return None

        if reward <= 0:
            return None

        rr = reward / risk

        if rr <= 1.0:
            return None

        if rr < MIN_RR:
            return None

        return {
            "tp": tp,
            "sl": sl,
            "rr": rr,

            "tp_source": "5M",
            "sl_source": "5M",

            "tp_pivot": tp_pivot,
            "sl_pivot": sl_pivot,
        }

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        # ----------------------------------------------------
        # SL:
        # Latest confirmed 5M HIGH before entry
        # ----------------------------------------------------

        if not confirmed_highs:
            return None

        sl_pivot = confirmed_highs[-1]

        sl = (
            sl_pivot["price"]
            *
            (1.0 + SL_BUFFER_PCT)
        )

        # ----------------------------------------------------
        # TP:
        # Nearest confirmed 5M LOW below entry
        # ----------------------------------------------------

        tp_candidates = [
            p
            for p in confirmed_lows
            if p["price"] < entry
        ]

        if not tp_candidates:
            return None

        tp_candidates.sort(
            key=lambda x: x["price"],
            reverse=True,
        )

        tp_pivot = tp_candidates[0]

        tp = tp_pivot["price"]

        risk = sl - entry
        reward = entry - tp

        if risk <= 0:
            return None

        if reward <= 0:
            return None

        rr = reward / risk

        if rr <= 1.0:
            return None

        if rr < MIN_RR:
            return None

        return {
            "tp": tp,
            "sl": sl,
            "rr": rr,

            "tp_source": "5M",
            "sl_source": "5M",

            "tp_pivot": tp_pivot,
            "sl_pivot": sl_pivot,
        }

    return None


# ============================================================
# DATABASE INSERT
# ============================================================

def insert_signal(
    asset,
    direction,
    signal_time,
    entry,
    tp,
    sl,
    rr,
    trendline_1h,
    trendline_5m,
    breakout_1h_time,
    breakout_5m_time,
):

    conn = db_connect()

    try:

        cur = conn.execute(
            """
            INSERT INTO signals (
                asset,
                direction,
                signal_time,
                entry,
                tp,
                sl,
                rr,
                status,
                trendline_1h,
                trendline_5m,
                breakout_1h_time,
                breakout_5m_time,
                tp_source,
                sl_source
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                'OPEN',
                ?, ?, ?, ?,
                '5M',
                '5M'
            )
            """,
            (
                asset,
                direction,
                signal_time,
                entry,
                tp,
                sl,
                rr,
                str(trendline_1h),
                str(trendline_5m),
                breakout_1h_time,
                breakout_5m_time,
            ),
        )

        inserted = (
            cur.rowcount == 1
        )

        conn.commit()

        return inserted

    except sqlite3.IntegrityError:

        conn.rollback()

        return False

    except Exception:

        conn.rollback()

        raise

    finally:

        conn.close()


# ============================================================
# OPEN TRADE RULE
# ============================================================

def has_open_same_direction(
    asset,
    direction,
):

    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
        FROM signals
        WHERE asset = ?
          AND direction = ?
          AND status = 'OPEN'
        LIMIT 1
        """,
        (
            asset,
            direction,
        ),
    ).fetchone()

    conn.close()

    return row is not None


# ============================================================
# PNL
# ============================================================

def calculate_pnl(
    direction,
    entry,
    exit_price,
):

    if direction == "LONG":

        return (
            (exit_price - entry)
            /
            entry
        ) * 100.0

    return (
        (entry - exit_price)
        /
        entry
    ) * 100.0


# ============================================================
# RECONCILE OPEN TRADES
# ============================================================

def reconcile_open_trades(
    tickers,
):

    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY id ASC
        """
    ).fetchall()

    closed = []

    now = utc_now()

    for row in rows:

        asset = row["asset"]
        direction = row["direction"]

        contract = contract_for(asset)

        current = ticker_price(
            tickers,
            contract,
        )

        if current is None:
            continue

        entry = float(row["entry"])
        tp = float(row["tp"])
        sl = float(row["sl"])

        signal_time = datetime.fromisoformat(
            row["signal_time"]
        )

        if signal_time.tzinfo is None:
            signal_time = signal_time.replace(
                tzinfo=timezone.utc
            )

        hold_seconds = (
            now - signal_time
        ).total_seconds()

        exit_price = None
        exit_reason = None

        # ----------------------------------------------------
        # TP / SL
        #
        # If both occur on same observed price,
        # SL gets priority.
        # ----------------------------------------------------

        if direction == "LONG":

            if current <= sl:

                exit_price = sl
                exit_reason = "SL"

            elif current >= tp:

                exit_price = tp
                exit_reason = "TP"

        else:

            if current >= sl:

                exit_price = sl
                exit_reason = "SL"

            elif current <= tp:

                exit_price = tp
                exit_reason = "TP"

        # ----------------------------------------------------
        # Time exit
        # ----------------------------------------------------

        if (
            exit_price is None
            and
            hold_seconds >= MAX_HOLD_HOURS * 3600
        ):

            exit_price = current
            exit_reason = "TIME"

        if exit_price is None:
            continue

        pnl = calculate_pnl(
            direction,
            entry,
            exit_price,
        )

        exit_time = now.isoformat()

        conn.execute(
            """
            UPDATE signals
            SET
                status = 'CLOSED',
                exit_time = ?,
                exit_price = ?,
                exit_reason = ?,
                pnl_pct = ?,
                duration_seconds = ?
            WHERE id = ?
            """,
            (
                exit_time,
                exit_price,
                exit_reason,
                pnl,
                int(hold_seconds),
                row["id"],
            ),
        )

        closed.append(
            {
                "id": row["id"],
                "asset": asset,
                "direction": direction,
                "entry": entry,
                "exit": exit_price,
                "tp": tp,
                "sl": sl,
                "pnl": pnl,
                "reason": exit_reason,
                "duration": hold_seconds,
                "signal_time": signal_time,
                "exit_time": now,
            }
        )

    conn.commit()
    conn.close()

    stats["closed_trades"] += len(closed)

    return closed


# ============================================================
# TELEGRAM CLOSED TRADE
# ============================================================

def send_close_message(trade):

    if trade["direction"] == "LONG":
        emoji = "🟢"
    else:
        emoji = "🔴"

    pnl = trade["pnl"]

    pnl_text = (
        f"{pnl:+.2f}%"
    )

    text = (
        f"{emoji} <b>CLOSE "
        f"{trade['asset']} "
        f"{trade['direction']}</b>\n\n"
        f"Exit: {trade['exit']:.8g}\n"
        f"Result: {pnl_text}\n"
        f"Reason: {trade['reason']}\n"
        f"Duration: "
        f"{format_duration(trade['duration'])}\n"
        f"Time: "
        f"{format_tehran(trade['exit_time'])}"
    )

    telegram_send(text)


# ============================================================
# OPEN TRADES
# ============================================================

def send_open_trades():

    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY signal_time ASC
        """
    ).fetchall()

    conn.close()

    if not rows:
        return

    now = utc_now()

    lines = [
        "📂 <b>OPEN TRADES</b>",
        "",
    ]

    tickers = fetch_tickers()

    for row in rows:

        asset = row["asset"]
        direction = row["direction"]

        contract = contract_for(asset)

        current = ticker_price(
            tickers,
            contract,
        )

        if current is None:
            current = row["entry"]

        entry = float(row["entry"])

        pnl = calculate_pnl(
            direction,
            entry,
            current,
        )

        try:
            signal_time = datetime.fromisoformat(
                row["signal_time"]
            )

            if signal_time.tzinfo is None:
                signal_time = signal_time.replace(
                    tzinfo=timezone.utc
                )

            duration = (
                now - signal_time
            ).total_seconds()

        except Exception:
            duration = None

        if direction == "LONG":
            emoji = "🟢"
        else:
            emoji = "🔴"

        lines.append(
            f"{emoji} {asset} {direction}\n"
            f"Entry: {entry:.8g}\n"
            f"Current: {current:.8g} "
            f"({pnl:+.2f}%)\n"
            f"TP: {float(row['tp']):.8g}\n"
            f"SL: {float(row['sl']):.8g}\n"
            f"RR: {float(row['rr']):.2f}\n"
            f"Duration: {format_duration(duration)}\n"
        )

    telegram_send(
        "\n".join(lines)
    )


# ============================================================
# PERFORMANCE
# ============================================================

def send_performance():

    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM signals
        WHERE status = 'CLOSED'
        ORDER BY id ASC
        """
    ).fetchall()

    conn.close()

    if not rows:
        return

    total = len(rows)

    wins = sum(
        1
        for r in rows
        if float(r["pnl_pct"] or 0) > 0
    )

    losses = sum(
        1
        for r in rows
        if float(r["pnl_pct"] or 0) <= 0
    )

    pnl = sum(
        float(r["pnl_pct"] or 0)
        for r in rows
    )

    win_rate = (
        wins / total * 100
        if total
        else 0
    )

    tp_count = sum(
        1
        for r in rows
        if r["exit_reason"] == "TP"
    )

    sl_count = sum(
        1
        for r in rows
        if r["exit_reason"] == "SL"
    )

    time_count = sum(
        1
        for r in rows
        if r["exit_reason"] == "TIME"
    )

    text = (
        "📈 <b>PERFORMANCE</b>\n\n"
        f"Closed: {total}\n"
        f"Win Rate: {win_rate:.2f}%\n"
        f"TP: {tp_count}\n"
        f"SL: {sl_count}\n"
        f"TIME: {time_count}\n"
        f"Net PNL: {pnl:+.2f}%"
    )

    telegram_send(text)


# ============================================================
# CHART
# ============================================================

def make_signal_chart(
    asset,
    candles,
    trendline,
    breakout,
    entry,
    tp,
    sl,
):

    if not candles:
        return None

    path = (
        f"trendline_{asset}_"
        f"{int(datetime.now().timestamp())}.png"
    )

    recent = candles[-180:]

    x = list(
        range(len(recent))
    )

    fig, ax = plt.subplots(
        figsize=(14, 7)
    )

    # --------------------------------------------------------
    # Candles
    # --------------------------------------------------------

    for i, candle in enumerate(recent):

        o = candle["open"]
        h = candle["high"]
        l = candle["low"]
        c = candle["close"]

        if c >= o:
            face = "green"
        else:
            face = "red"

        ax.plot(
            [i, i],
            [l, h],
            color="black",
            linewidth=1,
        )

        width = 0.6

        ax.bar(
            i,
            c - o,
            bottom=o,
            width=width,
            color=face,
            alpha=0.75,
        )

    # --------------------------------------------------------
    # Trendline
    # --------------------------------------------------------

    if trendline:

        p1 = trendline["p1"]
        p2 = trendline["p2"]

        first_time = recent[0]["time"]

        def idx_for_time(t):
            best = None
            best_dist = None

            for i, c in enumerate(recent):

                dist = abs(
                    (
                        c["time"] - t
                    ).total_seconds()
                )

                if best_dist is None or dist < best_dist:
                    best_dist = dist
                    best = i

            return best

        i1 = idx_for_time(
            p1["time"]
        )

        i2 = idx_for_time(
            p2["time"]
        )

        if i1 is not None and i2 is not None:

            y1 = p1["price"]
            y2 = p2["price"]

            ax.plot(
                [i1, i2],
                [y1, y2],
                linewidth=2,
                linestyle="--",
            )

            # Extend trendline
            last_i = len(recent) - 1

            if i2 != i1:

                slope = (
                    (y2 - y1)
                    /
                    (i2 - i1)
                )

                y_last = (
                    y1
                    +
                    slope
                    *
                    (last_i - i1)
                )

                ax.plot(
                    [i2, last_i],
                    [y2, y_last],
                    linewidth=2,
                    linestyle="--",
                )

    # --------------------------------------------------------
    # Breakout
    # --------------------------------------------------------

    if breakout:

        bt = breakout["time"]

        bi = None

        for i, c in enumerate(recent):

            if c["time"] == bt:
                bi = i
                break

        if bi is not None:

            ax.scatter(
                bi,
                breakout["price"],
                s=80,
                marker="^"
                if breakout["direction"] == "LONG"
                else "v",
            )

            ax.annotate(
                "BREAKOUT",
                (
                    bi,
                    breakout["price"],
                ),
                xytext=(8, 10),
                textcoords="offset points",
            )

    # --------------------------------------------------------
    # Pivot P1 / P2
    # --------------------------------------------------------

    if trendline:

        for label, pivot in [
            ("P1", trendline["p1"]),
            ("P2", trendline["p2"]),
        ]:

            pt = pivot["time"]

            pi = None

            for i, c in enumerate(recent):

                if c["time"] == pt:
                    pi = i
                    break

            if pi is not None:

                ax.scatter(
                    pi,
                    pivot["price"],
                    s=60,
                )

                ax.annotate(
                    label,
                    (
                        pi,
                        pivot["price"],
                    ),
                    xytext=(5, 5),
                    textcoords="offset points",
                )

    # --------------------------------------------------------
    # Entry
    # --------------------------------------------------------

    if entry is not None:

        ax.axhline(
            entry,
            linestyle="--",
            linewidth=1.5,
            label=f"Entry {entry:.8g}",
        )

    # --------------------------------------------------------
    # TP
    # --------------------------------------------------------

    if tp is not None:

        ax.axhline(
            tp,
            linestyle="--",
            linewidth=1.5,
            label=f"TP {tp:.8g}",
        )

    # --------------------------------------------------------
    # SL
    # --------------------------------------------------------

    if sl is not None:

        ax.axhline(
            sl,
            linestyle="--",
            linewidth=1.5,
            label=f"SL {sl:.8g}",
        )

    ax.set_title(
        f"{asset} | 5M Trendline Signal"
    )

    ax.set_xlabel(
        "5M Candles"
    )

    ax.set_ylabel(
        "Price"
    )

    ax.grid(
        alpha=0.20
    )

    ax.legend(
        loc="best"
    )

    plt.tight_layout()

    fig.savefig(
        path,
        dpi=140,
    )

    plt.close(fig)

    return path


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def send_signal(
    asset,
    direction,
    entry,
    tp,
    sl,
    rr,
    rvol,
    breakout_1h,
    breakout_5m,
    trendline_5m,
    chart_path,
):

    if direction == "LONG":
        emoji = "🟢"
    else:
        emoji = "🔴"

    risk_pct = abs(
        (entry - sl)
        /
        entry
        *
        100
    )

    reward_pct = abs(
        (tp - entry)
        /
        entry
        *
        100
    )

    text = (
        f"{emoji} <b>{direction} "
        f"{asset}</b>\n\n"
        f"Entry: {entry:.8g}\n"
        f"TP: {tp:.8g}\n"
        f"SL: {sl:.8g}\n"
        f"RR: {rr:.2f}\n"
        f"Risk: {risk_pct:.2f}%\n"
        f"Reward: {reward_pct:.2f}%\n"
        f"RVOL: {rvol:.2f}\n\n"
        f"1H Breakout: "
        f"{format_tehran(breakout_1h['time'])}\n"
        f"5M Breakout: "
        f"{format_tehran(breakout_5m['time'])}\n"
        f"TP Source: 5M\n"
        f"SL Source: 5M\n"
        f"Time: "
        f"{format_tehran(utc_now())}"
    )

    if chart_path:
        telegram_photo(
            chart_path,
            caption=text,
        )

        try:
            os.remove(chart_path)
        except Exception:
            pass

    else:
        telegram_send(text)


# ============================================================
# SCAN ASSET
# ============================================================

def scan_asset(
    asset,
    tickers,
):

    contract = contract_for(asset)

    # --------------------------------------------------------
    # 1H
    # --------------------------------------------------------

    candles_1h = fetch_candles(
        contract,
        RESOLUTION_1H,
        CANDLE_LIMIT_1H,
    )

    if len(candles_1h) < 50:
        return False

    highs_1h, lows_1h = find_pivots(
        candles_1h
    )

    trendline_1h = None

    # Try resistance first
    trendline_1h = find_valid_trendline(
        candles_1h,
        highs_1h,
        "HIGH",
    )

    if trendline_1h is None:

        trendline_1h = find_valid_trendline(
            candles_1h,
            lows_1h,
            "LOW",
        )

    if trendline_1h is None:
        return False

    stats["trendlines_1h"] += 1

    breakout_1h = find_trendline_breakout(
        candles_1h,
        trendline_1h,
    )

    if breakout_1h is None:
        return False

    stats["breakouts_1h"] += 1

    # --------------------------------------------------------
    # 5M
    # --------------------------------------------------------

    candles_5m = fetch_candles(
        contract,
        RESOLUTION_5M,
        CANDLE_LIMIT_5M,
    )

    if len(candles_5m) < 100:
        return False

    # --------------------------------------------------------
    # Only 5M candles AFTER 1H breakout
    # --------------------------------------------------------

    candles_5m_after = [
        c
        for c in candles_5m
        if c["time"] > breakout_1h["time"]
    ]

    if len(candles_5m_after) < 50:
        return False

    highs_5m, lows_5m = find_pivots(
        candles_5m
    )

    trendline_5m = None

    # --------------------------------------------------------
    # Direction from 1H breakout
    # --------------------------------------------------------

    if breakout_1h["direction"] == "LONG":

        trendline_5m = find_valid_trendline(
            candles_5m,
            highs_5m,
            "HIGH",
        )

    else:

        trendline_5m = find_valid_trendline(
            candles_5m,
            lows_5m,
            "LOW",
        )

    if trendline_5m is None:
        return False

    stats["trendlines_5m"] += 1

    breakout_5m = find_trendline_breakout(
        candles_5m,
        trendline_5m,
    )

    if breakout_5m is None:
        return False

    # --------------------------------------------------------
    # 5M breakout must be AFTER 1H breakout
    # --------------------------------------------------------

    if (
        breakout_5m["time"]
        <=
        breakout_1h["time"]
    ):
        return False

    # --------------------------------------------------------
    # Direction agreement
    # --------------------------------------------------------

    if (
        breakout_5m["direction"]
        !=
        breakout_1h["direction"]
    ):
        return False

    stats["breakouts_5m"] += 1

    break_index = breakout_5m["index"]

    # --------------------------------------------------------
    # RVOL
    # --------------------------------------------------------

    volume_ok, rvol = volume_confirmed(
        candles_5m,
        break_index,
    )

    if not volume_ok:
        return False

    stats["volume_accepted"] += 1

    # --------------------------------------------------------
    # Entry
    # --------------------------------------------------------

    entry = breakout_5m["price"]

    direction = breakout_5m["direction"]

    # --------------------------------------------------------
    # Existing open same-direction trade
    # --------------------------------------------------------

    if has_open_same_direction(
        asset,
        direction,
    ):
        return False

    # --------------------------------------------------------
    # TP / SL
    #
    # BOTH STRICTLY FROM 5M
    # --------------------------------------------------------

    levels = choose_levels(
        candles_5m=candles_5m,
        highs_5m=highs_5m,
        lows_5m=lows_5m,
        break_index=break_index,
        direction=direction,
        entry=entry,
    )

    if levels is None:

        stats["level_rejects"] += 1

        return False

    stats["valid_levels"] += 1

    tp = levels["tp"]
    sl = levels["sl"]
    rr = levels["rr"]

    # --------------------------------------------------------
    # Final validation
    # --------------------------------------------------------

    if direction == "LONG":

        if not (
            sl < entry < tp
        ):
            stats["level_rejects"] += 1
            return False

    else:

        if not (
            tp < entry < sl
        ):
            stats["level_rejects"] += 1
            return False

    # --------------------------------------------------------
    # Insert
    # --------------------------------------------------------

    signal_time = (
        breakout_5m["time"].isoformat()
    )

    inserted = insert_signal(
        asset=asset,
        direction=direction,
        signal_time=signal_time,
        entry=entry,
        tp=tp,
        sl=sl,
        rr=rr,
        trendline_1h=trendline_1h,
        trendline_5m=trendline_5m,
        breakout_1h_time=breakout_1h["time"].isoformat(),
        breakout_5m_time=breakout_5m["time"].isoformat(),
    )

    if not inserted:
        return False

    stats["new_signals"] += 1

    # --------------------------------------------------------
    # Chart
    # --------------------------------------------------------

    chart_path = make_signal_chart(
        asset=asset,
        candles=candles_5m,
        trendline=trendline_5m,
        breakout=breakout_5m,
        entry=entry,
        tp=tp,
        sl=sl,
    )

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    send_signal(
        asset=asset,
        direction=direction,
        entry=entry,
        tp=tp,
        sl=sl,
        rr=rr,
        rvol=rvol,
        breakout_1h=breakout_1h,
        breakout_5m=breakout_5m,
        trendline_5m=trendline_5m,
        chart_path=chart_path,
    )

    return True


# ============================================================
# SCAN SUMMARY
# ============================================================

def send_scan_summary():

    text = (
        "📊 <b>KRAKEN TRENDLINE SCANNER</b>\n\n"
        f"Version: {VERSION}\n"
        f"Mode: PAPER ONLY\n"
        f"Assets: "
        f"{stats['assets_scanned']}/{len(ASSETS)}\n\n"

        f"1H Valid Trendlines: "
        f"{stats['trendlines_1h']}\n"

        f"1H Breakouts: "
        f"{stats['breakouts_1h']}\n"

        f"5M Trendlines: "
        f"{stats['trendlines_5m']}\n"

        f"5M Breakouts: "
        f"{stats['breakouts_5m']}\n"

        f"Volume Accepted: "
        f"{stats['volume_accepted']}\n"

        f"Valid TP/SL: "
        f"{stats['valid_levels']}\n\n"

        f"New Signals: "
        f"{stats['new_signals']}\n"

        f"Closed Trades: "
        f"{stats['closed_trades']}\n\n"

        f"Level Rejects: "
        f"{stats['level_rejects']}\n"

        f"Errors: "
        f"{stats['errors']}\n\n"

        f"Time: "
        f"{format_tehran(utc_now())}"
    )

    telegram_send(text)


# ============================================================
# MAIN SCAN
# ============================================================

def scan():

    # ========================================================
    # CRITICAL:
    #
    # Every execution starts with a brand-new runtime
    # statistics object.
    #
    # This DOES NOT touch the SQLite database.
    # ========================================================

    reset_stats()

    # --------------------------------------------------------
    # Database remains preserved
    # --------------------------------------------------------

    init_db()

    # --------------------------------------------------------
    # Tickers
    # --------------------------------------------------------

    tickers = fetch_tickers()

    # --------------------------------------------------------
    # Reconcile existing open trades
    # --------------------------------------------------------

    closed_trades = reconcile_open_trades(
        tickers
    )

    for trade in closed_trades:
        send_close_message(trade)

    # --------------------------------------------------------
    # Scan assets
    # --------------------------------------------------------

    for asset in ASSETS:

        stats["assets_scanned"] += 1

        try:

            new_signal = scan_asset(
                asset,
                tickers,
            )

            # ------------------------------------------------
            # Maximum one new signal per scan
            # ------------------------------------------------

            if (
                new_signal
                and
                stats["new_signals"]
                >=
                MAX_SIGNALS_PER_SCAN
            ):
                break

        except Exception:

            stats["errors"] += 1

            print(
                f"[ERROR] {asset}"
            )

            traceback.print_exc()

    # --------------------------------------------------------
    # Open trades
    # --------------------------------------------------------

    send_open_trades()

    # --------------------------------------------------------
    # Performance
    # --------------------------------------------------------

    send_performance()

    # --------------------------------------------------------
    # Current scan summary
    # --------------------------------------------------------

    send_scan_summary()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        if REAL_TRADING:
            raise RuntimeError(
                "REAL_TRADING must remain False."
            )

        scan()

    except Exception:

        print(
            "\n[FATAL ERROR]"
        )

        traceback.print_exc()

        try:

            telegram_send(
                "⚠️ <b>TRENDLINE SCANNER ERROR</b>\n\n"
                "Scanner crashed.\n"
                f"Version: {VERSION}"
            )

        except Exception:
            pass

        sys.exit(1)
