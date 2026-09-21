# ============================================================
# KRAKEN FUTURES TRENDLINE LIVE SIGNAL SCANNER
# VERSION 7.0.4
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
# 5M:
#   Valid pivots
#        ↓
#   Valid trendline
#        ↓
#   5M trendline breakout
#        ↓
#   RVOL confirmation
#        ↓
#   Entry
#
# TP:
#   1H confirmed swing ahead of entry
#   fallback -> 5M confirmed swing ahead of entry
#
# SL:
#   Latest confirmed opposite 5M swing
#   + safety buffer
#
# IMPORTANT:
# - Closed candles only
# - No lookahead
# - Existing DB preserved
# - Legacy DB columns migrated safely
# - LONG and SHORT can coexist on same asset
# - REAL_TRADING is permanently disabled
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

VERSION = "7.0.4"

REAL_TRADING = False
PAPER_TRADING = True

DB_FILE = "kraken_pattern_live_v52.db"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

KRAKEN_CHART_URL = "https://futures.kraken.com/api/charts/v1/trade"
KRAKEN_TICKER_URL = "https://futures.kraken.com/derivatives/api/v3/tickers"

TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))


# ============================================================
# TIMEFRAMES
# ============================================================

TF_1H = "1h"
TF_5M = "5m"


# ============================================================
# ASSETS
# ============================================================

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

CONTRACTS = {
    asset: f"PF_{asset}USD"
    for asset in ASSETS
}


# ============================================================
# STRATEGY PARAMETERS
# ============================================================

PIVOT_LEFT = 3
PIVOT_RIGHT = 3

MIN_PIVOT_SEPARATION_1H = 4
MIN_PIVOT_SEPARATION_5M = 5

MIN_SWING_PCT_1H = 0.0030
MIN_SWING_PCT_5M = 0.0015

MAX_TRENDLINE_PIVOTS = 12

TRENDLINE_TOUCH_TOLERANCE = 0.0035
TRENDLINE_BREAK_BUFFER = 0.0005

BREAKOUT_LOOKAHEAD_1H = 72
BREAKOUT_LOOKAHEAD_5M = 72

RVOL_LOOKBACK = 20
MIN_RVOL = 1.30

MIN_BREAK_BODY_PCT = 0.0008

# IMPORTANT:
# RR must be strictly greater than 1.
MIN_RR = 1.01

SL_BUFFER_PCT = 0.0015

MIN_SL_DISTANCE_PCT = 0.0015
MIN_TP_DISTANCE_PCT = 0.0020

MAX_SIGNALS_PER_SCAN = 1

MAX_HOLD_HOURS = 48

PERIODIC_REPORT_SECONDS = 900

CHART_CANDLES = 120


# ============================================================
# GLOBAL STATS
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
    }


stats = new_stats()


def reset_stats():
    global stats
    stats = new_stats()


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "KrakenTrendlineScanner/7.0.4",
        "Accept": "application/json",
    }
)


# ============================================================
# TIME / FORMAT HELPERS
# ============================================================

def now_utc_ts():
    return int(datetime.now(timezone.utc).timestamp())


def now_tehran():
    return datetime.now(timezone.utc).astimezone(TEHRAN_TZ)


def ts_to_tehran(ts):
    try:
        return datetime.fromtimestamp(
            float(ts),
            timezone.utc
        ).astimezone(TEHRAN_TZ)
    except Exception:
        return now_tehran()


def fmt_time(ts=None):
    if ts is None:
        dt = now_tehran()
    else:
        dt = ts_to_tehran(ts)

    return dt.strftime("%Y-%m-%d %H:%M")


def fmt_price(value):
    try:
        value = float(value)

        if value >= 1000:
            return f"{value:,.2f}"
        if value >= 100:
            return f"{value:,.3f}"
        if value >= 1:
            return f"{value:.4f}"
        if value >= 0.1:
            return f"{value:.5f}"
        if value >= 0.01:
            return f"{value:.6f}"

        return f"{value:.8f}"
    except Exception:
        return "-"


def fmt_pct(value):
    try:
        return f"{float(value):+.2f}%"
    except Exception:
        return "-"


def safe_float(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def safe_int(value, default=None):
    try:
        return int(value)
    except Exception:
        return default


def format_duration(seconds):
    try:
        seconds = max(0, int(seconds))

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

    except Exception:
        return "-"


def row_value(row, key, default=None):
    try:
        if row is None:
            return default

        value = row[key]

        if value is None:
            return default

        return value

    except Exception:
        return default


# ============================================================
# TELEGRAM
# ============================================================

def telegram_configured():
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def send_telegram(text, image_path=None):
    if not telegram_configured():
        return False

    try:
        if image_path and os.path.exists(image_path):

            url = (
                f"https://api.telegram.org/bot"
                f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
            )

            with open(image_path, "rb") as photo:

                response = SESSION.post(
                    url,
                    data={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "caption": text,
                        "parse_mode": "HTML",
                    },
                    files={
                        "photo": photo
                    },
                    timeout=30,
                )

        else:

            url = (
                f"https://api.telegram.org/bot"
                f"{TELEGRAM_BOT_TOKEN}/sendMessage"
            )

            response = SESSION.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=30,
            )

        return response.ok

    except Exception as e:
        print("Telegram error:", e)
        return False


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn, table_name):
    try:
        rows = conn.execute(
            f"PRAGMA table_info({table_name})"
        ).fetchall()

        return {
            row["name"]
            for row in rows
        }

    except Exception:
        return set()


def ensure_column(conn, table_name, column_name, definition):
    columns = table_columns(conn, table_name)

    if column_name not in columns:

        conn.execute(
            f"""
            ALTER TABLE {table_name}
            ADD COLUMN {column_name} {definition}
            """
        )

        conn.commit()


