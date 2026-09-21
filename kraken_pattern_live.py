# ============================================================
# KRAKEN FUTURES TRENDLINE LIVE SIGNAL SCANNER
# VERSION 7.0.2
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
#   Volume confirmation
#        ↓
#   Entry
#
# TP:
#   Nearest valid confirmed swing ahead of price
#
# SL:
#   Slightly beyond previous confirmed swing
#
# IMPORTANT:
# - Closed candles only
# - No lookahead
# - Existing DB preserved
# - Existing legacy trades migrated safely
# - REAL_TRADING permanently disabled
# ============================================================

import os
import sys
import time
import math
import sqlite3
import traceback
from datetime import datetime, timezone, timedelta

import requests

# matplotlib is only used for Telegram charts.
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception:
    matplotlib = None
    plt = None


# ============================================================
# CONFIG
# ============================================================

VERSION = "7.0.2"

REAL_TRADING = False

DB_FILE = "kraken_pattern_live_v52.db"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

KRAKEN_TICKER_URL = (
    "https://futures.kraken.com/derivatives/api/v3/tickers"
)

# IMPORTANT:
# Kraken Futures Charts API:
# https://futures.kraken.com/api/charts/v1/trade/{symbol}/{resolution}
KRAKEN_CHART_URL = (
    "https://futures.kraken.com/api/charts/v1/trade"
)

# ============================================================
# TIMEFRAMES
# ============================================================

TF_1H = "1h"
TF_5M = "5m"

CANDLES_1H = 240
CANDLES_5M = 300

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

MIN_RR = 1.50

SL_BUFFER_PCT = 0.0015

MIN_SL_DISTANCE_PCT = 0.0015

MIN_TP_DISTANCE_PCT = 0.0020

MAX_SIGNALS_PER_SCAN = 1

MAX_HOLD_HOURS = 48

PERIODIC_REPORT_SECONDS = 900

CHART_CANDLES = 120

HTTP_TIMEOUT = 20

TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))


# ============================================================
# GLOBAL STATS
# ============================================================

stats = {
    "assets": 0,
    "candles_1h": 0,
    "candles_5m": 0,
    "trendlines_1h": 0,
    "breaks_1h": 0,
    "trendlines_5m": 0,
    "breaks_5m": 0,
    "volume_ok": 0,
    "valid_levels": 0,
    "new_signals": 0,
    "closed_trades": 0,
}


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "Kraken-Trendline-Scanner/7.0.2"
        ),
        "Accept": "application/json",
    }
)


# ============================================================
# UTILS
# ============================================================

def now_utc_ts():
    return int(datetime.now(timezone.utc).timestamp())


def now_tehran():
    return datetime.now(TEHRAN_TZ)


def fmt_price(value):
    try:
        value = float(value)

        if value >= 1000:
            return f"{value:,.2f}"

        if value >= 100:
            return f"{value:,.3f}"

        if value >= 1:
            return f"{value:,.4f}"

        if value >= 0.1:
            return f"{value:,.5f}"

        if value >= 0.01:
            return f"{value:,.6f}"

        return f"{value:,.8f}"

    except Exception:
        return str(value)


def fmt_pct(value):
    try:
        return f"{float(value):+.2f}%"
    except Exception:
        return "0.00%"


def ts_to_tehran(ts):
    try:
        return datetime.fromtimestamp(
            int(ts),
            tz=timezone.utc
        ).astimezone(TEHRAN_TZ)
    except Exception:
        return datetime.now(TEHRAN_TZ)


def format_duration(seconds):
    try:
        seconds = max(0, int(seconds))
    except Exception:
        return "0m"

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60

    if hours > 0:
        return f"{hours}h {minutes}m"

    return f"{minutes}m"


def safe_float(value, default=None):
    try:
        if value is None:
            return default

        return float(value)

    except Exception:
        return default


def safe_int(value, default=None):
    try:
        if value is None:
            return default

        return int(float(value))

    except Exception:
        return default


def row_value(row, key, default=None):
    try:
        if key not in row.keys():
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
    return bool(
        TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    )


def send_telegram(text, photo_path=None):
    if not telegram_configured():
        return False

    try:
        if photo_path and os.path.exists(photo_path):

            url = (
                f"https://api.telegram.org/bot"
                f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
            )

            with open(photo_path, "rb") as photo:

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
                    timeout=HTTP_TIMEOUT,
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
                timeout=HTTP_TIMEOUT,
            )

        if response.ok:
            return True

        print(
            "[TELEGRAM]",
            response.status_code,
            response.text[:300]
        )

    except Exception as e:
        print("[TELEGRAM ERROR]", e)

    return False


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    return conn


def table_columns(conn, table):
    try:
        rows = conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()

        return {
            row["name"]
            for row in rows
        }

    except Exception:
        return set()


def ensure_column(
    conn,
    table,
    column,
    definition
):
    columns = table_columns(conn, table)

    if column in columns:
        return False

    print(
        f"[DB MIGRATION] Adding {table}.{column}"
    )

    conn.execute(
        f"ALTER TABLE {table} "
        f"ADD COLUMN {column} {definition}"
    )

    return True


def copy_legacy_column(
    conn,
    table,
    target,
    sources
):
    columns = table_columns(conn, table)

    if target not in columns:
        return

    source = None

    for candidate in sources:
        if candidate in columns:
            source = candidate
            break

    if source is None:
        return

    try:
        conn.execute(
            f"""
            UPDATE {table}
            SET {target} = {source}
            WHERE
                ({target} IS NULL OR
                 TRIM(CAST({target} AS TEXT)) = '')
                AND {source} IS NOT NULL
            """
        )

    except Exception as e:
        print(
            f"[DB MIGRATION WARNING] "
            f"{table}.{target} <- {source}: {e}"
        )


