# ============================================================
# KRAKEN FUTURES ICHIMOKU 40 LIVE SCANNER
# VERSION 2.0.0
# ============================================================
#
# PAPER ONLY
#
# STRATEGY
# ------------------------------------------------------------
# 1H:
#   LONG  = price above Kumo + bullish cloud
#   SHORT = price below Kumo + bearish cloud
#
# 5M:
#   LONG:
#       Chikou crosses above price
#       AND price above visible Kumo
#       AND 1H trend LONG
#
#   SHORT:
#       Chikou crosses below price
#       AND price below visible Kumo
#       AND 1H trend SHORT
#
# CLOSED CANDLES ONLY
# NO LOOKAHEAD
#
# CHIKOU CROSS:
#   bullish:
#       close[t] > close[t-26]
#       close[t-1] <= close[t-27]
#
#   bearish:
#       close[t] < close[t-26]
#       close[t-1] >= close[t-27]
#
# SL / TP:
#   Combined from:
#       Tenkan
#       Kijun
#       Senkou A
#       Senkou B
#       Confirmed 5M swing high / low
#
# LONG:
#   SL = nearest valid support below entry
#   TP = nearest valid resistance above entry
#        that gives RR >= 1
#
# SHORT:
#   SL = nearest valid resistance above entry
#   TP = nearest valid support below entry
#        that gives RR >= 1
#
# SWINGS:
#   LEFT  = 2
#   RIGHT = 2
#   Only confirmed swings are used.
#
# EXITS:
#   LONG:
#       SL / TP
#       OR 5M close enters/leaves opposite Ichimoku state
#       OR Chikou bearish cross
#
#   SHORT:
#       SL / TP
#       OR 5M close enters/leaves opposite Ichimoku state
#       OR Chikou bullish cross
#
# TELEGRAM:
#   New signal -> chart + Ichimoku + Entry/SL/TP
#   No new signal -> explicit "هیچ سیگنال جدیدی نداریم"
#   Open trades -> current PnL / SL distance / TP distance
#   Performance -> trades / wins / losses / win rate / total PnL
#
# ============================================================

import os
import time
import math
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

VERSION = "2.0.0"

REAL_TRADING = False
PAPER_TRADING = True

DB_FILE = "kraken_ichimoku_40.db"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

KRAKEN_BASE = "https://futures.kraken.com"

# Kraken Futures Charts API
CHART_URL = KRAKEN_BASE + "/api/charts/v1"

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
    "INJ",
    "PEPE",
    "SHIB",
    "BONK",
    "WIF",
    "SEI",
    "TIA",
    "JUP",
    "JTO",
    "RUNE",
    "ICP",
    "APT",
    "FET",
    "ALGO",
    "XLM",
    "BCH",
    "ETC",
    "EOS",
    "MKR",
    "COMP",
]

SYMBOL_MAP = {
    asset: f"PF_{asset}USD"
    for asset in ASSETS
}

# ------------------------------------------------------------
# Timeframes
# ------------------------------------------------------------

TF_1H = "60m"
TF_5M = "5m"

# ------------------------------------------------------------
# Candle counts
# ------------------------------------------------------------

COUNT_1H = 300
COUNT_5M = 500

# ------------------------------------------------------------
# Ichimoku
# ------------------------------------------------------------

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26
SENKOU_B_PERIOD = 52
DISPLACEMENT = 26

# ------------------------------------------------------------
# Swing
# ------------------------------------------------------------

SWING_LEFT = 2
SWING_RIGHT = 2

# ------------------------------------------------------------
# Trading
# ------------------------------------------------------------

MIN_RR = 1.0

# ------------------------------------------------------------
# Scanner
# ------------------------------------------------------------

SCAN_INTERVAL_SECONDS = 60
REPORT_INTERVAL_SECONDS = 300

REQUEST_TIMEOUT = 20

# How many candles to show on Telegram chart
CHART_CANDLES = 140

# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 Kraken-Ichi-40-Scanner/2.0",
        "Accept": "application/json",
    }
)


# ============================================================
# HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def utc_iso(dt=None):
    if dt is None:
        dt = now_utc()

    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def fmt_price(value):
    if value is None:
        return "-"

    value = float(value)

    if value == 0:
        return "0"

    if abs(value) >= 1000:
        return f"{value:,.2f}"

    if abs(value) >= 100:
        return f"{value:,.3f}"

    if abs(value) >= 1:
        return f"{value:,.4f}"

    if abs(value) >= 0.01:
        return f"{value:,.6f}"

    if abs(value) >= 0.0001:
        return f"{value:,.8f}"

    return f"{value:.10f}"


def fmt_pct(value):
    if value is None:
        return "-"

    return f"{float(value):+.2f}%"


def safe_float(value):
    try:
        return float(value)
    except Exception:
        return None