def init_db():

    conn = db_connect()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_key TEXT UNIQUE,
            asset TEXT,
            contract TEXT,
            direction TEXT,
            strategy TEXT,
            pattern TEXT,
            timeframe TEXT,
            entry REAL,
            tp REAL,
            sl REAL,
            rr REAL,
            rvol REAL,
            signal_time INTEGER,
            created_at INTEGER,
            chart_path TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_key TEXT UNIQUE,
            asset TEXT,
            contract TEXT,
            direction TEXT,
            strategy TEXT,
            entry REAL,
            tp REAL,
            sl REAL,
            rr REAL,
            entry_time INTEGER,
            exit_time INTEGER,
            exit_price REAL,
            exit_reason TEXT,
            pnl_pct REAL,
            status TEXT DEFAULT 'OPEN'
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scanner_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

    # --------------------------------------------------------
    # Legacy migrations
    # --------------------------------------------------------

    signal_columns = {
        "signal_key": "TEXT",
        "asset": "TEXT",
        "contract": "TEXT",
        "direction": "TEXT",
        "strategy": "TEXT",
        "pattern": "TEXT",
        "timeframe": "TEXT",
        "entry": "REAL",
        "tp": "REAL",
        "sl": "REAL",
        "rr": "REAL",
        "rvol": "REAL",
        "signal_time": "INTEGER",
        "created_at": "INTEGER",
        "chart_path": "TEXT",
    }

    for column, definition in signal_columns.items():
        ensure_column(
            conn,
            "signals",
            column,
            definition
        )

    trade_columns = {
        "signal_key": "TEXT",
        "asset": "TEXT",
        "contract": "TEXT",
        "direction": "TEXT",
        "strategy": "TEXT",
        "entry": "REAL",
        "tp": "REAL",
        "sl": "REAL",
        "rr": "REAL",
        "entry_time": "INTEGER",
        "exit_time": "INTEGER",
        "exit_price": "REAL",
        "exit_reason": "TEXT",
        "pnl_pct": "REAL",
        "status": "TEXT DEFAULT 'OPEN'",
    }

    for column, definition in trade_columns.items():
        ensure_column(
            conn,
            "trades",
            column,
            definition
        )

    # --------------------------------------------------------
    # Legacy NULL normalization
    # --------------------------------------------------------

    conn.execute(
        """
        UPDATE trades
        SET status = 'OPEN'
        WHERE status IS NULL
        """
    )

    conn.commit()
    conn.close()