def normalize_legacy_trades(conn):
    columns = table_columns(
        conn,
        "trades"
    )

    # --------------------------------------------------------
    # Asset
    # --------------------------------------------------------

    copy_legacy_column(
        conn,
        "trades",
        "asset",
        [
            "symbol",
            "coin",
            "ticker",
            "market",
        ]
    )

    # --------------------------------------------------------
    # Direction
    # --------------------------------------------------------

    copy_legacy_column(
        conn,
        "trades",
        "direction",
        [
            "side",
            "position_side",
            "trade_direction",
        ]
    )

    if "direction" in columns:

        conn.execute(
            """
            UPDATE trades
            SET direction =
                CASE UPPER(TRIM(direction))
                    WHEN 'BUY' THEN 'LONG'
                    WHEN 'SELL' THEN 'SHORT'
                    WHEN 'LONG' THEN 'LONG'
                    WHEN 'SHORT' THEN 'SHORT'
                    ELSE direction
                END
            WHERE direction IS NOT NULL
            """
        )

    # --------------------------------------------------------
    # Entry
    # --------------------------------------------------------

    copy_legacy_column(
        conn,
        "trades",
        "entry",
        [
            "entry_price",
            "open_price",
            "openPrice",
            "price",
        ]
    )

    # --------------------------------------------------------
    # TP
    # --------------------------------------------------------

    copy_legacy_column(
        conn,
        "trades",
        "tp",
        [
            "tp_price",
            "take_profit",
            "takeprofit",
            "target",
            "target_price",
        ]
    )

    # --------------------------------------------------------
    # SL
    # --------------------------------------------------------

    copy_legacy_column(
        conn,
        "trades",
        "sl",
        [
            "sl_price",
            "stop_loss",
            "stoploss",
            "stop",
            "stop_price",
        ]
    )

    # --------------------------------------------------------
    # Current price
    # --------------------------------------------------------

    copy_legacy_column(
        conn,
        "trades",
        "current_price",
        [
            "current",
            "last_price",
            "mark_price",
        ]
    )

    # --------------------------------------------------------
    # RR
    # --------------------------------------------------------

    copy_legacy_column(
        conn,
        "trades",
        "rr",
        [
            "risk_reward",
            "risk_reward_ratio",
            "r_multiple",
        ]
    )

    # --------------------------------------------------------
    # Entry timestamp
    # --------------------------------------------------------

    copy_legacy_column(
        conn,
        "trades",
        "entry_timestamp",
        [
            "entry_time",
            "open_time",
            "opened_at",
            "created_at",
            "detected_at",
        ]
    )

    # --------------------------------------------------------
    # Exit timestamp
    # --------------------------------------------------------

    copy_legacy_column(
        conn,
        "trades",
        "exit_timestamp",
        [
            "exit_time",
            "closed_at",
        ]
    )

    # --------------------------------------------------------
    # Exit reason
    # --------------------------------------------------------

    copy_legacy_column(
        conn,
        "trades",
        "exit_reason",
        [
            "reason",
            "close_reason",
        ]
    )

    # --------------------------------------------------------
    # PNL
    # --------------------------------------------------------

    copy_legacy_column(
        conn,
        "trades",
        "pnl_pct",
        [
            "pnl",
            "profit_pct",
            "return_pct",
        ]
    )

    # --------------------------------------------------------
    # Status
    # --------------------------------------------------------

    copy_legacy_column(
        conn,
        "trades",
        "status",
        [
            "state",
            "trade_status",
        ]
    )

    if "status" in columns:

        if "exit_timestamp" in columns:

            conn.execute(
                """
                UPDATE trades
                SET status = 'OPEN'
                WHERE
                    (status IS NULL OR
                     TRIM(status) = '')
                    AND exit_timestamp IS NULL
                """
            )

            conn.execute(
                """
                UPDATE trades
                SET status = 'CLOSED'
                WHERE
                    (status IS NULL OR
                     TRIM(status) = '')
                    AND exit_timestamp IS NOT NULL
                """
            )

        conn.execute(
            """
            UPDATE trades
            SET status =
                CASE UPPER(TRIM(status))
                    WHEN 'OPEN' THEN 'OPEN'
                    WHEN 'CLOSED' THEN 'CLOSED'
                    ELSE status
                END
            WHERE status IS NOT NULL
            """
        )


def init_db():

    conn = db_connect()

    # --------------------------------------------------------
    # Signals
    # --------------------------------------------------------

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_key TEXT UNIQUE,
            asset TEXT,
            direction TEXT,
            pattern TEXT,
            entry REAL,
            tp REAL,
            sl REAL,
            rr REAL,
            rvol REAL,
            trendline_type_1h TEXT,
            trendline_type_5m TEXT,
            signal_timestamp INTEGER,
            created_at INTEGER
        )
        """
    )

    # --------------------------------------------------------
    # Trades
    # --------------------------------------------------------

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_key TEXT UNIQUE,
            asset TEXT,
            pattern TEXT,
            direction TEXT,
            entry REAL,
            current_price REAL,
            tp REAL,
            sl REAL,
            rr REAL,
            entry_timestamp INTEGER,
            exit_timestamp INTEGER,
            status TEXT,
            pnl_pct REAL,
            exit_reason TEXT,
            created_at INTEGER,
            updated_at INTEGER
        )
        """
    )

    # --------------------------------------------------------
    # Scanner metadata
    # --------------------------------------------------------

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scanner_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

    # --------------------------------------------------------
    # Robust migration
    # --------------------------------------------------------

    required_columns = {
        "asset": "TEXT",
        "symbol": "TEXT",
        "pattern": "TEXT",
        "direction": "TEXT",
        "pattern_start": "INTEGER",
        "pattern_end": "INTEGER",
        "breakout_time": "INTEGER",
        "retest_time": "INTEGER",
        "entry_time": "INTEGER",
        "entry_price": "REAL",
        "tp_price": "REAL",
        "sl_price": "REAL",
        "current_price": "REAL",
        "status": "TEXT",
        "result": "TEXT",
        "exit_time": "INTEGER",
        "exit_price": "REAL",
        "r_multiple": "REAL",
        "created_at": "INTEGER",
        "updated_at": "INTEGER",
        "detected_at": "INTEGER",
        "contract": "TEXT",
        "exit_reason": "TEXT",
        "notified_new": "INTEGER DEFAULT 0",
        "notified_close": "INTEGER DEFAULT 0",
        "confirm_time": "INTEGER",
        "sl": "REAL",
        "tp": "REAL",
        "pattern_time": "INTEGER",
        "confirmation_time": "INTEGER",
        "pnl_pct": "REAL",
        "rr": "REAL",
        "entry_timestamp": "INTEGER",
        "exit_timestamp": "INTEGER",
        "entry": "REAL",
    }

    for column, definition in required_columns.items():
        ensure_column(
            conn,
            "trades",
            column,
            definition
        )

    normalize_legacy_trades(conn)

    # --------------------------------------------------------
    # Signal migration
    # --------------------------------------------------------

    signal_columns = {
        "asset": "TEXT",
        "direction": "TEXT",
        "pattern": "TEXT",
        "entry": "REAL",
        "tp": "REAL",
        "sl": "REAL",
        "rr": "REAL",
        "rvol": "REAL",
        "trendline_type_1h": "TEXT",
        "trendline_type_5m": "TEXT",
        "signal_timestamp": "INTEGER",
        "created_at": "INTEGER",
    }

    for column, definition in signal_columns.items():
        ensure_column(
            conn,
            "signals",
            column,
            definition
        )

    conn.commit()

    print(
        "[DB] trades columns:",
        ", ".join(sorted(table_columns(conn, "trades")))
    )

    # --------------------------------------------------------
    # Report malformed old trades
    # --------------------------------------------------------

    try:

        rows = conn.execute(
            """
            SELECT
                id,
                asset,
                direction,
                entry,
                tp,
                sl,
                entry_timestamp,
                status
            FROM trades
            WHERE UPPER(COALESCE(status, 'OPEN')) = 'OPEN'
            ORDER BY id
            """
        ).fetchall()

        for row in rows:

            required = [
                "asset",
                "direction",
                "entry",
                "tp",
                "sl",
                "entry_timestamp",
            ]

            incomplete = False

            for field in required:

                value = row[field]

                if value is None:
                    incomplete = True
                    break

                if isinstance(value, str) and not value.strip():
                    incomplete = True
                    break

            if incomplete:

                print(
                    "[DB WARNING] "
                    f"Skipping incomplete legacy trade "
                    f"id={row['id']}"
                )

    except Exception as e:

        print(
            "[DB WARNING] "
            f"Legacy trade inspection failed: {e}"
        )

    conn.close()


