# ============================================================
# KRAKEN FUTURES TRENDLINE LIVE SIGNAL SCANNER
# VERSION 7.1.3
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
#   TP / SL validation
#        ↓
#   NEW SIGNAL
#
# FEATURES
# - Closed candles only
# - No lookahead
# - Existing DB preserved
# - Automatic DB migration
# - Scan statistics reset every run
# - Performance cumulative after reset
# - Immediate NEW SIGNAL notification
# - Immediate CLOSED TRADE notification
# - OPEN TRADES report
# - 5M TP / SL based on confirmed pivots
# - 5M chart with trendline / P1 / P2 / breakout / Entry / TP / SL
# - REAL_TRADING permanently disabled
#
# KRAKEN CURRENT CHART API:
# /api/charts/v1/trade/{symbol}/{resolution}
#
# Valid resolutions:
# 1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d, 1w
# ============================================================

import os
import io
import sqlite3
import traceback
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "7.1.3"

REAL_TRADING = False

DB_FILE = "kraken_pattern_live_v52.db"

REQUEST_TIMEOUT = 20

KRAKEN_CHART_URL = (
    "https://futures.kraken.com/api/charts/v1/trade/"
    "{symbol}/{resolution}"
)

KRAKEN_TICKER_URL = (
    "https://futures.kraken.com/derivatives/api/v3/tickers"
)

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)

# ============================================================
# PERFORMANCE RESET
#
# This is intentionally NOT tied to VERSION.
#
# Change this value manually only if you want to reset
# cumulative performance again.
# ============================================================

PERFORMANCE_RESET_KEY = "2026-09-21"

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

MAX_SIGNALS_PER_SCAN = 1

MAX_HOLD_HOURS = 48

# ============================================================
# PIVOT / TRENDLINE
# ============================================================

PIVOT_LEFT = 3
PIVOT_RIGHT = 3

MIN_TRENDLINE_BARS = 8

TRENDLINE_TOLERANCE_PCT = 0.20

BREAKOUT_BUFFER_PCT = 0.05

# ============================================================
# RVOL
# ============================================================

RVOL_LOOKBACK = 20

RVOL_MIN = 1.20

# ============================================================
# TP / SL
# ============================================================

MIN_RR = 1.01

SL_BUFFER_PCT = 0.05 / 100.0


# ============================================================
# CURRENT RUN STATS
# ============================================================

def new_stats():
    return {
        "assets_scanned": 0,
        "h1_valid_trendlines": 0,
        "h1_breakouts": 0,
        "m5_trendlines": 0,
        "m5_breakouts": 0,
        "volume_accepted": 0,
        "valid_tp_sl": 0,
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

TEHRAN_TZ = timezone(
    timedelta(
        hours=3,
        minutes=30,
    )
)


def utc_now():
    return datetime.now(timezone.utc)


def iso_utc(dt=None):

    if dt is None:
        dt = utc_now()

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=timezone.utc
        )

    return dt.astimezone(
        timezone.utc
    ).isoformat()


def parse_datetime(value):

    if not value:
        return None

    try:

        dt = datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00",
            )
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        )

    except Exception:
        return None


def tehran_time(value):

    dt = parse_datetime(value)

    if dt is None:
        return "-"

    return dt.astimezone(
        TEHRAN_TZ
    ).strftime(
        "%Y/%m/%d %H:%M:%S"
    )


def fmt_price(value):

    if value is None:
        return "-"

    try:
        value = float(value)
    except Exception:
        return "-"

    if value >= 1000:
        return f"{value:,.2f}"

    if value >= 1:
        return f"{value:.4f}"

    if value >= 0.01:
        return f"{value:.6f}"

    return f"{value:.8f}"


def fmt_pct(value):

    if value is None:
        return "-"

    try:
        return f"{float(value):+.2f}%"
    except Exception:
        return "-"


def fmt_duration(seconds):

    if seconds is None:
        return "-"

    try:
        seconds = max(
            0,
            int(seconds),
        )
    except Exception:
        return "-"

    days, rem = divmod(
        seconds,
        86400,
    )

    hours, rem = divmod(
        rem,
        3600,
    )

    minutes, _ = divmod(
        rem,
        60,
    )

    if days:
        return (
            f"{days}d "
            f"{hours}h "
            f"{minutes}m"
        )

    if hours:
        return (
            f"{hours}h "
            f"{minutes}m"
        )

    return f"{minutes}m"


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
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}"
        "/sendMessage"
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

        if not r.ok:

            print(
                "[TELEGRAM ERROR]",
                r.status_code,
                r.text[:500],
            )

            return False

        return True

    except Exception as e:

        print(
            "[TELEGRAM ERROR]",
            e,
        )

        return False


def telegram_photo(
    image_bytes,
    caption,
):

    if not telegram_enabled():
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}"
        "/sendPhoto"
    )

    try:

        files = {
            "photo": (
                "chart.png",
                image_bytes,
                "image/png",
            )
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

        if not r.ok:

            print(
                "[TELEGRAM PHOTO ERROR]",
                r.status_code,
                r.text[:500],
            )

            return False

        return True

    except Exception as e:

        print(
            "[TELEGRAM PHOTO ERROR]",
            e,
        )

        return False


# ============================================================
# DATABASE
# ============================================================

def db_connect():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30,
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA journal_mode=WAL"
    )

    conn.execute(
        "PRAGMA busy_timeout=30000"
    )

    return conn


def table_columns(
    conn,
    table_name,
):

    rows = conn.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    return {
        row["name"]
        for row in rows
    }