def get_open_trades():
    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY entry_time ASC
        """
    ).fetchall()

    conn.close()

    return rows


# ============================================================
# MARKET DATA
# ============================================================

def fetch_klines(contract, resolution, limit=500):

    url = (
        f"{KRAKEN_CHART_URL}/"
        f"{contract}/"
        f"{resolution}"
    )

    try:

        response = SESSION.get(
            url,
            params={
                "limit": limit
            },
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        candles = []

        raw = data.get("candles")

        if raw is None:
            raw = data.get("data")

        if raw is None:
            return []

        for item in raw:

            try:

                if isinstance(item, dict):

                    ts = (
                        item.get("time")
                        or item.get("timestamp")
                        or item.get("ts")
                    )

                    open_price = (
                        item.get("open")
                        or item.get("o")
                    )

                    high = (
                        item.get("high")
                        or item.get("h")
                    )

                    low = (
                        item.get("low")
                        or item.get("l")
                    )

                    close = (
                        item.get("close")
                        or item.get("c")
                    )

                    volume = (
                        item.get("volume")
                        or item.get("v")
                        or 0
                    )

                else:

                    if len(item) < 6:
                        continue

                    ts = item[0]
                    open_price = item[1]
                    high = item[2]
                    low = item[3]
                    close = item[4]
                    volume = item[5]

                ts = safe_float(ts)

                if ts is None:
                    continue

                # milliseconds -> seconds
                if ts > 10_000_000_000:
                    ts /= 1000.0

                candle = {
                    "time": int(ts),
                    "open": safe_float(open_price),
                    "high": safe_float(high),
                    "low": safe_float(low),
                    "close": safe_float(close),
                    "volume": safe_float(volume, 0.0),
                }

                if None in (
                    candle["open"],
                    candle["high"],
                    candle["low"],
                    candle["close"],
                ):
                    continue

                candles.append(candle)

            except Exception:
                continue

        candles.sort(
            key=lambda x: x["time"]
        )

        # ----------------------------------------------------
        # Remove currently-forming candle
        # ----------------------------------------------------

        if candles:

            resolution_seconds = {
                "1m": 60,
                "5m": 300,
                "15m": 900,
                "1h": 3600,
                "4h": 14400,
            }.get(resolution)

            if resolution_seconds:

                current_bucket = (
                    now_utc_ts() // resolution_seconds
                ) * resolution_seconds

                candles = [
                    c
                    for c in candles
                    if c["time"] < current_bucket
                ]

        return candles

    except Exception as e:

        print(
            f"[DATA ERROR] {contract} {resolution}: {e}"
        )

        return []


def fetch_tickers():

    try:

        response = SESSION.get(
            KRAKEN_TICKER_URL,
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        result = {}

        tickers = data.get("tickers", [])

        for ticker in tickers:

            symbol = (
                ticker.get("symbol")
                or ticker.get("tag")
                or ticker.get("instrument")
            )

            if not symbol:
                continue

            price = (
                ticker.get("last")
                or ticker.get("lastPrice")
                or ticker.get("price")
            )

            price = safe_float(price)

            if price is not None:
                result[symbol] = price

        return result

    except Exception as e:

        print("[TICKER ERROR]", e)
        return {}


def get_current_price(contract, tickers=None):

    if tickers is None:
        tickers = fetch_tickers()

    return safe_float(
        tickers.get(contract)
    )


# ============================================================
# PIVOTS
# ============================================================

def detect_pivots(
    candles,
    left=PIVOT_LEFT,
    right=PIVOT_RIGHT,
    min_separation=5,
    min_swing_pct=0.0015,
):

    highs = []
    lows = []

    n = len(candles)

    if n < left + right + 3:
        return highs, lows

    last_high_index = None
    last_low_index = None

    for i in range(left, n - right):

        current_high = candles[i]["high"]
        current_low = candles[i]["low"]

        is_high = True
        is_low = True

        for j in range(i - left, i + right + 1):

            if j == i:
                continue

            if candles[j]["high"] >= current_high:
                is_high = False

            if candles[j]["low"] <= current_low:
                is_low = False

        # ----------------------------------------------------
        # HIGH PIVOT
        # ----------------------------------------------------

        if is_high:

            if (
                last_high_index is not None
                and i - last_high_index < min_separation
            ):
                is_high = False

            if is_high and highs:

                previous_price = highs[-1]["price"]

                swing_pct = (
                    abs(current_high - previous_price)
                    / previous_price
                )

                if swing_pct < min_swing_pct:
                    is_high = False

            if is_high:

                pivot = {
                    "index": i,
                    "time": candles[i]["time"],
                    "price": current_high,
                    "type": "HIGH",
                }

                highs.append(pivot)
                last_high_index = i

        # ----------------------------------------------------
        # LOW PIVOT
        # ----------------------------------------------------

        if is_low:

            if (
                last_low_index is not None
                and i - last_low_index < min_separation
            ):
                is_low = False

            if is_low and lows:

                previous_price = lows[-1]["price"]

                swing_pct = (
                    abs(current_low - previous_price)
                    / previous_price
                )

                if swing_pct < min_swing_pct:
                    is_low = False

            if is_low:

                pivot = {
                    "index": i,
                    "time": candles[i]["time"],
                    "price": current_low,
                    "type": "LOW",
                }

                lows.append(pivot)
                last_low_index = i

    return highs, lows


# ============================================================
# TRENDLINE
# ============================================================

def line_value(p1, p2, x):

    if p2["index"] == p1["index"]:
        return p1["price"]

    slope = (
        p2["price"] - p1["price"]
    ) / (
        p2["index"] - p1["index"]
    )

    return p1["price"] + (
        slope * (x - p1["index"])
    )


def trendline_slope_pct(p1, p2):

    if p1["price"] == 0:
        return 0.0

    return (
        (p2["price"] - p1["price"])
        / p1["price"]
    )


def trendline_is_valid(
    candles,
    p1,
    p2,
    pivot_type,
):

    if p2["index"] <= p1["index"]:
        return False

    # Direction:
    # HIGH trendline = descending resistance
    # LOW trendline = ascending support

    slope = trendline_slope_pct(
        p1,
        p2
    )

    if pivot_type == "HIGH":

        if slope >= 0:
            return False

    elif pivot_type == "LOW":

        if slope <= 0:
            return False

    else:
        return False

    # --------------------------------------------------------
    # Validate candles between pivots
    # --------------------------------------------------------

    start = p1["index"]
    end = p2["index"]

    for i in range(start + 1, end):

        value = line_value(
            p1,
            p2,
            i
        )

        candle = candles[i]

        tolerance = (
            abs(value)
            * TRENDLINE_TOUCH_TOLERANCE
        )

        if pivot_type == "HIGH":

            # A resistance line should not be significantly
            # below candle highs.

            if candle["high"] > value + tolerance:
                return False

        else:

            # A support line should not be significantly
            # above candle lows.

            if candle["low"] < value - tolerance:
                return False

    return True


def find_valid_trendlines(
    candles,
    highs,
    lows,
):

    trendlines = []

    # --------------------------------------------------------
    # HIGH / RESISTANCE TRENDLINES
    # --------------------------------------------------------

    high_points = highs[
        -MAX_TRENDLINE_PIVOTS:
    ]

    for a in range(len(high_points)):

        for b in range(
            a + 1,
            len(high_points)
        ):

            p1 = high_points[a]
            p2 = high_points[b]

            if not trendline_is_valid(
                candles,
                p1,
                p2,
                "HIGH",
            ):
                continue

            trendlines.append(
                {
                    "type": "HIGH",
                    "p1": p1,
                    "p2": p2,
                }
            )

    # --------------------------------------------------------
    # LOW / SUPPORT TRENDLINES
    # --------------------------------------------------------

    low_points = lows[
        -MAX_TRENDLINE_PIVOTS:
    ]

    for a in range(len(low_points)):

        for b in range(
            a + 1,
            len(low_points)
        ):

            p1 = low_points[a]
            p2 = low_points[b]

            if not trendline_is_valid(
                candles,
                p1,
                p2,
                "LOW",
            ):
                continue

            trendlines.append(
                {
                    "type": "LOW",
                    "p1": p1,
                    "p2": p2,
                }
            )

    return trendlines


def trendline_price(trendline, index):

    return line_value(
        trendline["p1"],
        trendline["p2"],
        index,
    )


# ============================================================
# BREAKOUT
# ============================================================

def candle_body_pct(candle):

    if candle["open"] == 0:
        return 0.0

    return abs(
        candle["close"] - candle["open"]
    ) / candle["open"]


def find_trendline_breakout(
    candles,
    trendlines,
    lookahead=72,
):

    if not candles or not trendlines:
        return None

    n = len(candles)

    scan_start = max(
        1,
        n - lookahead
    )

    candidates = []

    for trendline in trendlines:

        start_index = (
            trendline["p2"]["index"] + 1
        )

        start_index = max(
            start_index,
            scan_start
        )

        if start_index >= n:
            continue

        end_index = min(
            n,
            start_index + lookahead
        )

        for i in range(
            start_index,
            end_index
        ):

            candle = candles[i]
            previous = candles[i - 1]

            line_now = trendline_price(
                trendline,
                i
            )

            line_previous = trendline_price(
                trendline,
                i - 1
            )

            body_pct = candle_body_pct(
                candle
            )

            if body_pct < MIN_BREAK_BODY_PCT:
                continue

            # ------------------------------------------------
            # BREAK ABOVE RESISTANCE -> LONG
            # ------------------------------------------------

            if trendline["type"] == "HIGH":

                buffer = (
                    abs(line_now)
                    * TRENDLINE_BREAK_BUFFER
                )

                if (
                    previous["close"]
                    <= line_previous + buffer
                    and
                    candle["close"]
                    > line_now + buffer
                ):

                    candidates.append(
                        {
                            "index": i,
                            "time": candle["time"],
                            "direction": "LONG",
                            "trendline": trendline,
                        }
                    )

                    break

            # ------------------------------------------------
            # BREAK BELOW SUPPORT -> SHORT
            # ------------------------------------------------

            elif trendline["type"] == "LOW":

                buffer = (
                    abs(line_now)
                    * TRENDLINE_BREAK_BUFFER
                )

                if (
                    previous["close"]
                    >= line_previous - buffer
                    and
                    candle["close"]
                    < line_now - buffer
                ):

                    candidates.append(
                        {
                            "index": i,
                            "time": candle["time"],
                            "direction": "SHORT",
                            "trendline": trendline,
                        }
                    )

                    break

    if not candidates:
        return None

    # Most recent breakout
    candidates.sort(
        key=lambda x: x["index"],
        reverse=True
    )

    return candidates[0]


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(
    candles,
    index,
    lookback=20,
):

    if index < lookback:
        return 0.0

    current_volume = (
        candles[index]["volume"]
    )

    previous_volumes = [
        candles[i]["volume"]
        for i in range(
            index - lookback,
            index
        )
        if candles[i]["volume"] is not None
    ]

    if not previous_volumes:
        return 0.0

    average_volume = (
        sum(previous_volumes)
        / len(previous_volumes)
    )

    if average_volume <= 0:
        return 0.0

    return (
        current_volume
        / average_volume
    )


# ============================================================
# TP / SL
# ============================================================

def choose_levels(
    candles_5m,
    highs_5m,
    lows_5m,
    highs_1h,
    lows_1h,
    break_index,
    direction,
    entry,
):
    """
    NO LOOKAHEAD.

    TP:
      1) confirmed 1H swing already known before 5M breakout
      2) fallback to confirmed 5M swing already known

    SL:
      latest confirmed opposite 5M swing before breakout

    This fixes the previous situation where:
        Volume Accepted > 0
        Valid TP/SL = 0

    because the old code only searched pre-breakout 5M
    pivots for TP, which frequently had no level ahead of
    entry.
    """

    if not candles_5m:
        return None

    breakout_time = (
        candles_5m[break_index]["time"]
    )

    # --------------------------------------------------------
    # CONFIRMED 5M PIVOTS
    # --------------------------------------------------------

    confirmed_highs_5m = [
        p
        for p in highs_5m
        if p["index"] < break_index
    ]

    confirmed_lows_5m = [
        p
        for p in lows_5m
        if p["index"] < break_index
    ]

    # --------------------------------------------------------
    # CONFIRMED 1H PIVOTS
    #
    # Compare TIME, not index, because 1H and 5M indexes
    # belong to different candle arrays.
    # --------------------------------------------------------

    confirmed_highs_1h = [
        p
        for p in highs_1h
        if p["time"] < breakout_time
    ]

    confirmed_lows_1h = [
        p
        for p in lows_1h
        if p["time"] < breakout_time
    ]

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        # ----------------------------------------------------
        # SL = latest confirmed 5M LOW
        # ----------------------------------------------------

        if not confirmed_lows_5m:
            return None

        sl_pivot = confirmed_lows_5m[-1]

        raw_sl = sl_pivot["price"]

        sl = (
            raw_sl
            * (1.0 - SL_BUFFER_PCT)
        )

        # ----------------------------------------------------
        # TP candidates
        # ----------------------------------------------------

        tp_candidates = []

        # Prefer 1H confirmed highs
        for p in confirmed_highs_1h:

            if p["price"] > entry:
                tp_candidates.append(
                    {
                        "price": p["price"],
                        "time": p["time"],
                        "source": "1H",
                    }
                )

        # Fallback to 5M
        if not tp_candidates:

            for p in confirmed_highs_5m:

                if p["price"] > entry:
                    tp_candidates.append(
                        {
                            "price": p["price"],
                            "time": p["time"],
                            "source": "5M",
                        }
                    )

        if not tp_candidates:
            return None

        tp_candidates.sort(
            key=lambda x: x["price"]
        )

        tp_info = tp_candidates[0]

        tp = tp_info["price"]

        # ----------------------------------------------------
        # Validate
        # ----------------------------------------------------

        if sl >= entry:
            return None

        if tp <= entry:
            return None

        sl_distance = (
            entry - sl
        ) / entry

        tp_distance = (
            tp - entry
        ) / entry

        if sl_distance < MIN_SL_DISTANCE_PCT:
            return None

        if tp_distance < MIN_TP_DISTANCE_PCT:
            return None

        risk = entry - sl
        reward = tp - entry

        if risk <= 0:
            return None

        rr = reward / risk

        # STRICTLY > 1
        if rr <= 1.0:
            return None

        if rr < MIN_RR:
            return None

        return {
            "tp": tp,
            "sl": sl,
            "rr": rr,
            "tp_source": tp_info["source"],
            "sl_source": "5M",
            "tp_pivot_time": tp_info["time"],
            "sl_pivot_time": sl_pivot["time"],
        }

    # ========================================================
    # SHORT
    # ========================================================

    if direction == "SHORT":

        # ----------------------------------------------------
        # SL = latest confirmed 5M HIGH
        # ----------------------------------------------------

        if not confirmed_highs_5m:
            return None

        sl_pivot = confirmed_highs_5m[-1]

        raw_sl = sl_pivot["price"]

        sl = (
            raw_sl
            * (1.0 + SL_BUFFER_PCT)
        )

        # ----------------------------------------------------
        # TP candidates
        # ----------------------------------------------------

        tp_candidates = []

        # Prefer 1H confirmed lows
        for p in confirmed_lows_1h:

            if p["price"] < entry:
                tp_candidates.append(
                    {
                        "price": p["price"],
                        "time": p["time"],
                        "source": "1H",
                    }
                )

        # Fallback to 5M
        if not tp_candidates:

            for p in confirmed_lows_5m:

                if p["price"] < entry:
                    tp_candidates.append(
                        {
                            "price": p["price"],
                            "time": p["time"],
                            "source": "5M",
                        }
                    )

        if not tp_candidates:
            return None

        tp_candidates.sort(
            key=lambda x: x["price"],
            reverse=True
        )

        tp_info = tp_candidates[0]

        tp = tp_info["price"]

        # ----------------------------------------------------
        # Validate
        # ----------------------------------------------------

        if sl <= entry:
            return None

        if tp >= entry:
            return None

        sl_distance = (
            sl - entry
        ) / entry

        tp_distance = (
            entry - tp
        ) / entry

        if sl_distance < MIN_SL_DISTANCE_PCT:
            return None

        if tp_distance < MIN_TP_DISTANCE_PCT:
            return None

        risk = sl - entry
        reward = entry - tp

        if risk <= 0:
            return None

        rr = reward / risk

        # STRICTLY > 1
        if rr <= 1.0:
            return None

        if rr < MIN_RR:
            return None

        return {
            "tp": tp,
            "sl": sl,
            "rr": rr,
            "tp_source": tp_info["source"],
            "sl_source": "5M",
            "tp_pivot_time": tp_info["time"],
            "sl_pivot_time": sl_pivot["time"],
        }

    return None


# ============================================================
# SIGNAL KEY
# ============================================================

def make_signal_key(
    asset,
    direction,
    breakout_time,
):
    return (
        f"TL_{asset}_"
        f"{direction}_"
        f"{int(breakout_time)}"
    )


# ============================================================
# INSERT SIGNAL / TRADE
# ============================================================

def insert_signal_and_trade(
    asset,
    contract,
    direction,
    entry,
    tp,
    sl,
    rr,
    rvol,
    signal_time,
    chart_path=None,
):

    signal_key = make_signal_key(
        asset,
        direction,
        signal_time,
    )

    conn = db_connect()

    try:

        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO signals (
                signal_key,
                asset,
                contract,
                direction,
                strategy,
                pattern,
                timeframe,
                entry,
                tp,
                sl,
                rr,
                rvol,
                signal_time,
                created_at,
                chart_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_key,
                asset,
                contract,
                direction,
                "TRENDLINE",
                "TRENDLINE_BREAKOUT",
                "5M",
                entry,
                tp,
                sl,
                rr,
                rvol,
                signal_time,
                now_utc_ts(),
                chart_path,
            ),
        )

        inserted = (
            cursor.rowcount == 1
        )

        if not inserted:

            conn.rollback()
            return False, signal_key

        # ----------------------------------------------------
        # One open trade per asset + direction.
        # Opposite direction remains allowed.
        # ----------------------------------------------------

        existing = conn.execute(
            """
            SELECT id
            FROM trades
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

        if existing:

            conn.rollback()
            return False, signal_key

        conn.execute(
            """
            INSERT OR IGNORE INTO trades (
                signal_key,
                asset,
                contract,
                direction,
                strategy,
                entry,
                tp,
                sl,
                rr,
                entry_time,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
            """,
            (
                signal_key,
                asset,
                contract,
                direction,
                "TRENDLINE",
                entry,
                tp,
                sl,
                rr,
                signal_time,
            ),
        )

        conn.commit()

        return True, signal_key

    except Exception:

        conn.rollback()
        raise

    finally:

        conn.close()