def is_valid_number(value):
    return (
        value is not None
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


# ============================================================
# KRAKEN DATA
# ============================================================

def parse_candle(item):
    """
    Supports common Kraken Futures chart response formats.
    """

    if isinstance(item, dict):

        timestamp = (
            item.get("time")
            or item.get("timestamp")
            or item.get("ts")
            or item.get("t")
        )

        open_price = (
            item.get("open")
            or item.get("o")
        )

        high_price = (
            item.get("high")
            or item.get("h")
        )

        low_price = (
            item.get("low")
            or item.get("l")
        )

        close_price = (
            item.get("close")
            or item.get("c")
        )

        volume = (
            item.get("volume")
            or item.get("v")
            or 0
        )

    elif isinstance(item, (list, tuple)) and len(item) >= 5:

        timestamp = item[0]
        open_price = item[1]
        high_price = item[2]
        low_price = item[3]
        close_price = item[4]

        volume = item[5] if len(item) > 5 else 0

    else:
        return None

    timestamp = safe_float(timestamp)

    if timestamp is None:
        return None

    # Normalize seconds -> milliseconds
    if timestamp < 10_000_000_000:
        timestamp *= 1000

    o = safe_float(open_price)
    h = safe_float(high_price)
    l = safe_float(low_price)
    c = safe_float(close_price)
    v = safe_float(volume)

    if not all(
        is_valid_number(x)
        for x in [o, h, l, c]
    ):
        return None

    return {
        "time": int(timestamp),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v if v is not None else 0.0,
    }


def normalize_candles(data):
    raw = None

    if isinstance(data, dict):

        for key in (
            "candles",
            "data",
            "results",
            "result",
        ):
            if key in data and isinstance(data[key], list):
                raw = data[key]
                break

    elif isinstance(data, list):
        raw = data

    if not raw:
        return []

    candles = []

    for item in raw:
        candle = parse_candle(item)

        if candle is not None:
            candles.append(candle)

    candles.sort(key=lambda x: x["time"])

    # Remove duplicate timestamps
    unique = {}

    for candle in candles:
        unique[candle["time"]] = candle

    candles = list(unique.values())
    candles.sort(key=lambda x: x["time"])

    return candles


def fetch_candles(symbol, resolution, count):
    """
    Kraken Futures Charts API.

    Uses:
        /api/charts/v1/trade/{symbol}/{resolution}
    """

    url = f"{CHART_URL}/trade/{symbol}/{resolution}"

    params = {
        "count": count,
    }

    try:
        response = SESSION.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        candles = normalize_candles(data)

        if not candles:
            print(
                f"[WARN] No candles: {symbol} {resolution}"
            )
            return []

        return candles

    except Exception as exc:
        print(
            f"[ERROR] Fetch failed "
            f"{symbol} {resolution}: {exc}"
        )

        return []


# ============================================================
# CLOSED CANDLES
# ============================================================

def candle_duration_ms(resolution):
    if resolution == "5m":
        return 5 * 60 * 1000

    if resolution == "60m":
        return 60 * 60 * 1000

    return 5 * 60 * 1000


def get_closed_candles(candles, resolution):
    """
    Removes the currently forming candle.

    If the API already returns only closed candles,
    this leaves the latest candle intact.
    """

    if len(candles) < 3:
        return []

    duration = candle_duration_ms(resolution)
    current_ms = int(time.time() * 1000)

    closed = []

    for candle in candles:

        candle_end = candle["time"] + duration

        if candle_end <= current_ms:
            closed.append(candle)

    return closed


# ============================================================
# ICHIMOKU
# ============================================================

def rolling_midpoint(highs, lows, period, index):
    start = index - period + 1

    if start < 0:
        return None

    window_highs = highs[start:index + 1]
    window_lows = lows[start:index + 1]

    if len(window_highs) != period:
        return None

    return (
        max(window_highs)
        + min(window_lows)
    ) / 2.0


def calculate_ichimoku(candles):
    n = len(candles)

    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]

    tenkan = [None] * n
    kijun = [None] * n
    senkou_a_raw = [None] * n
    senkou_b_raw = [None] * n

    for i in range(n):

        tenkan[i] = rolling_midpoint(
            highs,
            lows,
            TENKAN_PERIOD,
            i,
        )

        kijun[i] = rolling_midpoint(
            highs,
            lows,
            KIJUN_PERIOD,
            i,
        )

        senkou_b_raw[i] = rolling_midpoint(
            highs,
            lows,
            SENKOU_B_PERIOD,
            i,
        )

        if (
            tenkan[i] is not None
            and kijun[i] is not None
        ):
            senkou_a_raw[i] = (
                tenkan[i]
                + kijun[i]
            ) / 2.0

    # --------------------------------------------------------
    # Visible cloud at candle t
    #
    # Senkou values calculated at t-26 are displayed at t.
    # Therefore visible cloud[t] = raw[t-26].
    # --------------------------------------------------------

    visible_a = [None] * n
    visible_b = [None] * n

    for i in range(n):

        source_index = i - DISPLACEMENT

        if source_index >= 0:

            visible_a[i] = (
                senkou_a_raw[source_index]
            )

            visible_b[i] = (
                senkou_b_raw[source_index]
            )

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a_raw": senkou_a_raw,
        "senkou_b_raw": senkou_b_raw,
        "visible_a": visible_a,
        "visible_b": visible_b,
        "closes": closes,
    }


def ichimoku_state(ichi, index):
    close = ichi["closes"][index]

    cloud_a = ichi["visible_a"][index]
    cloud_b = ichi["visible_b"][index]

    if (
        cloud_a is None
        or cloud_b is None
        or close is None
    ):
        return "NEUTRAL"

    cloud_top = max(cloud_a, cloud_b)
    cloud_bottom = min(cloud_a, cloud_b)

    bullish_cloud = cloud_a > cloud_b
    bearish_cloud = cloud_a < cloud_b

    if (
        close > cloud_top
        and bullish_cloud
    ):
        return "LONG"

    if (
        close < cloud_bottom
        and bearish_cloud
    ):
        return "SHORT"

    return "NEUTRAL"


# ============================================================
# CHIKOU
# ============================================================

def bullish_chikou_cross(closes, index):
    if index < DISPLACEMENT + 1:
        return False

    current_reference = index - DISPLACEMENT
    previous_reference = index - DISPLACEMENT - 1

    return (
        closes[index]
        > closes[current_reference]
        and
        closes[index - 1]
        <= closes[previous_reference]
    )


def bearish_chikou_cross(closes, index):
    if index < DISPLACEMENT + 1:
        return False

    current_reference = index - DISPLACEMENT
    previous_reference = index - DISPLACEMENT - 1

    return (
        closes[index]
        < closes[current_reference]
        and
        closes[index - 1]
        >= closes[previous_reference]
    )


# ============================================================
# SWINGS
# ============================================================