def ensure_column(
    conn,
    table_name,
    column_name,
    definition,
):

    columns = table_columns(
        conn,
        table_name,
    )

    if column_name not in columns:

        print(
            "[DB MIGRATION] Adding "
            f"{table_name}.{column_name}"
        )

        conn.execute(
            f"ALTER TABLE {table_name} "
            f"ADD COLUMN {column_name} "
            f"{definition}"
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

            asset TEXT,
            direction TEXT,

            signal_time TEXT,

            entry REAL,
            tp REAL,
            sl REAL,

            status TEXT,

            exit_time TEXT,
            exit_price REAL,
            exit_reason TEXT,

            pnl_pct REAL,
            duration_seconds INTEGER,

            rr REAL,

            trendline_1h TEXT,
            trendline_5m TEXT,

            breakout_1h_time TEXT,
            breakout_5m_time TEXT,

            tp_source TEXT,
            sl_source TEXT,

            created_at TEXT
        )
        """
    )

    # --------------------------------------------------------
    # Migration
    # --------------------------------------------------------

    ensure_column(
        conn,
        "signals",
        "asset",
        "TEXT",
    )

    ensure_column(
        conn,
        "signals",
        "direction",
        "TEXT",
    )

    ensure_column(
        conn,
        "signals",
        "signal_time",
        "TEXT",
    )

    ensure_column(
        conn,
        "signals",
        "entry",
        "REAL",
    )

    ensure_column(
        conn,
        "signals",
        "tp",
        "REAL",
    )

    ensure_column(
        conn,
        "signals",
        "sl",
        "REAL",
    )

    ensure_column(
        conn,
        "signals",
        "status",
        "TEXT",
    )

    ensure_column(
        conn,
        "signals",
        "exit_time",
        "TEXT",
    )

    ensure_column(
        conn,
        "signals",
        "exit_price",
        "REAL",
    )

    ensure_column(
        conn,
        "signals",
        "exit_reason",
        "TEXT",
    )

    ensure_column(
        conn,
        "signals",
        "pnl_pct",
        "REAL",
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
        "rr",
        "REAL",
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

    ensure_column(
        conn,
        "signals",
        "created_at",
        "TEXT",
    )

    # --------------------------------------------------------
    # Performance baseline
    # --------------------------------------------------------

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS performance_baseline (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            reset_key TEXT NOT NULL,
            reset_time TEXT NOT NULL
        )
        """
    )

    row = conn.execute(
        """
        SELECT reset_key, reset_time
        FROM performance_baseline
        WHERE id = 1
        """
    ).fetchone()

    if row is None:

        reset_time = iso_utc()

        conn.execute(
            """
            INSERT INTO performance_baseline
            (
                id,
                reset_key,
                reset_time
            )
            VALUES
            (
                1,
                ?,
                ?
            )
            """,
            (
                PERFORMANCE_RESET_KEY,
                reset_time,
            ),
        )

        print(
            "[PERFORMANCE] Initial baseline:",
            reset_time,
        )

    elif row["reset_key"] != PERFORMANCE_RESET_KEY:

        reset_time = iso_utc()

        conn.execute(
            """
            UPDATE performance_baseline
            SET
                reset_key = ?,
                reset_time = ?
            WHERE id = 1
            """,
            (
                PERFORMANCE_RESET_KEY,
                reset_time,
            ),
        )

        print(
            "[PERFORMANCE] Baseline reset:",
            reset_time,
        )

    conn.commit()

    conn.close()


# ============================================================
# PERFORMANCE BASELINE
# ============================================================

def get_performance_reset_time():

    conn = db_connect()

    row = conn.execute(
        """
        SELECT reset_time
        FROM performance_baseline
        WHERE id = 1
        """
    ).fetchone()

    conn.close()

    if row:
        return row["reset_time"]

    return iso_utc()


# ============================================================
# KRAKEN CONTRACT
# ============================================================

def contract_for(asset):

    return f"PF_{asset}USD"


# ============================================================
# KRAKEN OHLC
#
# IMPORTANT FIX:
#
# Old:
#     resolution = "60"
#     resolution = "5"
#
# Correct current Kraken API:
#     resolution = "1h"
#     resolution = "5m"
# ============================================================

def fetch_ohlc(
    asset,
    resolution,
    limit=500,
):

    contract = contract_for(asset)

    resolution_map = {
        "1": "1m",
        "5": "5m",
        "15": "15m",
        "30": "30m",
        "60": "1h",
        "1h": "1h",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "4h": "4h",
        "12h": "12h",
        "1d": "1d",
        "1w": "1w",
    }

    kraken_resolution = (
        resolution_map.get(
            str(resolution)
        )
    )

    if not kraken_resolution:

        print(
            f"[OHLC ERROR] Invalid resolution: "
            f"{resolution}"
        )

        return None

    url = KRAKEN_CHART_URL.format(
        symbol=contract,
        resolution=kraken_resolution,
    )

    params = {
        "count": int(limit),
    }

    try:

        r = requests.get(
            url,
            params=params,
            headers={
                "Accept": "application/json",
                "User-Agent": (
                    "Mozilla/5.0 "
                    "KrakenTrendlineScanner/7.1.3"
                ),
            },
            timeout=REQUEST_TIMEOUT,
        )

        if not r.ok:

            print(
                f"[OHLC ERROR] "
                f"{asset} "
                f"{kraken_resolution}: "
                f"HTTP {r.status_code}"
            )

            print(
                "[OHLC RESPONSE]",
                r.text[:500],
            )

            return None

        data = r.json()

        # ----------------------------------------------------
        # Current Kraken response:
        #
        # {
        #   "candles": [
        #       {
        #          "time": ...,
        #          "open": ...,
        #          "high": ...,
        #          "low": ...,
        #          "close": ...,
        #          "volume": ...
        #       }
        #   ],
        #   "more_candles": ...
        # }
        # ----------------------------------------------------

        if isinstance(data, dict):

            rows = data.get(
                "candles",
                [],
            )

            if not rows:

                rows = data.get(
                    "data",
                    [],
                )

        elif isinstance(data, list):

            rows = data

        else:

            rows = []

        if not rows:

            print(
                f"[OHLC EMPTY] "
                f"{asset} "
                f"{kraken_resolution}"
            )

            return None

        parsed = []

        for row in rows:

            if isinstance(row, dict):

                ts = row.get(
                    "time"
                )

                op = row.get(
                    "open"
                )

                hi = row.get(
                    "high"
                )

                lo = row.get(
                    "low"
                )

                cl = row.get(
                    "close"
                )

                vol = row.get(
                    "volume",
                    0,
                )

            else:

                if len(row) < 6:
                    continue

                ts = row[0]
                op = row[1]
                hi = row[2]
                lo = row[3]
                cl = row[4]
                vol = row[5]

            try:

                parsed.append(
                    [
                        float(ts),
                        float(op),
                        float(hi),
                        float(lo),
                        float(cl),
                        float(vol or 0),
                    ]
                )

            except Exception:

                continue

        if not parsed:

            print(
                f"[OHLC PARSE ERROR] "
                f"{asset} "
                f"{kraken_resolution}"
            )

            return None

        df = pd.DataFrame(
            parsed,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ],
        )

        # Kraken currently returns milliseconds.
        if df["timestamp"].max() > 10_000_000_000:
            unit = "ms"
        else:
            unit = "s"

        df["time"] = pd.to_datetime(
            df["timestamp"],
            unit=unit,
            utc=True,
        )

        df = (
            df.sort_values("time")
            .drop_duplicates("time")
            .reset_index(drop=True)
        )

        # ----------------------------------------------------
        # CLOSED CANDLES ONLY
        # ----------------------------------------------------

        now = pd.Timestamp.now(
            tz="UTC"
        )

        candle_delta_map = {
            "1m": pd.Timedelta(minutes=1),
            "5m": pd.Timedelta(minutes=5),
            "15m": pd.Timedelta(minutes=15),
            "30m": pd.Timedelta(minutes=30),
            "1h": pd.Timedelta(hours=1),
            "4h": pd.Timedelta(hours=4),
            "12h": pd.Timedelta(hours=12),
            "1d": pd.Timedelta(days=1),
            "1w": pd.Timedelta(weeks=1),
        }

        candle_delta = candle_delta_map[
            kraken_resolution
        ]

        df = df[
            df["time"] + candle_delta <= now
        ].copy()

        if limit:

            df = df.tail(
                int(limit)
            ).copy()

        df.reset_index(
            drop=True,
            inplace=True,
        )

        if len(df) == 0:

            print(
                f"[OHLC CLOSED EMPTY] "
                f"{asset} "
                f"{kraken_resolution}"
            )

            return None

        return df

    except Exception as e:

        print(
            f"[OHLC ERROR] "
            f"{asset} "
            f"{kraken_resolution}: "
            f"{e}"
        )

        return None