# ============================================================
# TRADE PNL
# ============================================================

def calculate_trade_pnl(
    direction,
    entry,
    exit_price,
):

    if direction == "LONG":

        return (
            (exit_price - entry)
            / entry
        ) * 100.0

    if direction == "SHORT":

        return (
            (entry - exit_price)
            / entry
        ) * 100.0

    return 0.0


# ============================================================
# RECONCILE OPEN TRADES
# ============================================================

def reconcile_open_trades(
    tickers=None
):

    global stats

    if tickers is None:
        tickers = fetch_tickers()

    trades = get_open_trades()

    if not trades:
        return []

    closed = []

    conn = db_connect()

    try:

        for trade in trades:

            asset = row_value(
                trade,
                "asset"
            )

            contract = row_value(
                trade,
                "contract"
            )

            direction = row_value(
                trade,
                "direction"
            )

            entry = safe_float(
                row_value(
                    trade,
                    "entry"
                )
            )

            tp = safe_float(
                row_value(
                    trade,
                    "tp"
                )
            )

            sl = safe_float(
                row_value(
                    trade,
                    "sl"
                )
            )

            entry_time = safe_int(
                row_value(
                    trade,
                    "entry_time"
                )
            )

            if not contract and asset:
                contract = (
                    f"PF_{asset}USD"
                )

            if not all(
                x is not None
                for x in (
                    entry,
                    tp,
                    sl,
                    entry_time,
                )
            ):
                continue

            # ------------------------------------------------
            # Fetch fresh candles to accurately detect
            # TP / SL on closed candles.
            # ------------------------------------------------

            candles = fetch_klines(
                contract,
                "5m",
                20,
            )

            current_price = (
                get_current_price(
                    contract,
                    tickers
                )
            )

            exit_price = None
            exit_reason = None
            exit_time = now_utc_ts()

            # ------------------------------------------------
            # Check candles after entry
            # ------------------------------------------------

            for candle in candles:

                if candle["time"] <= entry_time:
                    continue

                if direction == "LONG":

                    hit_sl = (
                        candle["low"] <= sl
                    )

                    hit_tp = (
                        candle["high"] >= tp
                    )

                    # Same candle:
                    # SL first, as requested previously.
                    if hit_sl:

                        exit_price = sl
                        exit_reason = "SL"
                        exit_time = candle["time"]
                        break

                    if hit_tp:

                        exit_price = tp
                        exit_reason = "TP"
                        exit_time = candle["time"]
                        break

                elif direction == "SHORT":

                    hit_sl = (
                        candle["high"] >= sl
                    )

                    hit_tp = (
                        candle["low"] <= tp
                    )

                    if hit_sl:

                        exit_price = sl
                        exit_reason = "SL"
                        exit_time = candle["time"]
                        break

                    if hit_tp:

                        exit_price = tp
                        exit_reason = "TP"
                        exit_time = candle["time"]
                        break

            # ------------------------------------------------
            # Time exit
            # ------------------------------------------------

            if exit_price is None:

                if (
                    now_utc_ts()
                    - entry_time
                    >= MAX_HOLD_HOURS * 3600
                ):

                    if current_price is not None:

                        exit_price = current_price
                        exit_reason = "TIME"
                        exit_time = now_utc_ts()

            if exit_price is None:
                continue

            pnl = calculate_trade_pnl(
                direction,
                entry,
                exit_price,
            )

            conn.execute(
                """
                UPDATE trades
                SET
                    exit_time = ?,
                    exit_price = ?,
                    exit_reason = ?,
                    pnl_pct = ?,
                    status = 'CLOSED'
                WHERE id = ?
                """,
                (
                    exit_time,
                    exit_price,
                    exit_reason,
                    pnl,
                    trade["id"],
                ),
            )

            closed.append(
                {
                    "asset": asset,
                    "direction": direction,
                    "entry": entry,
                    "exit_price": exit_price,
                    "tp": tp,
                    "sl": sl,
                    "reason": exit_reason,
                    "pnl_pct": pnl,
                    "entry_time": entry_time,
                    "exit_time": exit_time,
                }
            )

        conn.commit()

    finally:

        conn.close()

    stats["closed_trades"] += len(closed)

    return closed