def get_open_trades():

    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE UPPER(COALESCE(status, 'OPEN')) = 'OPEN'
        ORDER BY
            COALESCE(entry_timestamp, created_at, 0) ASC
        """
    ).fetchall()

    conn.close()

    return rows


# ============================================================
# KRAKEN MARKET DATA
# ============================================================

def fetch_klines(
    contract,
    resolution,
    count
):
    """
    Kraken Futures Charts API.

    Correct format:
        /api/charts/v1/trade/PF_XBTUSD/1h
        /api/charts/v1/trade/PF_XBTUSD/5m

    NOT:
        /api/charts/v1/trade/PF_XBTUSD/60
    """

    url = (
        f"{KRAKEN_CHART_URL}/"
        f"{contract}/"
        f"{resolution}"
    )

    params = {
        "count": int(count)
    }

    try:

        response = SESSION.get(
            url,
            params=params,
            timeout=HTTP_TIMEOUT
        )

        if response.status_code != 200:

            print(
                f"[KRAKEN] "
                f"{contract} {resolution} "
                f"HTTP {response.status_code}"
            )

            try:
                print(
                    "[KRAKEN BODY]",
                    response.text[:250]
                )
            except Exception:
                pass

            return []

        data = response.json()

        rows = []

        if isinstance(data, dict):

            if isinstance(data.get("candles"), list):
                rows = data["candles"]

            elif isinstance(data.get("data"), list):
                rows = data["data"]

            elif isinstance(data.get("result"), list):
                rows = data["result"]

            elif isinstance(
                data.get("result"),
                dict
            ):

                result = data["result"]

                if isinstance(
                    result.get("candles"),
                    list
                ):
                    rows = result["candles"]

        elif isinstance(data, list):

            rows = data

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

                    v = (
                        row.get("volume")
                        or row.get("v")
                        or 0
                    )

                elif isinstance(row, (list, tuple)):

                    if len(row) < 6:
                        continue

                    ts = row[0]
                    o = row[1]
                    h = row[2]
                    l = row[3]
                    c = row[4]
                    v = row[5]

                else:
                    continue

                ts = int(float(ts))

                # Kraken returns milliseconds.
                if ts > 10_000_000_000:
                    ts = ts // 1000

                candle = {
                    "time": ts,
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": float(v),
                }

                candles.append(candle)

            except Exception:
                continue

        candles.sort(
            key=lambda x: x["time"]
        )

        # ----------------------------------------------------
        # Remove duplicate timestamps
        # ----------------------------------------------------

        unique = {}

        for candle in candles:
            unique[candle["time"]] = candle

        candles = list(unique.values())

        candles.sort(
            key=lambda x: x["time"]
        )

        # ----------------------------------------------------
        # Remove currently forming candle
        # ----------------------------------------------------

        if candles:

            if resolution == "1h":
                interval = 3600

            elif resolution == "5m":
                interval = 300

            else:
                interval = 60

            current_bucket = (
                now_utc_ts() // interval
            ) * interval

            candles = [
                candle
                for candle in candles
                if candle["time"] < current_bucket
            ]

        if not candles:

            print(
                f"[KRAKEN] "
                f"{contract} {resolution}: "
                f"0 closed candles"
            )

            return []

        print(
            f"[KRAKEN] "
            f"{contract} {resolution}: "
            f"{len(candles)} candles"
        )

        return candles[-count:]

    except Exception as e:

        print(
            f"[KRAKEN ERROR] "
            f"{contract} {resolution}: {e}"
        )

        return []


def fetch_tickers():

    try:

        response = SESSION.get(
            KRAKEN_TICKER_URL,
            timeout=HTTP_TIMEOUT
        )

        if response.status_code != 200:

            print(
                "[KRAKEN TICKERS]",
                response.status_code
            )

            return {}

        data = response.json()

        rows = []

        if isinstance(data, dict):

            if isinstance(
                data.get("tickers"),
                list
            ):
                rows = data["tickers"]

            elif isinstance(
                data.get("data"),
                list
            ):
                rows = data["data"]

            elif isinstance(
                data.get("result"),
                list
            ):
                rows = data["result"]

            elif isinstance(
                data.get("result"),
                dict
            ):
                rows = [
                    dict(
                        value,
                        symbol=key
                    )
                    if isinstance(value, dict)
                    else {
                        "symbol": key,
                        "price": value,
                    }
                    for key, value
                    in data["result"].items()
                ]

        prices = {}

        for row in rows:

            if not isinstance(row, dict):
                continue

            symbol = (
                row.get("symbol")
                or row.get("instrument")
                or row.get("pair")
            )

            if not symbol:
                continue

            symbol = str(symbol).upper()

            price = (
                row.get("last")
                or row.get("lastPrice")
                or row.get("price")
            )

            price = safe_float(price)

            if price is None:
                continue

            prices[symbol] = price

        return prices

    except Exception as e:

        print(
            "[KRAKEN TICKER ERROR]",
            e
        )

        return {}


def get_current_price(
    asset,
    prices
):

    contract = CONTRACTS.get(asset)

    if contract in prices:
        return prices[contract]

    # Fallback matching
    for key, value in prices.items():

        if key.endswith(
            f"_{asset}USD"
        ):
            return value

        if key == f"PF_{asset}USD":
            return value

    return None


# ============================================================
# PIVOTS
# ============================================================

def detect_pivots(
    candles,
    left=PIVOT_LEFT,
    right=PIVOT_RIGHT,
    min_separation=5,
    min_swing_pct=0.0015
):

    highs = []
    lows = []

    if len(candles) < (
        left + right + 1
    ):
        return highs, lows

    for i in range(
        left,
        len(candles) - right
    ):

        current_high = candles[i]["high"]
        current_low = candles[i]["low"]

        left_highs = [
            candles[j]["high"]
            for j in range(
                i - left,
                i
            )
        ]

        right_highs = [
            candles[j]["high"]
            for j in range(
                i + 1,
                i + right + 1
            )
        ]

        left_lows = [
            candles[j]["low"]
            for j in range(
                i - left,
                i
            )
        ]

        right_lows = [
            candles[j]["low"]
            for j in range(
                i + 1,
                i + right + 1
            )
        ]

        is_high = (
            current_high >= max(left_highs)
            and
            current_high >= max(right_highs)
        )

        is_low = (
            current_low <= min(left_lows)
            and
            current_low <= min(right_lows)
        )

        if is_high:

            if highs:

                previous = highs[-1]

                separation = (
                    i - previous["index"]
                )

                swing_pct = abs(
                    current_high
                    - previous["price"]
                ) / previous["price"]

                if (
                    separation
                    < min_separation
                ):
                    if (
                        current_high
                        > previous["price"]
                    ):
                        highs[-1] = {
                            "index": i,
                            "price": current_high,
                            "time": candles[i]["time"],
                        }

                elif swing_pct >= min_swing_pct:

                    highs.append(
                        {
                            "index": i,
                            "price": current_high,
                            "time": candles[i]["time"],
                        }
                    )

            else:

                highs.append(
                    {
                        "index": i,
                        "price": current_high,
                        "time": candles[i]["time"],
                    }
                )

        if is_low:

            if lows:

                previous = lows[-1]

                separation = (
                    i - previous["index"]
                )

                swing_pct = abs(
                    current_low
                    - previous["price"]
                ) / previous["price"]

                if (
                    separation
                    < min_separation
                ):
                    if (
                        current_low
                        < previous["price"]
                    ):
                        lows[-1] = {
                            "index": i,
                            "price": current_low,
                            "time": candles[i]["time"],
                        }

                elif swing_pct >= min_swing_pct:

                    lows.append(
                        {
                            "index": i,
                            "price": current_low,
                            "time": candles[i]["time"],
                        }
                    )

            else:

                lows.append(
                    {
                        "index": i,
                        "price": current_low,
                        "time": candles[i]["time"],
                    }
                )

    return highs, lows


# ============================================================
# TRENDLINE
# ============================================================

def line_value(
    p1,
    p2,
    x
):

    dx = (
        p2["index"]
        - p1["index"]
    )

    if dx == 0:
        return None

    slope = (
        p2["price"]
        - p1["price"]
    ) / dx

    return (
        p1["price"]
        + slope * (
            x - p1["index"]
        )
    )


def trendline_slope_pct(
    p1,
    p2
):

    if p1["price"] == 0:
        return 0

    return (
        (
            p2["price"]
            - p1["price"]
        )
        /
        p1["price"]
    ) / max(
        1,
        p2["index"] - p1["index"]
    )


def trendline_is_valid(
    candles,
    p1,
    p2,
    kind,
    tolerance=TRENDLINE_TOUCH_TOLERANCE
):

    if p2["index"] <= p1["index"]:
        return False

    if kind == "support":

        for i in range(
            p1["index"] + 1,
            p2["index"]
        ):

            line = line_value(
                p1,
                p2,
                i
            )

            if line is None:
                return False

            # Price should not meaningfully break
            # below support before second anchor.
            if (
                candles[i]["low"]
                < line * (1 - tolerance)
            ):
                return False

    elif kind == "resistance":

        for i in range(
            p1["index"] + 1,
            p2["index"]
        ):

            line = line_value(
                p1,
                p2,
                i
            )

            if line is None:
                return False

            if (
                candles[i]["high"]
                > line * (1 + tolerance)
            ):
                return False

    else:
        return False

    return True


def find_valid_trendlines(
    candles,
    highs,
    lows,
    max_pivots=MAX_TRENDLINE_PIVOTS
):

    candidates = []

    highs = highs[-max_pivots:]
    lows = lows[-max_pivots:]

    # --------------------------------------------------------
    # Resistance trendlines
    # --------------------------------------------------------

    for a in range(len(highs)):

        for b in range(
            a + 1,
            len(highs)
        ):

            p1 = highs[a]
            p2 = highs[b]

            if not trendline_is_valid(
                candles,
                p1,
                p2,
                "resistance"
            ):
                continue

            slope = trendline_slope_pct(
                p1,
                p2
            )

            # Resistance can be flat or falling.
            # A strongly rising resistance is less useful
            # for the intended breakout structure.
            if slope > 0.01:
                continue

            candidates.append(
                {
                    "type": "resistance",
                    "p1": p1,
                    "p2": p2,
                    "slope": slope,
                    "touches": 2,
                    "strength": abs(slope),
                }
            )

    # --------------------------------------------------------
    # Support trendlines
    # --------------------------------------------------------

    for a in range(len(lows)):

        for b in range(
            a + 1,
            len(lows)
        ):

            p1 = lows[a]
            p2 = lows[b]

            if not trendline_is_valid(
                candles,
                p1,
                p2,
                "support"
            ):
                continue

            slope = trendline_slope_pct(
                p1,
                p2
            )

            # Support can be flat or rising.
            # Strongly falling support is less useful.
            if slope < -0.01:
                continue

            candidates.append(
                {
                    "type": "support",
                    "p1": p1,
                    "p2": p2,
                    "slope": slope,
                    "touches": 2,
                    "strength": abs(slope),
                }
            )

    if not candidates:
        return []

    # Prefer recent second anchor,
    # then flatter/more stable lines.
    candidates.sort(
        key=lambda x: (
            x["p2"]["index"],
            x["touches"],
            -x["strength"],
        ),
        reverse=True
    )

    return candidates


def trendline_price(
    trendline,
    index
):

    return line_value(
        trendline["p1"],
        trendline["p2"],
        index
    )


# ============================================================
# BREAKOUT
# ============================================================

def candle_body_pct(candle):

    if candle["open"] == 0:
        return 0

    return abs(
        candle["close"]
        - candle["open"]
    ) / candle["open"]


def find_trendline_breakout(
    candles,
    trendlines,
    start_index,
    max_lookahead
):

    if not trendlines:
        return None

    last_index = min(
        len(candles) - 1,
        start_index + max_lookahead
    )

    for trendline in trendlines:

        # Breakout must occur after second anchor.
        scan_start = max(
            start_index,
            trendline["p2"]["index"] + 1
        )

        for i in range(
            scan_start,
            last_index + 1
        ):

            candle = candles[i]

            line = trendline_price(
                trendline,
                i
            )

            if line is None or line <= 0:
                continue

            body_pct = candle_body_pct(
                candle
            )

            if body_pct < MIN_BREAK_BODY_PCT:
                continue

            if trendline["type"] == "resistance":

                previous_close = candles[
                    i - 1
                ]["close"]

                breakout = (
                    previous_close
                    <= line
                    and
                    candle["close"]
                    > line * (
                        1
                        + TRENDLINE_BREAK_BUFFER
                    )
                )

                if breakout:

                    return {
                        "index": i,
                        "time": candle["time"],
                        "price": candle["close"],
                        "type": "LONG",
                        "trendline": trendline,
                    }

            elif trendline["type"] == "support":

                previous_close = candles[
                    i - 1
                ]["close"]

                breakout = (
                    previous_close
                    >= line
                    and
                    candle["close"]
                    < line * (
                        1
                        - TRENDLINE_BREAK_BUFFER
                    )
                )

                if breakout:

                    return {
                        "index": i,
                        "time": candle["time"],
                        "price": candle["close"],
                        "type": "SHORT",
                        "trendline": trendline,
                    }

    return None


# ============================================================
# VOLUME
# ============================================================

def calculate_rvol(
    candles,
    index
):

    if index < RVOL_LOOKBACK:
        return 0.0

    current_volume = (
        candles[index]["volume"]
    )

    previous = [
        candles[i]["volume"]
        for i in range(
            index - RVOL_LOOKBACK,
            index
        )
    ]

    previous = [
        x for x in previous
        if x is not None
        and x >= 0
    ]

    if not previous:
        return 0.0

    avg_volume = (
        sum(previous)
        / len(previous)
    )

    if avg_volume <= 0:
        return 0.0

    return (
        current_volume
        / avg_volume
    )


# ============================================================
# SWING LEVELS
# ============================================================

def choose_levels(
    candles,
    highs,
    lows,
    break_index,
    direction,
    entry
):

    # Only pivots CONFIRMED BEFORE the breakout
    # are allowed. No future information.
    confirmed_highs = [
        p for p in highs
        if p["index"] < break_index
    ]

    confirmed_lows = [
        p for p in lows
        if p["index"] < break_index
    ]

    if direction == "LONG":

        # TP = nearest confirmed swing high above entry
        future_highs = [
            p
            for p in confirmed_highs
            if p["price"] > entry
        ]

        future_highs.sort(
            key=lambda p: p["price"]
        )

        tp_pivot = (
            future_highs[0]
            if future_highs
            else None
        )

        # SL = previous confirmed swing low
        previous_lows = [
            p
            for p in confirmed_lows
            if p["index"] < break_index
        ]

        previous_lows.sort(
            key=lambda p: p["index"],
            reverse=True
        )

        sl_pivot = (
            previous_lows[0]
            if previous_lows
            else None
        )

        if not tp_pivot or not sl_pivot:
            return None

        tp = tp_pivot["price"]

        sl = (
            sl_pivot["price"]
            * (1 - SL_BUFFER_PCT)
        )

    else:

        # TP = nearest confirmed swing low below entry
        future_lows = [
            p
            for p in confirmed_lows
            if p["price"] < entry
        ]

        future_lows.sort(
            key=lambda p: p["price"],
            reverse=True
        )

        tp_pivot = (
            future_lows[0]
            if future_lows
            else None
        )

        # SL = previous confirmed swing high
        previous_highs = [
            p
            for p in confirmed_highs
            if p["index"] < break_index
        ]

        previous_highs.sort(
            key=lambda p: p["index"],
            reverse=True
        )

        sl_pivot = (
            previous_highs[0]
            if previous_highs
            else None
        )

        if not tp_pivot or not sl_pivot:
            return None

        tp = tp_pivot["price"]

        sl = (
            sl_pivot["price"]
            * (1 + SL_BUFFER_PCT)
        )

    # --------------------------------------------------------
    # Validate direction
    # --------------------------------------------------------

    if direction == "LONG":

        if tp <= entry:
            return None

        if sl >= entry:
            return None

        risk = entry - sl
        reward = tp - entry

    else:

        if tp >= entry:
            return None

        if sl <= entry:
            return None

        risk = sl - entry
        reward = entry - tp

    if risk <= 0 or reward <= 0:
        return None

    risk_pct = risk / entry
    reward_pct = reward / entry

    if risk_pct < MIN_SL_DISTANCE_PCT:
        return None

    if reward_pct < MIN_TP_DISTANCE_PCT:
        return None

    rr = reward / risk

    if rr < MIN_RR:
        return None

    return {
        "tp": tp,
        "sl": sl,
        "rr": rr,
        "tp_pivot": tp_pivot,
        "sl_pivot": sl_pivot,
    }


# ============================================================
# SIGNAL KEY
# ============================================================

def build_signal_key(
    asset,
    direction,
    entry_timestamp
):

    return (
        f"{asset}_"
        f"{direction}_"
        f"{entry_timestamp}"
    )


# ============================================================
# OPEN TRADE CHECK
# ============================================================

def has_open_trade(
    asset,
    direction=None
):

    conn = db_connect()

    if direction:

        row = conn.execute(
            """
            SELECT id
            FROM trades
            WHERE asset = ?
              AND direction = ?
              AND UPPER(COALESCE(status, 'OPEN')) = 'OPEN'
            LIMIT 1
            """,
            (
                asset,
                direction,
            )
        ).fetchone()

    else:

        row = conn.execute(
            """
            SELECT id
            FROM trades
            WHERE asset = ?
              AND UPPER(COALESCE(status, 'OPEN')) = 'OPEN'
            LIMIT 1
            """,
            (asset,)
        ).fetchone()

    conn.close()

    return row is not None


# ============================================================
# INSERT SIGNAL + TRADE
# ============================================================

def insert_signal_and_trade(
    signal
):

    conn = db_connect()

    signal_key = signal["signal_key"]

    try:

        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO signals (
                signal_key,
                asset,
                direction,
                pattern,
                entry,
                tp,
                sl,
                rr,
                rvol,
                trendline_type_1h,
                trendline_type_5m,
                signal_timestamp,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_key,
                signal["asset"],
                signal["direction"],
                signal["pattern"],
                signal["entry"],
                signal["tp"],
                signal["sl"],
                signal["rr"],
                signal["rvol"],
                signal["trendline_type_1h"],
                signal["trendline_type_5m"],
                signal["signal_timestamp"],
                now_utc_ts(),
            )
        )

        if cursor.rowcount == 0:

            conn.rollback()
            conn.close()

            return False

        conn.execute(
            """
            INSERT OR IGNORE INTO trades (
                signal_key,
                asset,
                symbol,
                pattern,
                direction,
                entry,
                entry_price,
                current_price,
                tp,
                tp_price,
                sl,
                sl_price,
                rr,
                entry_timestamp,
                entry_time,
                status,
                pnl_pct,
                created_at,
                updated_at,
                detected_at,
                contract,
                notified_new
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_key,
                signal["asset"],
                signal["asset"],
                signal["pattern"],
                signal["direction"],
                signal["entry"],
                signal["entry"],
                signal["entry"],
                signal["tp"],
                signal["tp"],
                signal["sl"],
                signal["sl"],
                signal["rr"],
                signal["signal_timestamp"],
                signal["signal_timestamp"],
                "OPEN",
                0.0,
                now_utc_ts(),
                now_utc_ts(),
                signal["signal_timestamp"],
                signal["contract"],
                0,
            )
        )

        conn.commit()

        conn.close()

        return True

    except Exception as e:

        conn.rollback()
        conn.close()

        print(
            "[DB SIGNAL ERROR]",
            e
        )

        return False


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade_id,
    exit_price,
    exit_reason,
    exit_timestamp=None
):

    if exit_timestamp is None:
        exit_timestamp = now_utc_ts()

    conn = db_connect()

    row = conn.execute(
        """
        SELECT
            entry,
            direction,
            entry_timestamp
        FROM trades
        WHERE id = ?
        LIMIT 1
        """,
        (trade_id,)
    ).fetchone()

    if not row:

        conn.close()
        return None

    entry = safe_float(
        row["entry"]
    )

    direction = (
        str(row["direction"])
        .upper()
        if row["direction"]
        else ""
    )

    if entry is None:
        conn.close()
        return None

    if direction == "LONG":

        pnl_pct = (
            (exit_price - entry)
            / entry
            * 100
        )

    else:

        pnl_pct = (
            (entry - exit_price)
            / entry
            * 100
        )

    conn.execute(
        """
        UPDATE trades
        SET
            current_price = ?,
            exit_price = ?,
            exit_time = ?,
            exit_timestamp = ?,
            exit_reason = ?,
            pnl_pct = ?,
            status = 'CLOSED',
            updated_at = ?
        WHERE id = ?
        """,
        (
            exit_price,
            exit_price,
            exit_timestamp,
            exit_timestamp,
            exit_reason,
            pnl_pct,
            now_utc_ts(),
            trade_id,
        )
    )

    conn.commit()

    updated = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE id = ?
        """,
        (trade_id,)
    ).fetchone()

    conn.close()

    return {
        "trade": updated,
        "pnl_pct": pnl_pct,
    }


# ============================================================
# RECONCILE OPEN TRADES
# ============================================================

def reconcile_open_trades(
    prices
):

    closed = []

    rows = get_open_trades()

    for trade in rows:

        trade_id = row_value(
            trade,
            "id"
        )

        asset = row_value(
            trade,
            "asset"
        )

        direction = row_value(
            trade,
            "direction"
        )

        entry = safe_float(
            row_value(trade, "entry")
        )

        tp = safe_float(
            row_value(trade, "tp")
        )

        sl = safe_float(
            row_value(trade, "sl")
        )

        entry_ts = safe_int(
            row_value(
                trade,
                "entry_timestamp"
            )
        )

        if not all(
            [
                trade_id is not None,
                asset,
                direction,
                entry is not None,
                tp is not None,
                sl is not None,
                entry_ts is not None,
            ]
        ):

            print(
                "[DB WARNING] "
                f"Skipping incomplete legacy "
                f"trade id={trade_id}"
            )

            continue

        direction = str(
            direction
        ).upper()

        current = get_current_price(
            asset,
            prices
        )

        if current is None:
            continue

        # ----------------------------------------------------
        # Max holding time
        # ----------------------------------------------------

        age_seconds = (
            now_utc_ts()
            - entry_ts
        )

        exit_reason = None

        # Same candle TP/SL:
        # SL has priority.
        if direction == "LONG":

            if current <= sl:

                exit_reason = "SL"

            elif current >= tp:

                exit_reason = "TP"

        else:

            if current >= sl:

                exit_reason = "SL"

            elif current <= tp:

                exit_reason = "TP"

        if (
            exit_reason is None
            and
            age_seconds
            >= MAX_HOLD_HOURS * 3600
        ):

            exit_reason = "TIME"

        if exit_reason:

            result = close_trade(
                trade_id,
                current,
                exit_reason
            )

            if result:

                closed.append(
                    {
                        "trade": result["trade"],
                        "pnl_pct": result["pnl_pct"],
                        "reason": exit_reason,
                    }
                )

        else:

            conn = db_connect()

            conn.execute(
                """
                UPDATE trades
                SET
                    current_price = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    current,
                    now_utc_ts(),
                    trade_id,
                )
            )

            conn.commit()
            conn.close()

    return closed