# ============================================================
# TICKERS
# ============================================================

def fetch_tickers():

    try:

        r = requests.get(
            KRAKEN_TICKER_URL,
            headers={
                "Accept": "application/json",
                "User-Agent": (
                    "Mozilla/5.0 "
                    "KrakenTrendlineScanner/7.1.3"
                ),
            },
            timeout=REQUEST_TIMEOUT,
        )

        if not r.ok:

            print(
                "[TICKER ERROR]",
                r.status_code,
                r.text[:500],
            )

            return {}

        data = r.json()

        rows = (
            data.get("tickers")
            if isinstance(data, dict)
            else None
        )

        if not rows:
            return {}

        result = {}

        for row in rows:

            symbol = (
                row.get("symbol")
                or row.get("product_id")
                or row.get("tag")
            )

            if not symbol:
                continue

            last = (
                row.get("last")
                or row.get("lastPrice")
                or row.get("markPrice")
                or row.get("price")
            )

            if last is None:
                continue

            try:
                result[
                    symbol
                ] = float(last)

            except Exception:
                pass

        return result

    except Exception as e:

        print(
            "[TICKER ERROR]",
            e,
        )

        return {}


def get_current_price(
    asset,
    tickers=None,
):

    contract = contract_for(asset)

    if (
        tickers
        and contract in tickers
    ):

        return float(
            tickers[contract]
        )

    # Fallback to latest closed 5M
    df = fetch_ohlc(
        asset,
        "5m",
        limit=2,
    )

    if df is not None and len(df):

        return float(
            df.iloc[-1]["close"]
        )

    return None


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(df):

    highs = []
    lows = []

    n = len(df)

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT,
    ):

        h = float(
            df.iloc[i]["high"]
        )

        l = float(
            df.iloc[i]["low"]
        )

        left_highs = df.iloc[
            i - PIVOT_LEFT:i
        ]["high"]

        right_highs = df.iloc[
            i + 1:
            i + PIVOT_RIGHT + 1
        ]["high"]

        left_lows = df.iloc[
            i - PIVOT_LEFT:i
        ]["low"]

        right_lows = df.iloc[
            i + 1:
            i + PIVOT_RIGHT + 1
        ]["low"]

        if (
            h >= left_highs.max()
            and h >= right_highs.max()
        ):

            highs.append(
                {
                    "index": i,
                    "time": df.iloc[i]["time"],
                    "price": h,
                }
            )

        if (
            l <= left_lows.min()
            and l <= right_lows.min()
        ):

            lows.append(
                {
                    "index": i,
                    "time": df.iloc[i]["time"],
                    "price": l,
                }
            )

    return highs, lows


# ============================================================
# TRENDLINE
# ============================================================

def line_value(
    p1,
    p2,
    x,
):

    x1 = p1["index"]
    x2 = p2["index"]

    y1 = p1["price"]
    y2 = p2["price"]

    if x2 == x1:
        return y1

    slope = (
        (y2 - y1)
        / (x2 - x1)
    )

    return (
        y1
        + slope
        * (x - x1)
    )


def trendline_error_pct(
    p1,
    p2,
    pivot,
):

    expected = line_value(
        p1,
        p2,
        pivot["index"],
    )

    if expected == 0:
        return 999.0

    return (
        abs(
            pivot["price"]
            - expected
        )
        / abs(expected)
        * 100.0
    )