def confirmed_swing_highs(candles):
    highs = [c["high"] for c in candles]

    result = []

    for i in range(
        SWING_LEFT,
        len(candles) - SWING_RIGHT,
    ):

        left = highs[
            i - SWING_LEFT:i
        ]

        right = highs[
            i + 1:i + 1 + SWING_RIGHT
        ]

        if not left or not right:
            continue

        value = highs[i]

        if all(value > x for x in left) and all(
            value >= x for x in right
        ):
            result.append(
                {
                    "index": i,
                    "price": value,
                    "time": candles[i]["time"],
                    "type": "Swing High",
                }
            )

    return result


def confirmed_swing_lows(candles):
    lows = [c["low"] for c in candles]

    result = []

    for i in range(
        SWING_LEFT,
        len(candles) - SWING_RIGHT,
    ):

        left = lows[
            i - SWING_LEFT:i
        ]

        right = lows[
            i + 1:i + 1 + SWING_RIGHT
        ]

        if not left or not right:
            continue

        value = lows[i]

        if all(value < x for x in left) and all(
            value <= x for x in right
        ):
            result.append(
                {
                    "index": i,
                    "price": value,
                    "time": candles[i]["time"],
                    "type": "Swing Low",
                }
            )

    return result


# ============================================================
# LEVEL SELECTION
# ============================================================

def add_level(levels, price, source, index=None):
    if not is_valid_number(price):
        return

    price = float(price)

    if price <= 0:
        return

    levels.append(
        {
            "price": price,
            "source": source,
            "index": index,
        }
    )


def build_level_candidates(
    candles,
    ichi,
    entry_index,
):
    entry = candles[entry_index]["close"]

    supports = []
    resistances = []

    # --------------------------------------------------------
    # Ichimoku components
    # --------------------------------------------------------

    add_level(
        supports,
        ichi["tenkan"][entry_index],
        "Tenkan",
        entry_index,
    )

    add_level(
        supports,
        ichi["kijun"][entry_index],
        "Kijun",
        entry_index,
    )

    add_level(
        supports,
        ichi["visible_a"][entry_index],
        "Senkou A",
        entry_index,
    )

    add_level(
        supports,
        ichi["visible_b"][entry_index],
        "Senkou B",
        entry_index,
    )

    # Same Ichimoku levels can act as resistance
    add_level(
        resistances,
        ichi["tenkan"][entry_index],
        "Tenkan",
        entry_index,
    )

    add_level(
        resistances,
        ichi["kijun"][entry_index],
        "Kijun",
        entry_index,
    )

    add_level(
        resistances,
        ichi["visible_a"][entry_index],
        "Senkou A",
        entry_index,
    )

    add_level(
        resistances,
        ichi["visible_b"][entry_index],
        "Senkou B",
        entry_index,
    )

    # --------------------------------------------------------
    # Confirmed swings
    #
    # Only swings confirmed by the entry candle are used.
    # Latest possible swing index is entry_index - 2.
    # --------------------------------------------------------

    highs = confirmed_swing_highs(candles)
    lows = confirmed_swing_lows(candles)

    max_confirmed_index = (
        entry_index - SWING_RIGHT
    )

    for swing in lows:

        if swing["index"] <= max_confirmed_index:

            add_level(
                supports,
                swing["price"],
                "Swing Low",
                swing["index"],
            )

    for swing in highs:

        if swing["index"] <= max_confirmed_index:

            add_level(
                resistances,
                swing["price"],
                "Swing High",
                swing["index"],
            )

    # --------------------------------------------------------
    # Keep only meaningful levels
    # --------------------------------------------------------

    supports = [
        x for x in supports
        if x["price"] < entry
    ]

    resistances = [
        x for x in resistances
        if x["price"] > entry
    ]

    # Remove duplicate price levels
    supports = deduplicate_levels(supports)
    resistances = deduplicate_levels(resistances)

    return supports, resistances


def deduplicate_levels(levels):
    result = []

    seen = set()

    for level in levels:

        key = round(level["price"], 12)

        if key in seen:
            continue

        seen.add(key)
        result.append(level)

    return result


def choose_long_levels(
    entry,
    supports,
    resistances,
):
    """
    LONG:
        SL = nearest support below entry
        TP = nearest resistance above entry
             satisfying RR >= 1
    """

    if not supports or not resistances:
        return None

    supports = sorted(
        supports,
        key=lambda x: x["price"],
        reverse=True,
    )

    resistances = sorted(
        resistances,
        key=lambda x: x["price"],
    )

    for sl in supports:

        risk = entry - sl["price"]

        if risk <= 0:
            continue

        for tp in resistances:

            reward = tp["price"] - entry

            if reward <= 0:
                continue

            rr = reward / risk

            if rr >= MIN_RR:

                return {
                    "sl": sl["price"],
                    "tp": tp["price"],
                    "rr": rr,
                    "sl_source": sl["source"],
                    "tp_source": tp["source"],
                    "sl_index": sl.get("index"),
                    "tp_index": tp.get("index"),
                }

    return None


def choose_short_levels(
    entry,
    supports,
    resistances,
):
    """
    SHORT:
        SL = nearest resistance above entry
        TP = nearest support below entry
             satisfying RR >= 1
    """

    if not supports or not resistances:
        return None

    resistances = sorted(
        resistances,
        key=lambda x: x["price"],
    )

    supports = sorted(
        supports,
        key=lambda x: x["price"],
        reverse=True,
    )

    for sl in resistances:

        risk = sl["price"] - entry

        if risk <= 0:
            continue

        for tp in supports:

            reward = entry - tp["price"]

            if reward <= 0:
                continue

            rr = reward / risk

            if rr >= MIN_RR:

                return {
                    "sl": sl["price"],
                    "tp": tp["price"],
                    "rr": rr,
                    "sl_source": sl["source"],
                    "tp_source": tp["source"],
                    "sl_index": sl.get("index"),
                    "tp_index": tp.get("index"),
                }

    return None


# ============================================================
# SIGNAL
# ============================================================