# ============================================================
# OPEN TRADES TELEGRAM
# ============================================================

def build_open_trades_message(
    prices
):

    rows = get_open_trades()

    if not rows:

        return (
            "📊 <b>KRAKEN TRENDLINE SCANNER</b>\n\n"
            "🟢 <b>OPEN TRADES: 0</b>"
        )

    lines = [
        "📊 <b>KRAKEN TRENDLINE SCANNER</b>",
        "",
        f"🟢 <b>OPEN TRADES: {len(rows)}</b>",
        "",
    ]

    valid_count = 0

    for trade in rows:

        asset = row_value(
            trade,
            "asset"
        )

        direction = row_value(
            trade,
            "direction"
        )

        entry = safe_float(
            row_value(trade, "entry")
        )

        tp = safe_float(
            row_value(trade, "tp")
        )

        sl = safe_float(
            row_value(trade, "sl")
        )

        entry_ts = safe_int(
            row_value(
                trade,
                "entry_timestamp"
            )
        )

        if not all(
            [
                asset,
                direction,
                entry is not None,
                tp is not None,
                sl is not None,
                entry_ts is not None,
            ]
        ):
            continue

        valid_count += 1

        current = get_current_price(
            asset,
            prices
        )

        if current is None:
            current = entry

        direction = str(
            direction
        ).upper()

        if direction == "LONG":

            pnl = (
                current - entry
            ) / entry * 100

            emoji = "🟢"

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

            pnl = (
                entry - current
            ) / entry * 100

            emoji = "🔴"

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

        duration = format_duration(
            now_utc_ts()
            - entry_ts
        )

        lines.extend(
            [
                f"{emoji} <b>{asset} {direction}</b>",
                f"💰 Entry: <b>{fmt_price(entry)}</b>",
                f"💵 Current: <b>{fmt_price(current)}</b> "
                f"({fmt_pct(pnl)})",
                f"🛑 SL: <b>{fmt_price(sl)}</b> "
                f"({fmt_pct(sl_pct)})",
                f"🎯 TP: <b>{fmt_price(tp)}</b> "
                f"({fmt_pct(tp_pct)})",
                f"⏱ Duration: <b>{duration}</b>",
                "",
            ]
        )

    if valid_count == 0:

        return (
            "📊 <b>KRAKEN TRENDLINE SCANNER</b>\n\n"
            "🟢 <b>OPEN TRADES: 0 VALID</b>"
        )

    return "\n".join(lines)