def find_valid_trendline(
    df,
    direction,
):

    highs, lows = find_pivots(df)

    if direction == "SHORT":
        pivots = highs
    else:
        pivots = lows

    if len(pivots) < 2:
        return None

    best = None

    for i in range(
        len(pivots) - 2,
        -1,
        -1,
    ):

        for j in range(
            i + 1,
            len(pivots),
        ):

            p1 = pivots[i]
            p2 = pivots[j]

            if (
                p2["index"]
                <= p1["index"]
            ):
                continue

            bars = (
                p2["index"]
                - p1["index"]
            )

            if bars < MIN_TRENDLINE_BARS:
                continue

            valid = True

            for k in range(
                i + 1,
                len(pivots),
            ):

                p = pivots[k]

                if (
                    p["index"]
                    <= p2["index"]
                ):
                    continue

                err = trendline_error_pct(
                    p1,
                    p2,
                    p,
                )

                if (
                    err
                    <= TRENDLINE_TOLERANCE_PCT
                ):

                    valid = False
                    break

            if not valid:
                continue

            best = {
                "direction": direction,
                "p1": p1,
                "p2": p2,
            }

            break

        if best:
            break

    return best


# ============================================================
# BREAKOUT
# ============================================================

def find_trendline_breakout(
    df,
    trendline,
    direction,
):

    if trendline is None:
        return None

    p1 = trendline["p1"]
    p2 = trendline["p2"]

    start = max(
        p2["index"] + 1,
        PIVOT_RIGHT,
    )

    buffer = (
        BREAKOUT_BUFFER_PCT
        / 100.0
    )

    for i in range(
        start,
        len(df),
    ):

        row = df.iloc[i]

        line = line_value(
            p1,
            p2,
            i,
        )

        close = float(
            row["close"]
        )

        if direction == "LONG":

            if (
                close
                > line * (1.0 + buffer)
            ):

                return {
                    "index": i,
                    "time": row["time"],
                    "price": close,
                    "line": line,
                }

        else:

            if (
                close
                < line * (1.0 - buffer)
            ):

                return {
                    "index": i,
                    "time": row["time"],
                    "price": close,
                    "line": line,
                }

    return None


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(
    df,
    index,
):

    if index < RVOL_LOOKBACK:
        return None

    current_volume = float(
        df.iloc[index]["volume"]
    )

    previous = df.iloc[
        index - RVOL_LOOKBACK:
        index
    ]["volume"]

    avg_volume = float(
        previous.mean()
    )

    if avg_volume <= 0:
        return None

    return (
        current_volume
        / avg_volume
    )


# ============================================================
# 5M CONFIRMED LEVELS
# ============================================================

def get_confirmed_5m_levels(
    df,
    breakout_index,
    direction,
):

    highs, lows = find_pivots(df)

    confirmed_highs = [
        p for p in highs
        if p["index"] < breakout_index
    ]

    confirmed_lows = [
        p for p in lows
        if p["index"] < breakout_index
    ]

    entry = float(
        df.iloc[
            breakout_index
        ]["close"]
    )

    if direction == "LONG":

        sl_pivot = (
            confirmed_lows[-1]
            if confirmed_lows
            else None
        )

        future_targets = [
            p
            for p in confirmed_highs
            if p["price"] > entry
        ]

        future_targets.sort(
            key=lambda x: x["price"]
        )

        tp_pivot = (
            future_targets[0]
            if future_targets
            else None
        )

        return (
            tp_pivot,
            sl_pivot,
        )

    else:

        sl_pivot = (
            confirmed_highs[-1]
            if confirmed_highs
            else None
        )

        future_targets = [
            p
            for p in confirmed_lows
            if p["price"] < entry
        ]

        future_targets.sort(
            key=lambda x: x["price"],
            reverse=True,
        )

        tp_pivot = (
            future_targets[0]
            if future_targets
            else None
        )

        return (
            tp_pivot,
            sl_pivot,
        )


def calculate_tp_sl(
    entry,
    direction,
    tp_pivot,
    sl_pivot,
):

    if (
        tp_pivot is None
        or sl_pivot is None
    ):
        return None

    if direction == "LONG":

        tp = float(
            tp_pivot["price"]
        )

        sl = float(
            sl_pivot["price"]
        )

        sl *= (
            1.0
            - SL_BUFFER_PCT
        )

        reward = tp - entry

        risk = entry - sl

    else:

        tp = float(
            tp_pivot["price"]
        )

        sl = float(
            sl_pivot["price"]
        )

        sl *= (
            1.0
            + SL_BUFFER_PCT
        )

        reward = entry - tp

        risk = sl - entry

    if reward <= 0:
        return None

    if risk <= 0:
        return None

    rr = reward / risk

    if rr < MIN_RR:
        return None

    return {
        "tp": tp,
        "sl": sl,
        "rr": rr,

        "tp_source": (
            "5M confirmed pivot "
            + tehran_time(
                tp_pivot["time"]
            )
        ),

        "sl_source": (
            "5M confirmed pivot "
            + tehran_time(
                sl_pivot["time"]
            )
        ),
    }


# ============================================================
# DUPLICATE / OPEN TRADE
# ============================================================

def has_open_trade(
    conn,
    asset,
    direction,
):

    row = conn.execute(
        """
        SELECT id
        FROM signals
        WHERE asset = ?
          AND direction = ?
          AND status = 'OPEN'
        ORDER BY id DESC
        LIMIT 1
        """,
        (
            asset,
            direction,
        ),
    ).fetchone()

    return row is not None


def signal_already_exists(
    conn,
    asset,
    direction,
    signal_time,
):

    row = conn.execute(
        """
        SELECT id
        FROM signals
        WHERE asset = ?
          AND direction = ?
          AND signal_time = ?
        LIMIT 1
        """,
        (
            asset,
            direction,
            signal_time,
        ),
    ).fetchone()

    return row is not None


# ============================================================
# INSERT SIGNAL
# ============================================================