# ============================================================
# OPEN TRADE TELEGRAM
# ============================================================

def send_open_trades():

    trades = get_open_trades()

    if not trades:
        return

    lines = [
        "📂 <b>OPEN TRADES</b>",
        "",
    ]

    tickers = fetch_tickers()

    for trade in trades:

        asset = row_value(
            trade,
            "asset",
            "-"
        )

        direction = row_value(
            trade,
            "direction",
            "-"
        )

        contract = row_value(
            trade,
            "contract",
            f"PF_{asset}USD"
        )

        entry = safe_float(
            row_value(
                trade,
                "entry"
            )
        )

        tp = safe_float(
            row_value(
                trade,
                "tp"
            )
        )

        sl = safe_float(
            row_value(
                trade,
                "sl"
            )
        )

        entry_time = safe_int(
            row_value(
                trade,
                "entry_time"
            )
        )

        current = get_current_price(
            contract,
            tickers
        )

        if (
            current is not None
            and entry
        ):

            if direction == "LONG":

                current_pct = (
                    (current - entry)
                    / entry
                ) * 100

            else:

                current_pct = (
                    (entry - current)
                    / entry
                ) * 100

        else:

            current_pct = 0.0

        duration = format_duration(
            now_utc_ts()
            - entry_time
        ) if entry_time else "-"

        emoji = (
            "🟢"
            if direction == "LONG"
            else "🔴"
        )

        lines.append(
            f"{emoji} <b>{asset} {direction}</b>\n"
            f"Entry: {fmt_price(entry)}\n"
            f"TP: {fmt_price(tp)}\n"
            f"SL: {fmt_price(sl)}\n"
            f"Current: {fmt_price(current)} "
            f"({fmt_pct(current_pct)})\n"
            f"Duration: {duration}\n"
        )

    send_telegram(
        "\n".join(lines)
    )