# ============================================================
# PERFORMANCE
# ============================================================

def build_performance_message():

    conn = db_connect()

    total = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE UPPER(status) = 'CLOSED'
        """
    ).fetchone()[0]

    wins = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE UPPER(status) = 'CLOSED'
          AND pnl_pct > 0
        """
    ).fetchone()[0]

    losses = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE UPPER(status) = 'CLOSED'
          AND pnl_pct <= 0
        """
    ).fetchone()[0]

    pnl = conn.execute(
        """
        SELECT COALESCE(SUM(pnl_pct), 0)
        FROM trades
        WHERE UPPER(status) = 'CLOSED'
        """
    ).fetchone()[0]

    tp_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE UPPER(status) = 'CLOSED'
          AND UPPER(exit_reason) = 'TP'
        """
    ).fetchone()[0]

    sl_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE UPPER(status) = 'CLOSED'
          AND UPPER(exit_reason) = 'SL'
        """
    ).fetchone()[0]

    conn.close()

    win_rate = (
        wins / total * 100
        if total
        else 0
    )

    return (
        "📈 <b>PERFORMANCE</b>\n\n"
        f"Trades: <b>{total}</b>\n"
        f"Wins: <b>{wins}</b>\n"
        f"Losses: <b>{losses}</b>\n"
        f"Win Rate: <b>{win_rate:.2f}%</b>\n"
        f"TP: <b>{tp_count}</b>\n"
        f"SL: <b>{sl_count}</b>\n"
        f"Net PnL: <b>{fmt_pct(pnl)}</b>"
    )


# ============================================================
# CLOSE MESSAGE
# ============================================================

def build_close_message(
    closed
):

    if not closed:
        return None

    lines = [
        "📊 <b>KRAKEN TRENDLINE SCANNER</b>",
        "",
    ]

    for item in closed:

        trade = item["trade"]

        asset = row_value(
            trade,
            "asset",
            "?"
        )

        direction = row_value(
            trade,
            "direction",
            "?"
        )

        entry = safe_float(
            row_value(trade, "entry"),
            0
        )

        exit_price = safe_float(
            row_value(
                trade,
                "exit_price"
            ),
            0
        )

        pnl = item["pnl_pct"]

        reason = item["reason"]

        emoji = (
            "🎯"
            if reason == "TP"
            else
            "🛑"
            if reason == "SL"
            else
            "⏱"
        )

        entry_ts = safe_int(
            row_value(
                trade,
                "entry_timestamp"
            )
        )

        exit_ts = safe_int(
            row_value(
                trade,
                "exit_timestamp"
            ),
            now_utc_ts()
        )

        duration = "N/A"

        if entry_ts:

            duration = format_duration(
                exit_ts - entry_ts
            )

        lines.extend(
            [
                f"{emoji} <b>CLOSE "
                f"{asset} {direction}</b>",
                f"Entry: <b>{fmt_price(entry)}</b>",
                f"Exit: <b>{fmt_price(exit_price)}</b>",
                f"Result: <b>{reason}</b>",
                f"PnL: <b>{fmt_pct(pnl)}</b>",
                f"Duration: <b>{duration}</b>",
                "",
            ]
        )

    return "\n".join(lines)


# ============================================================
# SCAN SUMMARY
# ============================================================

def build_summary_message():

    local = now_tehran()

    return (
        "📊 <b>KRAKEN TRENDLINE SCANNER</b>\n\n"
        f"Version: <b>{VERSION}</b>\n"
        "Mode: <b>PAPER ONLY</b>\n"
        f"Assets: <b>{stats['assets']}/"
        f"{len(ASSETS)}</b>\n\n"
        f"1H Valid Trendlines: "
        f"<b>{stats['trendlines_1h']}</b>\n"
        f"1H Breakouts: "
        f"<b>{stats['breaks_1h']}</b>\n"
        f"5M Trendlines: "
        f"<b>{stats['trendlines_5m']}</b>\n"
        f"5M Breakouts: "
        f"<b>{stats['breaks_5m']}</b>\n"
        f"Volume Accepted: "
        f"<b>{stats['volume_ok']}</b>\n"
        f"Valid TP/SL: "
        f"<b>{stats['valid_levels']}</b>\n\n"
        f"New Signals: "
        f"<b>{stats['new_signals']}</b>\n"
        f"Closed Trades: "
        f"<b>{stats['closed_trades']}</b>\n"
        f"Time: <b>{local:%Y-%m-%d %H:%M}</b>"
    )


# ============================================================
# CHART
# ============================================================

def create_signal_chart(
    asset,
    candles,
    breakout,
    entry,
    tp,
    sl,
    filename
):

    if plt is None:
        return None

    try:

        data = candles[
            -CHART_CANDLES:
        ]

        if not data:
            return None

        xs = list(
            range(len(data))
        )

        opens = [
            x["open"]
            for x in data
        ]

        highs = [
            x["high"]
            for x in data
        ]

        lows = [
            x["low"]
            for x in data
        ]

        closes = [
            x["close"]
            for x in data
        ]

        fig, ax = plt.subplots(
            figsize=(12, 7)
        )

        # ----------------------------------------------------
        # Candles
        # ----------------------------------------------------

        for i in xs:

            o = opens[i]
            h = highs[i]
            l = lows[i]
            c = closes[i]

            if c >= o:
                body_bottom = o
                body_height = c - o
            else:
                body_bottom = c
                body_height = o - c

            ax.plot(
                [i, i],
                [l, h],
                linewidth=0.8
            )

            width = 0.55

            ax.add_patch(
                plt.Rectangle(
                    (
                        i - width / 2,
                        body_bottom
                    ),
                    width,
                    max(
                        body_height,
                        1e-12
                    ),
                    fill=False
                )
            )

        # ----------------------------------------------------
        # Trendline
        # ----------------------------------------------------

        trendline = breakout.get(
            "trendline"
        )

        if trendline:

            p1 = trendline["p1"]
            p2 = trendline["p2"]

            start_global = p1["index"]
            end_global = (
                len(candles) - 1
            )

            offset = (
                len(candles)
                - len(data)
            )

            x1 = (
                start_global
                - offset
            )

            x2 = (
                end_global
                - offset
            )

            if x2 >= 0:

                y1 = line_value(
                    p1,
                    p2,
                    start_global
                )

                y2 = line_value(
                    p1,
                    p2,
                    end_global
                )

                if y1 is not None and y2 is not None:

                    ax.plot(
                        [x1, x2],
                        [y1, y2],
                        linewidth=2,
                        linestyle="--"
                    )

        # ----------------------------------------------------
        # Breakout
        # ----------------------------------------------------

        breakout_global = breakout[
            "index"
        ]

        breakout_x = (
            breakout_global
            - (
                len(candles)
                - len(data)
            )
        )

        if 0 <= breakout_x < len(data):

            ax.scatter(
                [breakout_x],
                [candles[
                    breakout_global
                ]["close"]],
                s=70,
                marker="o",
                zorder=5
            )

            ax.annotate(
                "BREAK",
                (
                    breakout_x,
                    candles[
                        breakout_global
                    ]["close"]
                ),
                xytext=(5, 10),
                textcoords="offset points"
            )

        # ----------------------------------------------------
        # Entry / TP / SL
        # ----------------------------------------------------

        ax.axhline(
            entry,
            linestyle="-",
            linewidth=1.5,
            label=f"Entry {fmt_price(entry)}"
        )

        ax.axhline(
            tp,
            linestyle="--",
            linewidth=1.3,
            label=f"TP {fmt_price(tp)}"
        )

        ax.axhline(
            sl,
            linestyle="--",
            linewidth=1.3,
            label=f"SL {fmt_price(sl)}"
        )

        ax.set_title(
            f"{asset} 5M Trendline Breakout"
        )

        ax.set_xlabel(
            "5M Closed Candles"
        )

        ax.set_ylabel(
            "Price"
        )

        ax.grid(
            alpha=0.25
        )

        ax.legend(
            loc="best"
        )

        fig.tight_layout()

        fig.savefig(
            filename,
            dpi=130
        )

        plt.close(fig)

        return filename

    except Exception as e:

        print(
            "[CHART ERROR]",
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

def build_signal_message(
    signal
):

    direction = signal["direction"]

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    entry = signal["entry"]
    tp = signal["tp"]
    sl = signal["sl"]

    if direction == "LONG":

        tp_pct = (
            tp - entry
        ) / entry * 100

        sl_pct = (
            sl - entry
        ) / entry * 100

    else:

        tp_pct = (
            entry - tp
        ) / entry * 100

        sl_pct = (
            entry - sl
        ) / entry * 100

    return (
        "📊 <b>KRAKEN TRENDLINE SIGNAL</b>\n\n"
        f"{emoji} <b>{direction} "
        f"{signal['asset']}</b>\n\n"
        f"Pattern: <b>{signal['pattern']}</b>\n"
        f"Entry: <b>{fmt_price(entry)}</b>\n"
        f"TP: <b>{fmt_price(tp)}</b> "
        f"({fmt_pct(tp_pct)})\n"
        f"SL: <b>{fmt_price(sl)}</b> "
        f"({fmt_pct(sl_pct)})\n"
        f"RR: <b>{signal['rr']:.2f}</b>\n"
        f"RVOL: <b>{signal['rvol']:.2f}</b>\n\n"
        f"1H TL: <b>{signal['trendline_type_1h']}</b>\n"
        f"5M TL: <b>{signal['trendline_type_5m']}</b>\n"
        f"Time: <b>"
        f"{ts_to_tehran(signal['signal_timestamp']):%Y-%m-%d %H:%M}"
        f"</b>"
    )


# ============================================================
# ASSET SCANNER
# ============================================================

def scan_asset(
    asset
):

    contract = CONTRACTS[asset]

    # ========================================================
    # 1H DATA
    # ========================================================

    candles_1h = fetch_klines(
        contract,
        TF_1H,
        CANDLES_1H
    )

    if len(candles_1h) < 50:
        return None

    stats["candles_1h"] += 1

    highs_1h, lows_1h = detect_pivots(
        candles_1h,
        PIVOT_LEFT,
        PIVOT_RIGHT,
        MIN_PIVOT_SEPARATION_1H,
        MIN_SWING_PCT_1H
    )

    trendlines_1h = find_valid_trendlines(
        candles_1h,
        highs_1h,
        lows_1h
    )

    if not trendlines_1h:
        return None

    stats["trendlines_1h"] += len(
        trendlines_1h
    )

    # ========================================================
    # 1H BREAKOUT
    # ========================================================

    breakout_1h = find_trendline_breakout(
        candles_1h,
        trendlines_1h,
        max(
            1,
            len(candles_1h)
            - BREAKOUT_LOOKAHEAD_1H
        ),
        BREAKOUT_LOOKAHEAD_1H
    )

    if not breakout_1h:
        return None

    stats["breaks_1h"] += 1

    direction = breakout_1h["type"]

    # ========================================================
    # 5M DATA
    # ========================================================

    candles_5m = fetch_klines(
        contract,
        TF_5M,
        CANDLES_5M
    )

    if len(candles_5m) < 80:
        return None

    stats["candles_5m"] += 1

    highs_5m, lows_5m = detect_pivots(
        candles_5m,
        PIVOT_LEFT,
        PIVOT_RIGHT,
        MIN_PIVOT_SEPARATION_5M,
        MIN_SWING_PCT_5M
    )

    trendlines_5m = find_valid_trendlines(
        candles_5m,
        highs_5m,
        lows_5m
    )

    if not trendlines_5m:
        return None

    stats["trendlines_5m"] += len(
        trendlines_5m
    )

    # ========================================================
    # ONLY LOOK FOR 5M BREAKOUT AFTER 1H BREAKOUT
    # ========================================================

    breakout_time_1h = (
        breakout_1h["time"]
    )

    candidate_start = 0

    for i, candle in enumerate(
        candles_5m
    ):

        if (
            candle["time"]
            > breakout_time_1h
        ):

            candidate_start = i
            break

    if candidate_start <= 0:
        return None

    breakout_5m = find_trendline_breakout(
        candles_5m,
        trendlines_5m,
        candidate_start,
        BREAKOUT_LOOKAHEAD_5M
    )

    if not breakout_5m:
        return None

    # Direction must agree with 1H breakout.
    if (
        breakout_5m["type"]
        != direction
    ):
        return None

    stats["breaks_5m"] += 1

    # ========================================================
    # VOLUME
    # ========================================================

    break_index = breakout_5m[
        "index"
    ]

    rvol = calculate_rvol(
        candles_5m,
        break_index
    )

    if rvol < MIN_RVOL:
        return None

    stats["volume_ok"] += 1

    # ========================================================
    # ENTRY
    # ========================================================

    entry = breakout_5m["price"]

    # ========================================================
    # TP / SL
    # ========================================================

    levels = choose_levels(
        candles_5m,
        highs_5m,
        lows_5m,
        break_index,
        direction,
        entry
    )

    if not levels:
        return None

    stats["valid_levels"] += 1

    # ========================================================
    # SIGNAL
    # ========================================================

    signal_timestamp = (
        breakout_5m["time"]
    )

    signal_key = build_signal_key(
        asset,
        direction,
        signal_timestamp
    )

    signal = {
        "signal_key": signal_key,
        "asset": asset,
        "contract": contract,
        "direction": direction,
        "pattern": (
            "Trendline Breakout"
        ),
        "entry": entry,
        "tp": levels["tp"],
        "sl": levels["sl"],
        "rr": levels["rr"],
        "rvol": rvol,
        "trendline_type_1h": (
            breakout_1h[
                "trendline"
            ]["type"]
        ),
        "trendline_type_5m": (
            breakout_5m[
                "trendline"
            ]["type"]
        ),
        "signal_timestamp": (
            signal_timestamp
        ),
        "candles_5m": candles_5m,
        "breakout_5m": breakout_5m,
    }

    return signal


# ============================================================
# MAIN SCAN
# ============================================================

def scan():

    global stats

    stats = {
        "assets": 0,
        "candles_1h": 0,
        "candles_5m": 0,
        "trendlines_1h": 0,
        "breaks_1h": 0,
        "trendlines_5m": 0,
        "breaks_5m": 0,
        "volume_ok": 0,
        "valid_levels": 0,
        "new_signals": 0,
        "closed_trades": 0,
    }

    print("=" * 70)

    print(
        f"KRAKEN FUTURES TRENDLINE SCANNER "
        f"v{VERSION}"
    )

    print("=" * 70)

    print(
        f"REAL_TRADING = {REAL_TRADING}"
    )

    if REAL_TRADING:
        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    init_db()

    # --------------------------------------------------------
    # Current prices
    # --------------------------------------------------------

    prices = fetch_tickers()

    # --------------------------------------------------------
    # Reconcile open trades first
    # --------------------------------------------------------

    closed = reconcile_open_trades(
        prices
    )

    if closed:

        stats["closed_trades"] = len(
            closed
        )

        message = build_close_message(
            closed
        )

        if message:
            send_telegram(message)

    # --------------------------------------------------------
    # Scan assets
    # --------------------------------------------------------

    signals_created = 0

    for asset in ASSETS:

        stats["assets"] += 1

        if signals_created >= MAX_SIGNALS_PER_SCAN:
            break

        try:

            # ------------------------------------------------
            # Avoid multiple open trades for same asset.
            # Opposite direction is allowed by design.
            # ------------------------------------------------

            if has_open_trade(asset):
                continue

            signal = scan_asset(
                asset
            )

            if not signal:
                continue

            # ------------------------------------------------
            # DB insertion
            # ------------------------------------------------

            inserted = insert_signal_and_trade(
                signal
            )

            if not inserted:
                continue

            signals_created += 1
            stats["new_signals"] += 1

            # ------------------------------------------------
            # Telegram
            # ------------------------------------------------

            message = build_signal_message(
                signal
            )

            chart_file = (
                f"signal_{asset}_"
                f"{signal['direction']}_"
                f"{signal['signal_timestamp']}.png"
            )

            chart = create_signal_chart(
                asset,
                signal["candles_5m"],
                signal["breakout_5m"],
                signal["entry"],
                signal["tp"],
                signal["sl"],
                chart_file
            )

            send_telegram(
                message,
                chart
            )

            if chart and os.path.exists(chart):
                try:
                    os.remove(chart)
                except Exception:
                    pass

        except Exception as e:

            print(
                f"[ASSET ERROR] "
                f"{asset}: {e}"
            )

            traceback.print_exc()

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    summary = build_summary_message()

    send_telegram(
        summary
    )

    # --------------------------------------------------------
    # Open trades
    # --------------------------------------------------------

    open_message = build_open_trades_message(
        prices
    )

    send_telegram(
        open_message
    )

    # --------------------------------------------------------
    # Performance
    # --------------------------------------------------------

    performance = build_performance_message()

    send_telegram(
        performance
    )

    print(summary)

    print()
    print(
        f"Signals created: "
        f"{stats['new_signals']}"
    )

    print(
        f"Open trades: "
        f"{len(get_open_trades())}"
    )


# ============================================================
# PERIODIC REPORT CONTROL
# ============================================================

def periodic_report_due():

    conn = db_connect()

    row = conn.execute(
        """
        SELECT value
        FROM scanner_meta
        WHERE key = 'last_periodic_report'
        LIMIT 1
        """
    ).fetchone()

    conn.close()

    if not row:
        return True

    last = safe_int(
        row["value"],
        0
    )

    return (
        now_utc_ts() - last
        >= PERIODIC_REPORT_SECONDS
    )


def mark_periodic_report():

    conn = db_connect()

    conn.execute(
        """
        INSERT INTO scanner_meta (
            key,
            value
        )
        VALUES (
            'last_periodic_report',
            ?
        )
        ON CONFLICT(key)
        DO UPDATE SET
            value = excluded.value
        """,
        (
            str(now_utc_ts()),
        )
    )

    conn.commit()
    conn.close()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        scan()

    except KeyboardInterrupt:

        print(
            "[STOPPED] Keyboard interrupt"
        )

        sys.exit(0)

    except Exception as e:

        print(
            "[FATAL ERROR]",
            e
        )

        traceback.print_exc()

        sys.exit(1)