def detect_signal(
    candles_1h,
    ichi_1h,
    candles_5m,
    ichi_5m,
):
    if len(candles_1h) < 100:
        return None

    if len(candles_5m) < 100:
        return None

    i1 = len(candles_1h) - 1
    i5 = len(candles_5m) - 1

    trend_1h = ichimoku_state(
        ichi_1h,
        i1,
    )

    state_5m = ichimoku_state(
        ichi_5m,
        i5,
    )

    closes_5m = ichi_5m["closes"]

    entry = candles_5m[i5]["close"]

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if (
        trend_1h == "LONG"
        and state_5m == "LONG"
        and bullish_chikou_cross(
            closes_5m,
            i5,
        )
    ):

        supports, resistances = (
            build_level_candidates(
                candles_5m,
                ichi_5m,
                i5,
            )
        )

        levels = choose_long_levels(
            entry,
            supports,
            resistances,
        )

        if levels is None:
            return None

        return {
            "direction": "LONG",
            "entry": entry,
            "signal_time": candles_5m[i5]["time"],
            "trend_1h": trend_1h,
            "state_5m": state_5m,
            **levels,
        }

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if (
        trend_1h == "SHORT"
        and state_5m == "SHORT"
        and bearish_chikou_cross(
            closes_5m,
            i5,
        )
    ):

        supports, resistances = (
            build_level_candidates(
                candles_5m,
                ichi_5m,
                i5,
            )
        )

        levels = choose_short_levels(
            entry,
            supports,
            resistances,
        )

        if levels is None:
            return None

        return {
            "direction": "SHORT",
            "entry": entry,
            "signal_time": candles_5m[i5]["time"],
            "trend_1h": trend_1h,
            "state_5m": state_5m,
            **levels,
        }

    return None


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(
        DB_FILE,
        timeout=30,
    )

    conn.row_factory = sqlite3.Row

    return conn


def ensure_column(
    conn,
    table,
    column,
    definition,
):
    cursor = conn.execute(
        f"PRAGMA table_info({table})"
    )

    columns = {
        row["name"]
        for row in cursor.fetchall()
    }

    if column not in columns:

        conn.execute(
            f"""
            ALTER TABLE {table}
            ADD COLUMN {column} {definition}
            """
        )


def init_db():
    conn = db_connect()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            signal_time INTEGER NOT NULL,
            entry REAL NOT NULL,
            sl REAL NOT NULL,
            tp REAL NOT NULL,
            rr REAL,
            sl_source TEXT,
            tp_source TEXT,
            status TEXT NOT NULL DEFAULT 'OPEN',
            exit_price REAL,
            exit_time INTEGER,
            exit_reason TEXT,
            pnl_pct REAL
        )
        """
    )

    # Migration support
    ensure_column(
        conn,
        "trades",
        "rr",
        "REAL",
    )

    ensure_column(
        conn,
        "trades",
        "sl_source",
        "TEXT",
    )

    ensure_column(
        conn,
        "trades",
        "tp_source",
        "TEXT",
    )

    ensure_column(
        conn,
        "trades",
        "exit_price",
        "REAL",
    )

    ensure_column(
        conn,
        "trades",
        "exit_time",
        "INTEGER",
    )

    ensure_column(
        conn,
        "trades",
        "exit_reason",
        "TEXT",
    )

    ensure_column(
        conn,
        "trades",
        "pnl_pct",
        "REAL",
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_trades_status
        ON trades(status)
        """
    )

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_trades_signal_unique
        ON trades(symbol, direction, signal_time)
        """
    )

    conn.commit()
    conn.close()


def has_open_trade(
    symbol,
    direction,
):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
        FROM trades
        WHERE symbol = ?
          AND direction = ?
          AND status = 'OPEN'
        LIMIT 1
        """,
        (
            symbol,
            direction,
        ),
    ).fetchone()

    conn.close()

    return row is not None


def signal_already_exists(
    symbol,
    direction,
    signal_time,
):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
        FROM trades
        WHERE symbol = ?
          AND direction = ?
          AND signal_time = ?
        LIMIT 1
        """,
        (
            symbol,
            direction,
            signal_time,
        ),
    ).fetchone()

    conn.close()

    return row is not None


def insert_trade(
    asset,
    symbol,
    signal,
):
    conn = db_connect()

    try:

        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO trades (
                asset,
                symbol,
                direction,
                signal_time,
                entry,
                sl,
                tp,
                rr,
                sl_source,
                tp_source,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
            """,
            (
                asset,
                symbol,
                signal["direction"],
                signal["signal_time"],
                signal["entry"],
                signal["sl"],
                signal["tp"],
                signal["rr"],
                signal["sl_source"],
                signal["tp_source"],
            ),
        )

        conn.commit()

        inserted = cursor.rowcount == 1

    except Exception as exc:

        print(
            f"[ERROR] DB insert: {exc}"
        )

        inserted = False

    finally:
        conn.close()

    return inserted


def get_open_trades():
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

    return rows


def calculate_pnl(
    direction,
    entry,
    current,
):
    if entry <= 0:
        return 0.0

    if direction == "LONG":

        return (
            (current - entry)
            / entry
            * 100.0
        )

    return (
        (entry - current)
        / entry
        * 100.0
    )