# ============================================================
# PERFORMANCE
# ============================================================

def send_performance():

    conn = db_connect()

    try:

        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(
                    CASE
                        WHEN pnl_pct > 0
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
                ) AS total_pnl
            FROM trades
            WHERE status = 'CLOSED'
            """
        ).fetchone()

    finally:

        conn.close()

    total = safe_int(
        row["total"],
        0
    )

    wins = safe_int(
        row["wins"],
        0
    )

    losses = safe_int(
        row["losses"],
        0
    )

    total_pnl = safe_float(
        row["total_pnl"],
        0.0
    )

    if total > 0:

        win_rate = (
            wins / total
        ) * 100

    else:

        win_rate = 0.0

    text = (
        "📈 <b>PERFORMANCE</b>\n\n"
        f"Closed: {total}\n"
        f"Wins: {wins}\n"
        f"Losses: {losses}\n"
        f"Win Rate: {win_rate:.2f}%\n"
        f"Net PnL: {total_pnl:+.2f}%"
    )

    send_telegram(text)


# ============================================================
# CLOSE MESSAGE
# ============================================================

def send_close_message(trade):

    direction = trade["direction"]

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    duration = format_duration(
        trade["exit_time"]
        - trade["entry_time"]
    )

    text = (
        f"{emoji} <b>CLOSE {trade['asset']} "
        f"{direction}</b>\n\n"
        f"Entry: {fmt_price(trade['entry'])}\n"
        f"Exit: {fmt_price(trade['exit_price'])}\n"
        f"TP: {fmt_price(trade['tp'])}\n"
        f"SL: {fmt_price(trade['sl'])}\n"
        f"Result: {trade['reason']}\n"
        f"PnL: {fmt_pct(trade['pnl_pct'])}\n"
        f"Duration: {duration}\n"
        f"Exit Time: {fmt_time(trade['exit_time'])}"
    )

    send_telegram(text)


# ============================================================
# CHART
# ============================================================

def create_chart(
    asset,
    candles,
    breakout,
    entry,
    tp,
    sl,
):

    if not candles:
        return None

    try:

        display = candles[
            -CHART_CANDLES:
        ]

        offset = (
            len(candles)
            - len(display)
        )

        x = list(
            range(len(display))
        )

        opens = [
            c["open"]
            for c in display
        ]

        highs = [
            c["high"]
            for c in display
        ]

        lows = [
            c["low"]
            for c in display
        ]

        closes = [
            c["close"]
            for c in display
        ]

        fig, ax = plt.subplots(
            figsize=(14, 8)
        )

        # ----------------------------------------------------
        # Candles
        # ----------------------------------------------------

        for i, candle in enumerate(display):

            o = candle["open"]
            h = candle["high"]
            l = candle["low"]
            c = candle["close"]

            if c >= o:
                body_low = o
                body_high = c
            else:
                body_low = c
                body_high = o

            body_height = max(
                body_high - body_low,
                abs(o) * 0.00001
            )

            ax.plot(
                [i, i],
                [l, h],
                linewidth=0.8
            )

            ax.bar(
                i,
                body_height,
                bottom=body_low,
                width=0.65,
                alpha=0.7
            )

        # ----------------------------------------------------
        # Trendline
        # ----------------------------------------------------

        trendline = breakout["trendline"]

        p1 = trendline["p1"]
        p2 = trendline["p2"]

        start_global = p1["index"]
        end_global = breakout["index"]

        if end_global >= offset:

            line_x = []
            line_y = []

            for global_index in range(
                max(start_global, offset),
                min(
                    end_global + 1,
                    len(candles)
                )
            ):

                local_index = (
                    global_index - offset
                )

                line_x.append(
                    local_index
                )

                line_y.append(
                    trendline_price(
                        trendline,
                        global_index
                    )
                )

            if line_x:

                ax.plot(
                    line_x,
                    line_y,
                    linewidth=2.0,
                    label="Trendline"
                )

        # ----------------------------------------------------
        # P1 / P2
        # ----------------------------------------------------

        for label, pivot in (
            ("P1", p1),
            ("P2", p2),
        ):

            if pivot["index"] >= offset:

                px = (
                    pivot["index"]
                    - offset
                )

                ax.scatter(
                    [px],
                    [pivot["price"]],
                    s=70,
                    zorder=5
                )

                ax.annotate(
                    label,
                    (
                        px,
                        pivot["price"]
                    ),
                    xytext=(0, 10),
                    textcoords="offset points",
                    ha="center",
                    fontsize=9,
                )

        # ----------------------------------------------------
        # Breakout
        # ----------------------------------------------------

        if (
            breakout["index"]
            >= offset
        ):

            bx = (
                breakout["index"]
                - offset
            )

            by = candles[
                breakout["index"]
            ]["close"]

            ax.scatter(
                [bx],
                [by],
                marker="^"
                if breakout["direction"] == "LONG"
                else "v",
                s=120,
                zorder=6,
                label="Breakout"
            )

        # ----------------------------------------------------
        # Entry / TP / SL
        # ----------------------------------------------------

        ax.axhline(
            entry,
            linestyle="--",
            linewidth=1.2,
            label=f"Entry {fmt_price(entry)}"
        )

        ax.axhline(
            tp,
            linestyle="--",
            linewidth=1.2,
            label=f"TP {fmt_price(tp)}"
        )

        ax.axhline(
            sl,
            linestyle="--",
            linewidth=1.2,
            label=f"SL {fmt_price(sl)}"
        )

        ax.set_title(
            f"{asset} | 5M Trendline Breakout | "
            f"{breakout['direction']}"
        )

        ax.set_xlabel(
            "5M Candles"
        )

        ax.set_ylabel(
            "Price"
        )

        ax.grid(
            alpha=0.2
        )

        ax.legend(
            loc="best"
        )

        plt.tight_layout()

        filename = (
            f"chart_{asset}_"
            f"{breakout['direction']}_"
            f"{breakout['time']}.png"
        )

        path = os.path.join(
            "/tmp",
            filename
        )

        plt.savefig(
            path,
            dpi=150
        )

        plt.close(fig)

        return path

    except Exception as e:

        print(
            "[CHART ERROR]",
            asset,
            e
        )

        try:
            plt.close("all")
        except Exception:
            pass

        return None


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def send_signal_message(
    asset,
    direction,
    entry,
    tp,
    sl,
    rr,
    rvol,
    tp_source,
    sl_source,
    signal_time,
    chart_path=None,
):

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    text = (
        f"{emoji} <b>{direction} {asset}</b>\n\n"
        f"Strategy: Trendline Breakout\n"
        f"Entry: {fmt_price(entry)}\n"
        f"TP: {fmt_price(tp)}\n"
        f"SL: {fmt_price(sl)}\n"
        f"RR: {rr:.2f}\n"
        f"RVOL: {rvol:.2f}\n"
        f"TP Source: {tp_source}\n"
        f"SL Source: {sl_source}\n"
        f"Time: {fmt_time(signal_time)}"
    )

    send_telegram(
        text,
        chart_path
    )


# ============================================================
# SCAN ASSET
# ============================================================

def scan_asset(
    asset,
    tickers=None,
):

    contract = CONTRACTS[asset]

    # ========================================================
    # 1H DATA
    # ========================================================

    candles_1h = fetch_klines(
        contract,
        "1h",
        500
    )

    if len(candles_1h) < 100:
        return None

    highs_1h, lows_1h = detect_pivots(
        candles_1h,
        min_separation=MIN_PIVOT_SEPARATION_1H,
        min_swing_pct=MIN_SWING_PCT_1H,
    )

    trendlines_1h = find_valid_trendlines(
        candles_1h,
        highs_1h,
        lows_1h,
    )

    stats["trendlines_1h"] += len(
        trendlines_1h
    )

    if not trendlines_1h:
        return None

    # ========================================================
    # 1H BREAKOUT
    # ========================================================

    breakout_1h = find_trendline_breakout(
        candles_1h,
        trendlines_1h,
        BREAKOUT_LOOKAHEAD_1H,
    )

    if not breakout_1h:
        return None

    stats["breakouts_1h"] += 1

    # ========================================================
    # 5M DATA
    # ========================================================

    candles_5m = fetch_klines(
        contract,
        "5m",
        700
    )

    if len(candles_5m) < 150:
        return None

    highs_5m, lows_5m = detect_pivots(
        candles_5m,
        min_separation=MIN_PIVOT_SEPARATION_5M,
        min_swing_pct=MIN_SWING_PCT_5M,
    )

    trendlines_5m = find_valid_trendlines(
        candles_5m,
        highs_5m,
        lows_5m,
    )

    stats["trendlines_5m"] += len(
        trendlines_5m
    )

    if not trendlines_5m:
        return None

    # ========================================================
    # 5M BREAKOUT
    # ========================================================

    breakout_5m = find_trendline_breakout(
        candles_5m,
        trendlines_5m,
        BREAKOUT_LOOKAHEAD_5M,
    )

    if not breakout_5m:
        return None

    stats["breakouts_5m"] += 1

    break_index = breakout_5m["index"]

    direction = (
        breakout_5m["direction"]
    )

    entry = candles_5m[
        break_index
    ]["close"]

    # ========================================================
    # RVOL
    # ========================================================

    rvol = calculate_rvol(
        candles_5m,
        break_index,
        RVOL_LOOKBACK,
    )

    if rvol < MIN_RVOL:
        return None

    stats["volume_accepted"] += 1

    # ========================================================
    # TP / SL
    # ========================================================

    levels = choose_levels(
        candles_5m=candles_5m,
        highs_5m=highs_5m,
        lows_5m=lows_5m,
        highs_1h=highs_1h,
        lows_1h=lows_1h,
        break_index=break_index,
        direction=direction,
        entry=entry,
    )

    if not levels:

        stats["level_rejects"] += 1

        print(
            f"[LEVEL REJECT] "
            f"{asset} {direction} "
            f"entry={fmt_price(entry)}"
        )

        return None

    stats["valid_levels"] += 1

    tp = levels["tp"]
    sl = levels["sl"]
    rr = levels["rr"]

    # ========================================================
    # CHART
    # ========================================================

    chart_path = create_chart(
        asset=asset,
        candles=candles_5m,
        breakout=breakout_5m,
        entry=entry,
        tp=tp,
        sl=sl,
    )

    # ========================================================
    # INSERT
    # ========================================================

    signal_time = (
        candles_5m[
            break_index
        ]["time"]
    )

    inserted, signal_key = (
        insert_signal_and_trade(
            asset=asset,
            contract=contract,
            direction=direction,
            entry=entry,
            tp=tp,
            sl=sl,
            rr=rr,
            rvol=rvol,
            signal_time=signal_time,
            chart_path=chart_path,
        )
    )

    if not inserted:
        return None

    stats["new_signals"] += 1

    # ========================================================
    # TELEGRAM
    # ========================================================

    send_signal_message(
        asset=asset,
        direction=direction,
        entry=entry,
        tp=tp,
        sl=sl,
        rr=rr,
        rvol=rvol,
        tp_source=levels["tp_source"],
        sl_source=levels["sl_source"],
        signal_time=signal_time,
        chart_path=chart_path,
    )

    return {
        "asset": asset,
        "contract": contract,
        "direction": direction,
        "entry": entry,
        "tp": tp,
        "sl": sl,
        "rr": rr,
        "rvol": rvol,
        "signal_time": signal_time,
        "signal_key": signal_key,
    }


# ============================================================
# SUMMARY
# ============================================================

def send_summary():

    text = (
        "📊 <b>KRAKEN TRENDLINE SCANNER</b>\n"
        f"Version: {VERSION}\n"
        "Mode: PAPER ONLY\n"
        f"Assets: {stats['assets_scanned']}/{len(ASSETS)}\n\n"

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

        f"Time: {fmt_time()}"
    )

    send_telegram(
        text
    )


# ============================================================
# MAIN SCAN
# ============================================================

def scan():

    global stats

    # --------------------------------------------------------
    # IMPORTANT:
    # Every GitHub Actions execution starts with fresh stats.
    #
    # This DOES NOT delete or reset the SQLite database.
    # --------------------------------------------------------

    reset_stats()

    print(
        f"\n{'=' * 65}"
    )

    print(
        f"KRAKEN TRENDLINE SCANNER {VERSION}"
    )

    print(
        "MODE: PAPER ONLY"
    )

    print(
        f"{'=' * 65}\n"
    )

    # --------------------------------------------------------
    # DB is preserved
    # --------------------------------------------------------

    init_db()

    # --------------------------------------------------------
    # First reconcile old/open trades
    # --------------------------------------------------------

    tickers = fetch_tickers()

    closed_trades = reconcile_open_trades(
        tickers
    )

    for trade in closed_trades:

        send_close_message(
            trade
        )

    # --------------------------------------------------------
    # Scan assets
    # --------------------------------------------------------

    for asset in ASSETS:

        if (
            stats["new_signals"]
            >= MAX_SIGNALS_PER_SCAN
        ):
            break

        print(
            f"[SCAN] {asset}"
        )

        try:

            result = scan_asset(
                asset,
                tickers,
            )

            stats["assets_scanned"] += 1

            if result:

                print(
                    f"[SIGNAL] "
                    f"{asset} "
                    f"{result['direction']} "
                    f"Entry={fmt_price(result['entry'])} "
                    f"TP={fmt_price(result['tp'])} "
                    f"SL={fmt_price(result['sl'])} "
                    f"RR={result['rr']:.2f}"
                )

        except Exception as e:

            stats["assets_scanned"] += 1

            print(
                f"[ERROR] {asset}: {e}"
            )

            traceback.print_exc()

    # --------------------------------------------------------
    # Reports
    # --------------------------------------------------------

    print(
        "\n--- SCAN RESULT ---"
    )

    print(
        f"Assets: "
        f"{stats['assets_scanned']}/{len(ASSETS)}"
    )

    print(
        f"1H Trendlines: "
        f"{stats['trendlines_1h']}"
    )

    print(
        f"1H Breakouts: "
        f"{stats['breakouts_1h']}"
    )

    print(
        f"5M Trendlines: "
        f"{stats['trendlines_5m']}"
    )

    print(
        f"5M Breakouts: "
        f"{stats['breakouts_5m']}"
    )

    print(
        f"Volume Accepted: "
        f"{stats['volume_accepted']}"
    )

    print(
        f"Valid TP/SL: "
        f"{stats['valid_levels']}"
    )

    print(
        f"Level Rejections: "
        f"{stats['level_rejects']}"
    )

    print(
        f"New Signals: "
        f"{stats['new_signals']}"
    )

    print(
        f"Closed Trades: "
        f"{stats['closed_trades']}"
    )

    # --------------------------------------------------------
    # Summary only when no new signal
    # --------------------------------------------------------

    if stats["new_signals"] == 0:

        send_summary()

    # --------------------------------------------------------
    # Open trades + performance
    # --------------------------------------------------------

    send_open_trades()

    send_performance()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        scan()

    except KeyboardInterrupt:

        print(
            "\nStopped."
        )

        sys.exit(0)

    except Exception as e:

        print(
            "\nFATAL ERROR:",
            e
        )

        traceback.print_exc()

        try:

            send_telegram(
                "⚠️ <b>SCANNER ERROR</b>\n\n"
                f"{type(e).__name__}: {e}"
            )

        except Exception:
            pass

        sys.exit(1)