def insert_signal(signal):

    conn = db_connect()

    try:

        if has_open_trade(
            conn,
            signal["asset"],
            signal["direction"],
        ):

            conn.close()

            return (
                False,
                None,
            )

        if signal_already_exists(
            conn,
            signal["asset"],
            signal["direction"],
            signal["signal_time"],
        ):

            conn.close()

            return (
                False,
                None,
            )

        cur = conn.execute(
            """
            INSERT INTO signals
            (
                asset,
                direction,
                signal_time,

                entry,
                tp,
                sl,

                status,

                rr,

                trendline_1h,
                trendline_5m,

                breakout_1h_time,
                breakout_5m_time,

                tp_source,
                sl_source,

                created_at
            )
            VALUES
            (
                ?,
                ?,
                ?,

                ?,
                ?,
                ?,

                'OPEN',

                ?,

                ?,
                ?,

                ?,
                ?,

                ?,
                ?,

                ?
            )
            """,
            (
                signal["asset"],
                signal["direction"],
                signal["signal_time"],

                signal["entry"],
                signal["tp"],
                signal["sl"],

                signal["rr"],

                signal["trendline_1h"],
                signal["trendline_5m"],

                signal["breakout_1h_time"],
                signal["breakout_5m_time"],

                signal["tp_source"],
                signal["sl_source"],

                iso_utc(),
            ),
        )

        conn.commit()

        signal_id = cur.lastrowid

        conn.close()

        return (
            True,
            signal_id,
        )

    except Exception:

        conn.rollback()
        conn.close()

        raise


# ============================================================
# PNL
# ============================================================

def calculate_pnl_pct(
    entry,
    exit_price,
    direction,
):

    if (
        entry is None
        or exit_price is None
    ):
        return None

    entry = float(entry)

    exit_price = float(
        exit_price
    )

    if entry <= 0:
        return None

    if direction == "LONG":

        return (
            (exit_price - entry)
            / entry
            * 100.0
        )

    return (
        (entry - exit_price)
        / entry
        * 100.0
    )


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    conn,
    row,
    exit_price,
    reason,
):

    exit_time = iso_utc()

    entry = float(
        row["entry"]
    )

    direction = row["direction"]

    pnl = calculate_pnl_pct(
        entry,
        exit_price,
        direction,
    )

    entry_dt = parse_datetime(
        row["signal_time"]
    )

    exit_dt = parse_datetime(
        exit_time
    )

    duration_seconds = None

    if (
        entry_dt
        and exit_dt
    ):

        duration_seconds = int(
            (
                exit_dt
                - entry_dt
            ).total_seconds()
        )

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
            reason,
            pnl,
            duration_seconds,
            row["id"],
        ),
    )

    conn.commit()

    return {
        "id": row["id"],
        "asset": row["asset"],
        "direction": direction,
        "entry": entry,
        "tp": row["tp"],
        "sl": row["sl"],
        "exit_price": exit_price,
        "exit_time": exit_time,
        "reason": reason,
        "pnl_pct": pnl,
        "duration_seconds": duration_seconds,
    }


# ============================================================
# IMMEDIATE CLOSE MESSAGE
# ============================================================

def send_close_message(
    trade,
):

    direction = trade["direction"]

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    if trade["reason"] == "TP":
        result_emoji = "✅"
    elif trade["reason"] == "SL":
        result_emoji = "❌"
    else:
        result_emoji = "⏱"

    text = (
        "<b>📕 TRADE CLOSED</b>\n\n"

        f"{emoji} "
        f"<b>{direction} "
        f"{trade['asset']}</b>\n\n"

        f"Entry: "
        f"<code>{fmt_price(trade['entry'])}</code>\n"

        f"Exit: "
        f"<code>{fmt_price(trade['exit_price'])}</code>\n"

        f"TP: "
        f"<code>{fmt_price(trade['tp'])}</code>\n"

        f"SL: "
        f"<code>{fmt_price(trade['sl'])}</code>\n\n"

        f"Result: "
        f"{result_emoji} "
        f"<b>{trade['reason']}</b>\n"

        f"PnL: "
        f"<b>{fmt_pct(trade['pnl_pct'])}</b>\n"

        f"Duration: "
        f"<b>{fmt_duration(trade['duration_seconds'])}</b>\n"

        f"Exit Time: "
        f"{tehran_time(trade['exit_time'])}"
    )

    telegram_send(text)


# ============================================================
# RECONCILE OPEN TRADES
#
# This executes BEFORE scanning new signals.
# Any close is immediately reported.
# ============================================================