def close_trade(
    trade_id,
    exit_price,
    exit_time,
    exit_reason,
):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE id = ?
        """,
        (trade_id,),
    ).fetchone()

    if row is None:
        conn.close()
        return None

    pnl = calculate_pnl(
        row["direction"],
        row["entry"],
        exit_price,
    )

    conn.execute(
        """
        UPDATE trades
        SET
            status = 'CLOSED',
            exit_price = ?,
            exit_time = ?,
            exit_reason = ?,
            pnl_pct = ?
        WHERE id = ?
        """,
        (
            exit_price,
            exit_time,
            exit_reason,
            pnl,
            trade_id,
        ),
    )

    conn.commit()
    conn.close()

    return {
        "id": trade_id,
        "asset": row["asset"],
        "symbol": row["symbol"],
        "direction": row["direction"],
        "entry": row["entry"],
        "exit": exit_price,
        "reason": exit_reason,
        "pnl": pnl,
    }


# ============================================================
# TELEGRAM
# ============================================================

def telegram_enabled():
    return bool(
        TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    )


def telegram_url(method):
    return (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/{method}"
    )


def send_telegram_message(text):
    if not telegram_enabled():
        print(
            "[WARN] Telegram secrets are not configured."
        )
        return False

    try:

        response = SESSION.post(
            telegram_url("sendMessage"),
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=REQUEST_TIMEOUT,
        )

        if not response.ok:
            print(
                "[ERROR] Telegram message:",
                response.text[:500],
            )

            return False

        return True

    except Exception as exc:

        print(
            f"[ERROR] Telegram message failed: {exc}"
        )

        return False


def send_telegram_photo(
    image_path,
    caption,
):
    if not telegram_enabled():
        return False

    try:

        with open(
            image_path,
            "rb",
        ) as photo:

            response = SESSION.post(
                telegram_url("sendPhoto"),
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption[:1024],
                },
                files={
                    "photo": photo,
                },
                timeout=REQUEST_TIMEOUT,
            )

        if not response.ok:
            print(
                "[ERROR] Telegram photo:",
                response.text[:500],
            )

            return False

        return True

    except Exception as exc:

        print(
            f"[ERROR] Telegram photo failed: {exc}"
        )

        return False


# ============================================================
# CHART
# ============================================================

def create_ichimoku_chart(
    candles,
    ichi,
    signal,
    symbol,
    output_path,
):
    if len(candles) < 60:
        return False

    n = len(candles)

    start = max(
        0,
        n - CHART_CANDLES,
    )

    view_candles = candles[start:]

    x_all = [
        datetime.fromtimestamp(
            c["time"] / 1000,
            tz=timezone.utc,
        )
        for c in candles
    ]

    x = x_all[start:]

    fig, ax = plt.subplots(
        figsize=(15, 9)
    )

    # --------------------------------------------------------
    # Candles
    # --------------------------------------------------------

    candle_width = (
        5 / 1440
    ) * 0.65

    for j, candle in enumerate(
        view_candles
    ):

        idx = start + j

        o = candle["open"]
        h = candle["high"]
        l = candle["low"]
        c = candle["close"]

        xnum = mdates.date2num(x[j])

        if c >= o:
            body_color = "#16a34a"
        else:
            body_color = "#dc2626"

        ax.vlines(
            xnum,
            l,
            h,
            color="black",
            linewidth=0.7,
            alpha=0.8,
        )

        body_low = min(o, c)
        body_height = abs(c - o)

        if body_height == 0:
            body_height = max(
                c * 0.00005,
                1e-12,
            )

        rect = Rectangle(
            (
                xnum - candle_width / 2,
                body_low,
            ),
            candle_width,
            body_height,
            facecolor=body_color,
            edgecolor=body_color,
            linewidth=0.5,
        )

        ax.add_patch(rect)

    # --------------------------------------------------------
    # Ichimoku
    # --------------------------------------------------------

    tenkan = ichi["tenkan"][start:]
    kijun = ichi["kijun"][start:]
    cloud_a = ichi["visible_a"][start:]
    cloud_b = ichi["visible_b"][start:]

    ax.plot(
        x,
        tenkan,
        label="Tenkan",
        linewidth=1.2,
    )

    ax.plot(
        x,
        kijun,
        label="Kijun",
        linewidth=1.2,
    )

    ax.plot(
        x,
        cloud_a,
        label="Senkou A",
        linewidth=1.0,
        linestyle="--",
    )

    ax.plot(
        x,
        cloud_b,
        label="Senkou B",
        linewidth=1.0,
        linestyle="--",
    )

    # Cloud
    valid_cloud = []

    for a, b in zip(
        cloud_a,
        cloud_b,
    ):
        valid_cloud.append(
            (
                is_valid_number(a)
                and is_valid_number(b)
            )
        )

    if any(valid_cloud):

        cloud_a_plot = [
            a if valid else math.nan
            for a, valid in zip(
                cloud_a,
                valid_cloud,
            )
        ]

        cloud_b_plot = [
            b if valid else math.nan
            for b, valid in zip(
                cloud_b,
                valid_cloud,
            )
        ]

        ax.fill_between(
            x,
            cloud_a_plot,
            cloud_b_plot,
            where=[
                bool(v)
                for v in valid_cloud
            ],
            alpha=0.18,
            label="Kumo",
        )

    # --------------------------------------------------------
    # Chikou
    #
    # close[t] is plotted at t-26
    # --------------------------------------------------------

    if n > DISPLACEMENT:

        chikou_x = x_all[
            start:max(
                start,
                n - DISPLACEMENT,
            )
        ]

        chikou_y = []

        for i in range(
            start + DISPLACEMENT,
            n,
        ):
            chikou_y.append(
                candles[i]["close"]
            )

        if len(chikou_x) == len(chikou_y):

            ax.plot(
                chikou_x,
                chikou_y,
                label="Chikou",
                linewidth=1.0,
                linestyle=":",
            )

    # --------------------------------------------------------
    # Entry / SL / TP
    # --------------------------------------------------------

    entry = signal["entry"]
    sl = signal["sl"]
    tp = signal["tp"]

    ax.axhline(
        entry,
        linestyle="--",
        linewidth=1.4,
        label=f"Entry {fmt_price(entry)}",
    )

    ax.axhline(
        sl,
        linestyle="--",
        linewidth=1.5,
        label=(
            f"SL {fmt_price(sl)} "
            f"({signal['sl_source']})"
        ),
    )

    ax.axhline(
        tp,
        linestyle="--",
        linewidth=1.5,
        label=(
            f"TP {fmt_price(tp)} "
            f"({signal['tp_source']})"
        ),
    )

    # --------------------------------------------------------
    # Mark chosen swing if applicable
    # --------------------------------------------------------

    sl_index = signal.get("sl_index")
    tp_index = signal.get("tp_index")

    if (
        signal["sl_source"].startswith("Swing")
        and sl_index is not None
        and start <= sl_index < n
    ):

        ax.scatter(
            x_all[sl_index],
            sl,
            s=70,
            marker="v",
            zorder=10,
            label="SL Swing",
        )

    if (
        signal["tp_source"].startswith("Swing")
        and tp_index is not None
        and start <= tp_index < n
    ):

        ax.scatter(
            x_all[tp_index],
            tp,
            s=70,
            marker="^",
            zorder=10,
            label="TP Swing",
        )

    # --------------------------------------------------------
    # Signal marker
    # --------------------------------------------------------

    last_x = x_all[-1]

    if signal["direction"] == "LONG":

        ax.scatter(
            last_x,
            entry,
            s=120,
            marker="^",
            zorder=20,
            label="LONG ENTRY",
        )

    else:

        ax.scatter(
            last_x,
            entry,
            s=120,
            marker="v",
            zorder=20,
            label="SHORT ENTRY",
        )

    # --------------------------------------------------------
    # Title
    # --------------------------------------------------------

    title_direction = signal["direction"]

    ax.set_title(
        (
            f"{symbol} | 5M Ichimoku | "
            f"{title_direction}\n"
            f"Entry {fmt_price(entry)} | "
            f"SL {fmt_price(sl)} | "
            f"TP {fmt_price(tp)} | "
            f"RR {signal['rr']:.2f}"
        ),
        fontsize=13,
        fontweight="bold",
    )

    ax.set_ylabel("Price")
    ax.grid(
        True,
        alpha=0.20,
    )

    ax.xaxis.set_major_formatter(
        mdates.DateFormatter(
            "%m-%d %H:%M",
            tz=timezone.utc,
        )
    )

    ax.tick_params(
        axis="x",
        rotation=30,
    )

    ax.legend(
        loc="upper left",
        fontsize=8,
        ncol=2,
    )

    fig.tight_layout()

    try:
        fig.savefig(
            output_path,
            dpi=150,
            bbox_inches="tight",
        )

        plt.close(fig)

        return True

    except Exception as exc:

        print(
            f"[ERROR] Chart save failed: {exc}"
        )

        plt.close(fig)

        return False


# ============================================================
# OPEN TRADE EXIT
# ============================================================

def evaluate_open_trade(
    trade,
    candles,
    ichi,
):
    if not candles:
        return None

    # --------------------------------------------------------
    # Current price
    # --------------------------------------------------------

    current_price = candles[-1]["close"]

    # --------------------------------------------------------
    # Closed candle for logic
    # --------------------------------------------------------

    closed = get_closed_candles(
        candles,
        TF_5M,
    )

    if not closed:
        return None

    last = closed[-1]
    i = len(closed) - 1

    closed_ichi = calculate_ichimoku(
        closed
    )

    current_close = last["close"]
    candle_high = last["high"]
    candle_low = last["low"]

    direction = trade["direction"]

    entry = trade["entry"]
    sl = trade["sl"]
    tp = trade["tp"]

    # --------------------------------------------------------
    # HARD SL / TP
    #
    # If both hit on the same candle:
    # SL is considered first.
    # --------------------------------------------------------

    if direction == "LONG":

        if candle_low <= sl:
            return {
                "reason": "SL",
                "price": sl,
                "current": current_price,
            }

        if candle_high >= tp:
            return {
                "reason": "TP",
                "price": tp,
                "current": current_price,
            }

    else:

        if candle_high >= sl:
            return {
                "reason": "SL",
                "price": sl,
                "current": current_price,
            }

        if candle_low <= tp:
            return {
                "reason": "TP",
                "price": tp,
                "current": current_price,
            }

    # --------------------------------------------------------
    # Ichimoku exits
    # --------------------------------------------------------

    state = ichimoku_state(
        closed_ichi,
        i,
    )

    closes = closed_ichi["closes"]

    if direction == "LONG":

        if bearish_chikou_cross(
            closes,
            i,
        ):
            return {
                "reason": "CHIKOU_EXIT",
                "price": current_close,
                "current": current_price,
            }

        if state != "LONG":
            return {
                "reason": "KUMO_EXIT",
                "price": current_close,
                "current": current_price,
            }

    else:

        if bullish_chikou_cross(
            closes,
            i,
        ):
            return {
                "reason": "CHIKOU_EXIT",
                "price": current_close,
                "current": current_price,
            }

        if state != "SHORT":
            return {
                "reason": "KUMO_EXIT",
                "price": current_close,
                "current": current_price,
            }

    return {
        "reason": None,
        "price": current_price,
        "current": current_price,
    }


# ============================================================
# REPORT
# ============================================================

def performance_stats():
    conn = db_connect()

    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(
                CASE
                    WHEN pnl_pct > 0 THEN 1
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
            ) AS total_pnl
        FROM trades
        WHERE status = 'CLOSED'
        """
    ).fetchone()

    conn.close()

    total = int(row["total"] or 0)
    wins = int(row["wins"] or 0)
    losses = int(row["losses"] or 0)
    total_pnl = float(row["total_pnl"] or 0)

    decided = wins + losses

    win_rate = (
        wins / decided * 100
        if decided > 0
        else 0.0
    )

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
    }