def reconcile_open_trades(
    tickers=None,
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

        current = get_current_price(
            asset,
            tickers,
        )

        if current is None:
            continue

        # ----------------------------------------------------
        # TIME EXIT
        # ----------------------------------------------------

        entry_time = parse_datetime(
            row["signal_time"]
        )

        if entry_time:

            age_hours = (
                now - entry_time
            ).total_seconds() / 3600.0

            if age_hours >= MAX_HOLD_HOURS:

                trade = close_trade(
                    conn,
                    row,
                    current,
                    "TIME",
                )

                closed.append(
                    trade
                )

                # IMMEDIATE
                send_close_message(
                    trade
                )

                continue

        tp = (
            float(row["tp"])
            if row["tp"] is not None
            else None
        )

        sl = (
            float(row["sl"])
            if row["sl"] is not None
            else None
        )

        if (
            tp is None
            or sl is None
        ):
            continue

        exit_reason = None

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if direction == "LONG":

            if current >= tp:

                exit_reason = "TP"

            elif current <= sl:

                exit_reason = "SL"

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        else:

            if current <= tp:

                exit_reason = "TP"

            elif current >= sl:

                exit_reason = "SL"

        if exit_reason:

            trade = close_trade(
                conn,
                row,
                current,
                exit_reason,
            )

            closed.append(
                trade
            )

            # IMMEDIATE
            send_close_message(
                trade
            )

    conn.close()

    return closed


# ============================================================
# OPEN TRADES REPORT
# ============================================================

def send_open_trades(
    tickers=None,
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

    conn.close()

    if not rows:

        telegram_send(
            "<b>📂 OPEN TRADES</b>\n\n"
            "No open trades."
        )

        return

    lines = [
        "<b>📂 OPEN TRADES</b>",
        "",
    ]

    now = utc_now()

    for row in rows:

        asset = row["asset"]

        direction = row["direction"]

        current = get_current_price(
            asset,
            tickers,
        )

        pnl = None

        if current is not None:

            pnl = calculate_pnl_pct(
                row["entry"],
                current,
                direction,
            )

        entry_dt = parse_datetime(
            row["signal_time"]
        )

        duration = None

        if entry_dt:

            duration = int(
                (
                    now - entry_dt
                ).total_seconds()
            )

        emoji = (
            "🟢"
            if direction == "LONG"
            else "🔴"
        )

        lines.append(
            f"{emoji} "
            f"<b>{direction} {asset}</b>"
        )

        lines.append(
            "Entry: "
            f"<code>{fmt_price(row['entry'])}</code>"
        )

        lines.append(
            "Current: "
            f"<code>{fmt_price(current)}</code> "
            f"({fmt_pct(pnl)})"
        )

        lines.append(
            "TP: "
            f"<code>{fmt_price(row['tp'])}</code>"
        )

        lines.append(
            "SL: "
            f"<code>{fmt_price(row['sl'])}</code>"
        )

        lines.append(
            "Duration: "
            f"<b>{fmt_duration(duration)}</b>"
        )

        lines.append("")

    telegram_send(
        "\n".join(lines)
    )


# ============================================================
# PERFORMANCE
# ============================================================

def get_performance():

    reset_time = (
        get_performance_reset_time()
    )

    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM signals
        WHERE status = 'CLOSED'
          AND exit_time IS NOT NULL
          AND exit_time >= ?
        ORDER BY id ASC
        """,
        (
            reset_time,
        ),
    ).fetchall()

    conn.close()

    total = len(rows)

    wins = 0

    losses = 0

    breakeven = 0

    tp_count = 0

    sl_count = 0

    time_count = 0

    net_pnl = 0.0

    for row in rows:

        pnl = row["pnl_pct"]

        if pnl is not None:

            pnl = float(pnl)

            net_pnl += pnl

            if pnl > 0:

                wins += 1

            elif pnl < 0:

                losses += 1

            else:

                breakeven += 1

        reason = row[
            "exit_reason"
        ]

        if reason == "TP":

            tp_count += 1

        elif reason == "SL":

            sl_count += 1

        elif reason == "TIME":

            time_count += 1

    decided = (
        wins
        + losses
    )

    if decided:

        win_rate = (
            wins
            / decided
            * 100.0
        )

    else:

        win_rate = 0.0

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "tp": tp_count,
        "sl": sl_count,
        "time": time_count,
        "net_pnl": net_pnl,
        "win_rate": win_rate,
        "reset_time": reset_time,
    }


def send_performance():

    p = get_performance()

    text = (
        "<b>📈 PERFORMANCE</b>\n\n"

        f"Total Trades: "
        f"<b>{p['total']}</b>\n"

        f"Wins: "
        f"<b>{p['wins']}</b>\n"

        f"Losses: "
        f"<b>{p['losses']}</b>\n"

        f"Breakeven: "
        f"<b>{p['breakeven']}</b>\n"

        f"Win Rate: "
        f"<b>{p['win_rate']:.2f}%</b>\n\n"

        f"TP: "
        f"<b>{p['tp']}</b>\n"

        f"SL: "
        f"<b>{p['sl']}</b>\n"

        f"TIME: "
        f"<b>{p['time']}</b>\n\n"

        f"Net PnL: "
        f"<b>{p['net_pnl']:+.2f}%</b>\n\n"

        f"Since: "
        f"{tehran_time(p['reset_time'])}"
    )

    telegram_send(text)


# ============================================================
# TRENDLINE TEXT
# ============================================================

def trendline_text(
    trendline,
):

    if not trendline:
        return "-"

    p1 = trendline["p1"]

    p2 = trendline["p2"]

    return (
        f"P1={fmt_price(p1['price'])}"
        f"@{tehran_time(p1['time'])}; "
        f"P2={fmt_price(p2['price'])}"
        f"@{tehran_time(p2['time'])}"
    )


# ============================================================
# CHART
# ============================================================

def create_chart(
    df,
    trendline,
    breakout,
    direction,
    entry,
    tp,
    sl,
):

    try:

        chart_df = (
            df.tail(150)
            .copy()
            .reset_index(drop=True)
        )

        fig, ax = plt.subplots(
            figsize=(14, 7)
        )

        for i, row in chart_df.iterrows():

            op = float(row["open"])

            hi = float(row["high"])

            lo = float(row["low"])

            cl = float(row["close"])

            ax.plot(
                [i, i],
                [lo, hi],
                linewidth=1,
            )

            bottom = min(
                op,
                cl,
            )

            height = abs(
                cl - op
            )

            if height == 0:

                height = max(
                    abs(hi - lo) * 0.01,
                    1e-12,
                )

            ax.bar(
                i,
                height,
                bottom=bottom,
                width=0.55,
                align="center",
            )

        # ----------------------------------------------------
        # Trendline
        # ----------------------------------------------------

        if trendline:

            p1 = trendline["p1"]

            p2 = trendline["p2"]

            matches1 = chart_df.index[
                chart_df["time"]
                == p1["time"]
            ].tolist()

            matches2 = chart_df.index[
                chart_df["time"]
                == p2["time"]
            ].tolist()

            if (
                matches1
                and matches2
            ):

                x1 = matches1[0]

                x2 = matches2[0]

                y1 = p1["price"]

                y2 = p2["price"]

                if x2 != x1:

                    slope = (
                        (y2 - y1)
                        / (x2 - x1)
                    )

                    line_x = np.arange(
                        x1,
                        len(chart_df),
                    )

                    line_y = (
                        y1
                        + slope
                        * (line_x - x1)
                    )

                    ax.plot(
                        line_x,
                        line_y,
                        linewidth=2,
                        label="5M Trendline",
                    )

                ax.scatter(
                    [x1],
                    [y1],
                    s=70,
                    label="P1",
                    zorder=5,
                )

                ax.scatter(
                    [x2],
                    [y2],
                    s=70,
                    label="P2",
                    zorder=5,
                )

        # ----------------------------------------------------
        # Breakout
        # ----------------------------------------------------

        if breakout:

            matches = chart_df.index[
                chart_df["time"]
                == breakout["time"]
            ].tolist()

            if matches:

                bx = matches[0]

                by = float(
                    chart_df.iloc[bx]["close"]
                )

                ax.scatter(
                    [bx],
                    [by],
                    s=120,
                    marker="*",
                    label="Breakout",
                    zorder=6,
                )

        # ----------------------------------------------------
        # Entry / TP / SL
        # ----------------------------------------------------

        ax.axhline(
            entry,
            linewidth=1.5,
            linestyle="--",
            label="Entry",
        )

        ax.axhline(
            tp,
            linewidth=1.5,
            linestyle="--",
            label="TP",
        )

        ax.axhline(
            sl,
            linewidth=1.5,
            linestyle="--",
            label="SL",
        )

        ax.set_title(
            f"{direction} | "
            f"5M Trendline Signal"
        )

        ax.set_xlabel(
            "5M Candles"
        )

        ax.set_ylabel(
            "Price"
        )

        ax.grid(
            True,
            alpha=0.2,
        )

        ax.legend(
            loc="best"
        )

        plt.tight_layout()

        output = io.BytesIO()

        plt.savefig(
            output,
            format="png",
            dpi=150,
            bbox_inches="tight",
        )

        plt.close(fig)

        output.seek(0)

        return output.getvalue()

    except Exception as e:

        print(
            "[CHART ERROR]",
            e,
        )

        return None


# ============================================================
# IMMEDIATE NEW SIGNAL
# ============================================================

def send_new_signal(
    signal,
    chart_bytes=None,
):

    direction = signal[
        "direction"
    ]

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    text = (
        "<b>🚨 NEW SIGNAL</b>\n\n"

        f"{emoji} "
        f"<b>{direction} "
        f"{signal['asset']}</b>\n\n"

        f"Entry: "
        f"<code>{fmt_price(signal['entry'])}</code>\n"

        f"TP: "
        f"<code>{fmt_price(signal['tp'])}</code>\n"

        f"SL: "
        f"<code>{fmt_price(signal['sl'])}</code>\n"

        f"RR: "
        f"<b>{signal['rr']:.2f}</b>\n"

        f"RVOL: "
        f"<b>{signal['rvol']:.2f}</b>\n\n"

        f"1H Breakout: "
        f"{tehran_time(signal['breakout_1h_time'])}\n"

        f"5M Breakout: "
        f"{tehran_time(signal['breakout_5m_time'])}\n\n"

        f"TP Source: "
        f"5M confirmed pivot\n"

        f"SL Source: "
        f"5M confirmed pivot\n"

        f"Time: "
        f"{tehran_time(signal['signal_time'])}"
    )

    if chart_bytes:

        telegram_photo(
            chart_bytes,
            text,
        )

    else:

        telegram_send(text)


# ============================================================
# SCAN ONE ASSET
# ============================================================

def scan_asset(
    asset,
    tickers,
):

    global stats

    try:

        # ----------------------------------------------------
        # 1H
        # ----------------------------------------------------

        df1h = fetch_ohlc(
            asset,
            "1h",
            limit=500,
        )

        if (
            df1h is None
            or len(df1h) < 50
        ):

            return None

        # ----------------------------------------------------
        # LONG / SHORT TRENDLINES
        # ----------------------------------------------------

        long_tl_1h = (
            find_valid_trendline(
                df1h,
                "LONG",
            )
        )

        short_tl_1h = (
            find_valid_trendline(
                df1h,
                "SHORT",
            )
        )

        candidates = []

        if long_tl_1h:

            candidates.append(
                (
                    "LONG",
                    long_tl_1h,
                )
            )

        if short_tl_1h:

            candidates.append(
                (
                    "SHORT",
                    short_tl_1h,
                )
            )

        if not candidates:
            return None

        stats[
            "h1_valid_trendlines"
        ] += len(candidates)

        # ----------------------------------------------------
        # H1 BREAKOUT
        # ----------------------------------------------------

        best_candidate = None

        for (
            direction,
            tl1h,
        ) in candidates:

            breakout1h = (
                find_trendline_breakout(
                    df1h,
                    tl1h,
                    direction,
                )
            )

            if breakout1h is None:
                continue

            if best_candidate is None:

                best_candidate = {
                    "direction": direction,
                    "trendline_1h": tl1h,
                    "breakout_1h": breakout1h,
                }

            else:

                old_time = (
                    best_candidate[
                        "breakout_1h"
                    ]["time"]
                )

                new_time = (
                    breakout1h["time"]
                )

                if new_time > old_time:

                    best_candidate = {
                        "direction": direction,
                        "trendline_1h": tl1h,
                        "breakout_1h": breakout1h,
                    }

        if best_candidate is None:
            return None

        direction = (
            best_candidate[
                "direction"
            ]
        )

        trendline_1h = (
            best_candidate[
                "trendline_1h"
            ]
        )

        breakout1h = (
            best_candidate[
                "breakout_1h"
            ]
        )

        stats[
            "h1_breakouts"
        ] += 1

        # ----------------------------------------------------
        # 5M
        # ----------------------------------------------------

        df5m = fetch_ohlc(
            asset,
            "5m",
            limit=1000,
        )

        if (
            df5m is None
            or len(df5m) < 100
        ):

            return None

        # ----------------------------------------------------
        # 5M TRENDLINE
        # ----------------------------------------------------

        trendline5m = (
            find_valid_trendline(
                df5m,
                direction,
            )
        )

        if trendline5m is None:
            return None

        stats[
            "m5_trendlines"
        ] += 1

        # ----------------------------------------------------
        # 5M BREAKOUT
        # ----------------------------------------------------

        breakout5m = (
            find_trendline_breakout(
                df5m,
                trendline5m,
                direction,
            )
        )

        if breakout5m is None:
            return None

        # Must happen after H1 breakout
        if (
            breakout5m["time"]
            <= breakout1h["time"]
        ):

            return None

        stats[
            "m5_breakouts"
        ] += 1

        breakout_index = (
            breakout5m["index"]
        )

        # ----------------------------------------------------
        # RVOL
        # ----------------------------------------------------

        rvol = calculate_rvol(
            df5m,
            breakout_index,
        )

        if rvol is None:
            return None

        if rvol < RVOL_MIN:
            return None

        stats[
            "volume_accepted"
        ] += 1

        # ----------------------------------------------------
        # ENTRY
        # ----------------------------------------------------

        entry = float(
            df5m.iloc[
                breakout_index
            ]["close"]
        )

        signal_time = iso_utc(
            breakout5m[
                "time"
            ].to_pydatetime()
        )

        # ----------------------------------------------------
        # CONFIRMED 5M TP / SL
        # ----------------------------------------------------

        (
            tp_pivot,
            sl_pivot,
        ) = get_confirmed_5m_levels(
            df5m,
            breakout_index,
            direction,
        )

        levels = calculate_tp_sl(
            entry,
            direction,
            tp_pivot,
            sl_pivot,
        )

        if levels is None:

            stats[
                "level_rejects"
            ] += 1

            return None

        stats[
            "valid_tp_sl"
        ] += 1

        # ----------------------------------------------------
        # SIGNAL
        # ----------------------------------------------------

        signal = {
            "asset": asset,

            "direction": direction,

            "signal_time": signal_time,

            "entry": entry,

            "tp": levels["tp"],

            "sl": levels["sl"],

            "rr": levels["rr"],

            "rvol": rvol,

            "trendline_1h":
                trendline_text(
                    trendline_1h
                ),

            "trendline_5m":
                trendline_text(
                    trendline5m
                ),

            "breakout_1h_time":
                iso_utc(
                    breakout1h[
                        "time"
                    ].to_pydatetime()
                ),

            "breakout_5m_time":
                iso_utc(
                    breakout5m[
                        "time"
                    ].to_pydatetime()
                ),

            "tp_source":
                levels[
                    "tp_source"
                ],

            "sl_source":
                levels[
                    "sl_source"
                ],
        }

        # ----------------------------------------------------
        # INSERT
        # ----------------------------------------------------

        inserted, signal_id = (
            insert_signal(
                signal
            )
        )

        if not inserted:
            return None

        stats[
            "new_signals"
        ] += 1

        signal["id"] = signal_id

        # ----------------------------------------------------
        # CHART
        # ----------------------------------------------------

        chart_bytes = create_chart(
            df5m,
            trendline5m,
            breakout5m,
            direction,
            signal["entry"],
            signal["tp"],
            signal["sl"],
        )

        # ----------------------------------------------------
        # IMMEDIATE NEW SIGNAL
        # ----------------------------------------------------

        send_new_signal(
            signal,
            chart_bytes,
        )

        return signal

    except Exception as e:

        stats[
            "errors"
        ] += 1

        print(
            f"[ASSET ERROR] "
            f"{asset}: {e}"
        )

        traceback.print_exc()

        return None


# ============================================================
# SCAN SUMMARY
# ============================================================

def send_scan_summary():

    text = (
        "<b>📊 KRAKEN TRENDLINE SCANNER</b>\n"

        f"Version: <b>{VERSION}</b>\n"

        "Mode: <b>PAPER ONLY</b>\n"

        f"Time: "
        f"{tehran_time(iso_utc())}\n\n"

        "<b>🔎 SCAN SUMMARY</b>\n\n"

        f"Assets Scanned: "
        f"<b>{stats['assets_scanned']}</b>\n"

        f"1H Valid Trendlines: "
        f"<b>{stats['h1_valid_trendlines']}</b>\n"

        f"1H Breakouts: "
        f"<b>{stats['h1_breakouts']}</b>\n"

        f"5M Trendlines: "
        f"<b>{stats['m5_trendlines']}</b>\n"

        f"5M Breakouts: "
        f"<b>{stats['m5_breakouts']}</b>\n"

        f"Volume Accepted: "
        f"<b>{stats['volume_accepted']}</b>\n"

        f"Valid TP/SL: "
        f"<b>{stats['valid_tp_sl']}</b>\n"

        f"New Signals: "
        f"<b>{stats['new_signals']}</b>\n"

        f"Closed Trades: "
        f"<b>{stats['closed_trades']}</b>\n"

        f"Level Rejects: "
        f"<b>{stats['level_rejects']}</b>\n"

        f"Errors: "
        f"<b>{stats['errors']}</b>"
    )

    telegram_send(
        text
    )


# ============================================================
# MAIN SCAN
# ============================================================

def scan():

    global stats

    # --------------------------------------------------------
    # Current-run statistics reset
    # --------------------------------------------------------

    reset_stats()

    print(
        "=" * 70
    )

    print(
        "KRAKEN FUTURES "
        "TRENDLINE SCANNER "
        f"VERSION {VERSION}"
    )

    print(
        "REAL_TRADING =",
        REAL_TRADING,
    )

    print(
        "=" * 70
    )

    if REAL_TRADING:

        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    # --------------------------------------------------------
    # DB
    # --------------------------------------------------------

    init_db()

    # --------------------------------------------------------
    # Tickers
    # --------------------------------------------------------

    tickers = fetch_tickers()

    # --------------------------------------------------------
    # FIRST:
    # Reconcile open trades
    #
    # CLOSED notifications happen immediately.
    # --------------------------------------------------------

    closed_trades = (
        reconcile_open_trades(
            tickers
        )
    )

    stats[
        "closed_trades"
    ] = len(
        closed_trades
    )

    # --------------------------------------------------------
    # SCAN ASSETS
    # --------------------------------------------------------

    signals_created = []

    for asset in ASSETS:

        stats[
            "assets_scanned"
        ] += 1

        print(
            f"[SCAN] {asset}"
        )

        signal = scan_asset(
            asset,
            tickers,
        )

        if signal:

            signals_created.append(
                signal
            )

            if (
                len(signals_created)
                >= MAX_SIGNALS_PER_SCAN
            ):

                break

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    send_open_trades(
        tickers
    )

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    send_performance()

    # --------------------------------------------------------
    # CURRENT SCAN SUMMARY
    # --------------------------------------------------------

    send_scan_summary()

    print(
        "=" * 70
    )

    print(
        "SCAN COMPLETE"
    )

    print(
        stats
    )

    print(
        "=" * 70
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        scan()

    except Exception as e:

        print(
            "[FATAL ERROR]"
        )

        print(e)

        traceback.print_exc()

        try:

            telegram_send(
                "<b>🚨 SCANNER FATAL ERROR</b>\n\n"
                f"<code>{str(e)[:3500]}</code>"
            )

        except Exception:
            pass

        raise