def build_report(
    trend_counts,
    new_signals,
    open_details,
):
    stats = performance_stats()

    lines = []

    lines.append(
        "📊 KRAKEN ICHIMOKU 40"
    )

    lines.append(
        f"Version: {VERSION}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # 1H trend
    # --------------------------------------------------------

    lines.append(
        "🕐 1H TREND"
    )

    lines.append(
        f"🟢 LONG: {trend_counts['LONG']}   "
        f"🔴 SHORT: {trend_counts['SHORT']}   "
        f"⚪ NEUTRAL: {trend_counts['NEUTRAL']}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # New signals
    # --------------------------------------------------------

    lines.append(
        "🆕 NEW SIGNALS"
    )

    if not new_signals:

        lines.append(
            "❌ هیچ سیگنال جدیدی نداریم"
        )

    else:

        for signal in new_signals:

            emoji = (
                "🟢"
                if signal["direction"] == "LONG"
                else "🔴"
            )

            lines.append(
                f"{emoji} {signal['asset']} "
                f"{signal['direction']} | "
                f"Entry {fmt_price(signal['entry'])} | "
                f"SL {fmt_price(signal['sl'])} | "
                f"TP {fmt_price(signal['tp'])} | "
                f"RR {signal['rr']:.2f}"
            )

    lines.append(
        "━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # Open trades
    # --------------------------------------------------------

    lines.append(
        "📌 OPEN TRADES"
    )

    if not open_details:

        lines.append(
            "هیچ معامله بازی نداریم"
        )

    else:

        for item in open_details:

            trade = item["trade"]
            current = item["current"]

            pnl = calculate_pnl(
                trade["direction"],
                trade["entry"],
                current,
            )

            if trade["direction"] == "LONG":

                sl_distance = (
                    (current - trade["sl"])
                    / current
                    * 100
                    if current
                    else 0
                )

                tp_distance = (
                    (trade["tp"] - current)
                    / current
                    * 100
                    if current
                    else 0
                )

            else:

                sl_distance = (
                    (trade["sl"] - current)
                    / current
                    * 100
                    if current
                    else 0
                )

                tp_distance = (
                    (current - trade["tp"])
                    / current
                    * 100
                    if current
                    else 0
                )

            emoji = (
                "🟢"
                if trade["direction"] == "LONG"
                else "🔴"
            )

            lines.append(
                f"{emoji} {trade['asset']} "
                f"{trade['direction']}"
            )

            lines.append(
                f"Entry: {fmt_price(trade['entry'])} | "
                f"Current: {fmt_price(current)}"
            )

            lines.append(
                f"P/L: {fmt_pct(pnl)} | "
                f"SL: {sl_distance:.2f}% | "
                f"TP: {tp_distance:.2f}%"
            )

    lines.append(
        "━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # Performance
    # --------------------------------------------------------

    lines.append(
        "📈 PERFORMANCE"
    )

    lines.append(
        f"Trades: {stats['total']} | "
        f"Wins: {stats['wins']} | "
        f"Losses: {stats['losses']}"
    )

    lines.append(
        f"Win Rate: {stats['win_rate']:.2f}%"
    )

    lines.append(
        f"Total PnL: {fmt_pct(stats['total_pnl'])}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📌 PAPER ONLY"
    )

    return "\n".join(lines)


# ============================================================
# PROCESS ONE ASSET
# ============================================================

def process_asset(asset):
    symbol = SYMBOL_MAP[asset]

    result = {
        "asset": asset,
        "symbol": symbol,
        "trend_1h": "NEUTRAL",
        "signal": None,
        "new_trade": False,
        "candles_5m": None,
        "ichi_5m": None,
    }

    # --------------------------------------------------------
    # 1H
    # --------------------------------------------------------

    raw_1h = fetch_candles(
        symbol,
        TF_1H,
        COUNT_1H,
    )

    closed_1h = get_closed_candles(
        raw_1h,
        TF_1H,
    )

    if len(closed_1h) < 100:
        return result

    ichi_1h = calculate_ichimoku(
        closed_1h
    )

    i1 = len(closed_1h) - 1

    trend = ichimoku_state(
        ichi_1h,
        i1,
    )

    result["trend_1h"] = trend

    # --------------------------------------------------------
    # 5M
    # --------------------------------------------------------

    raw_5m = fetch_candles(
        symbol,
        TF_5M,
        COUNT_5M,
    )

    closed_5m = get_closed_candles(
        raw_5m,
        TF_5M,
    )

    if len(closed_5m) < 100:
        return result

    ichi_5m = calculate_ichimoku(
        closed_5m
    )

    result["candles_5m"] = closed_5m
    result["ichi_5m"] = ichi_5m
    result["raw_5m"] = raw_5m

    signal = detect_signal(
        closed_1h,
        ichi_1h,
        closed_5m,
        ichi_5m,
    )

    if signal is None:
        return result

    direction = signal["direction"]

    # --------------------------------------------------------
    # One open trade per asset + direction
    # --------------------------------------------------------

    if has_open_trade(
        symbol,
        direction,
    ):
        return result

    # --------------------------------------------------------
    # One signal per candle
    # --------------------------------------------------------

    if signal_already_exists(
        symbol,
        direction,
        signal["signal_time"],
    ):
        return result

    inserted = insert_trade(
        asset,
        symbol,
        signal,
    )

    if inserted:

        signal["asset"] = asset
        signal["symbol"] = symbol

        result["signal"] = signal
        result["new_trade"] = True

    return result


# ============================================================
# PROCESS OPEN TRADES
# ============================================================

def process_open_trades(cache):
    closed_events = []

    trades = get_open_trades()

    for trade in trades:

        symbol = trade["symbol"]

        data = cache.get(symbol)

        if not data:
            continue

        candles = data.get("raw_5m")

        if not candles:
            continue

        closed_candles = get_closed_candles(
            candles,
            TF_5M,
        )

        if len(closed_candles) < 100:
            continue

        ichi = calculate_ichimoku(
            closed_candles
        )

        decision = evaluate_open_trade(
            trade,
            candles,
            ichi,
        )

        if not decision:
            continue

        reason = decision["reason"]

        if reason is None:
            continue

        closed = close_trade(
            trade["id"],
            decision["price"],
            int(time.time() * 1000),
            reason,
        )

        if closed:
            closed_events.append(
                closed
            )

    return closed_events


# ============================================================
# NEW SIGNAL TELEGRAM
# ============================================================

def send_new_signal(
    signal,
    candles,
    ichi,
):
    direction = signal["direction"]

    if direction == "LONG":
        emoji = "🟢"
    else:
        emoji = "🔴"

    signal_dt = datetime.fromtimestamp(
        signal["signal_time"] / 1000,
        tz=timezone.utc,
    )

    caption = (
        f"{emoji} NEW {direction}\n\n"
        f"📌 {signal['asset']}\n"
        f"Entry: {fmt_price(signal['entry'])}\n"
        f"SL: {fmt_price(signal['sl'])} "
        f"({signal['sl_source']})\n"
        f"TP: {fmt_price(signal['tp'])} "
        f"({signal['tp_source']})\n"
        f"RR: {signal['rr']:.2f}\n\n"
        f"1H Trend: {signal['trend_1h']}\n"
        f"5M Kumo: {signal['state_5m']}\n"
        f"Time: {signal_dt.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"📌 PAPER ONLY"
    )

    filename = (
        f"/tmp/"
        f"{signal['asset']}_"
        f"{direction}_"
        f"{signal['signal_time']}.png"
    )

    created = create_ichimoku_chart(
        candles,
        ichi,
        signal,
        signal["symbol"],
        filename,
    )

    if created:

        sent = send_telegram_photo(
            filename,
            caption,
        )

        try:
            os.remove(filename)
        except Exception:
            pass

        if sent:
            return True

    # Fallback if chart generation/send fails
    return send_telegram_message(
        caption
    )


# ============================================================
# CLOSE EVENT TELEGRAM
# ============================================================

def send_close_event(event):
    if event["pnl"] >= 0:
        emoji = "✅"
    else:
        emoji = "❌"

    text = (
        f"{emoji} TRADE CLOSED\n\n"
        f"{event['asset']} "
        f"{event['direction']}\n"
        f"Reason: {event['reason']}\n"
        f"Entry: {fmt_price(event['entry'])}\n"
        f"Exit: {fmt_price(event['exit'])}\n"
        f"PnL: {fmt_pct(event['pnl'])}\n\n"
        f"📌 PAPER ONLY"
    )

    send_telegram_message(
        text
    )


# ============================================================
# MAIN SCAN
# ============================================================

def run_scan():
    print()
    print("=" * 70)
    print(
        f"KRAKEN ICHIMOKU 40 | {VERSION}"
    )
    print(
        f"UTC: {utc_iso()}"
    )
    print("=" * 70)

    init_db()

    cache = {}

    trend_counts = {
        "LONG": 0,
        "SHORT": 0,
        "NEUTRAL": 0,
    }

    new_signals = []

    # --------------------------------------------------------
    # Scan 40 assets
    # --------------------------------------------------------

    for asset in ASSETS:

        try:

            print(
                f"[SCAN] {asset}"
            )

            result = process_asset(
                asset
            )

            trend = result[
                "trend_1h"
            ]

            if trend not in trend_counts:
                trend = "NEUTRAL"

            trend_counts[trend] += 1

            symbol = result["symbol"]

            cache[symbol] = result

            if result["new_trade"]:

                signal = result["signal"]

                new_signals.append(
                    signal
                )

                print(
                    f"[NEW] "
                    f"{asset} "
                    f"{signal['direction']} "
                    f"Entry={fmt_price(signal['entry'])} "
                    f"SL={fmt_price(signal['sl'])} "
                    f"TP={fmt_price(signal['tp'])} "
                    f"RR={signal['rr']:.2f}"
                )

                send_new_signal(
                    signal,
                    result["candles_5m"],
                    result["ichi_5m"],
                )

        except Exception as exc:

            print(
                f"[ERROR] {asset}: {exc}"
            )

            traceback.print_exc()

    # --------------------------------------------------------
    # Open trade exits
    # --------------------------------------------------------

    closed_events = process_open_trades(
        cache
    )

    for event in closed_events:
        send_close_event(
            event
        )

    # --------------------------------------------------------
    # Open trades report data
    # --------------------------------------------------------

    open_trades = get_open_trades()

    open_details = []

    for trade in open_trades:

        data = cache.get(
            trade["symbol"]
        )

        if not data:
            continue

        raw_5m = data.get(
            "raw_5m"
        )

        if not raw_5m:
            continue

        current = raw_5m[-1]["close"]

        open_details.append(
            {
                "trade": trade,
                "current": current,
            }
        )

    return {
        "trend_counts": trend_counts,
        "new_signals": new_signals,
        "open_details": open_details,
        "closed_events": closed_events,
    }


# ============================================================
# LOOP
# ============================================================

def main():
    if REAL_TRADING:
        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    if not PAPER_TRADING:
        raise RuntimeError(
            "PAPER_TRADING must remain True."
        )

    init_db()

    print(
        "============================================================"
    )

    print(
        f"KRAKEN ICHIMOKU 40 LIVE SCANNER "
        f"v{VERSION}"
    )

    print(
        "REAL_TRADING = False"
    )

    print(
        "PAPER_TRADING = True"
    )

    print(
        "============================================================"
    )

    last_report_time = 0

    while True:

        cycle_start = time.time()

        try:

            result = run_scan()

            current_time = time.time()

            # ------------------------------------------------
            # Telegram periodic report
            # ------------------------------------------------

            if (
                current_time
                - last_report_time
                >= REPORT_INTERVAL_SECONDS
            ):

                report = build_report(
                    result["trend_counts"],
                    result["new_signals"],
                    result["open_details"],
                )

                send_telegram_message(
                    report
                )

                last_report_time = current_time

        except Exception as exc:

            print(
                f"[FATAL SCAN ERROR] {exc}"
            )

            traceback.print_exc()

            try:

                send_telegram_message(
                    "⚠️ KRAKEN ICHIMOKU SCANNER ERROR\n\n"
                    f"{type(exc).__name__}: {exc}\n\n"
                    "📌 PAPER ONLY"
                )

            except Exception:
                pass

        elapsed = time.time() - cycle_start

        sleep_for = max(
            5,
            SCAN_INTERVAL_SECONDS - elapsed,
        )

        print(
            f"[WAIT] {sleep_for:.1f}s"
        )

        time.sleep(
            sleep_for
        )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    main()
