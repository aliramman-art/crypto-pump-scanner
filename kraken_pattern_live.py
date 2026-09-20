# ============================================================
# KRAKEN FUTURES PATTERN LIVE SIGNAL SCANNER
# VERSION 6.0.0
# ============================================================
#
# IMPORTANT:
# - PAPER ONLY
# - REAL_TRADING = False
# - Existing DB is preserved
# - 40 Kraken Futures assets
# - Closed candles only
# - No lookahead
#
# STRATEGY:
# 1H Pattern
#      ->
# 1H closed-candle breakout
#      ->
# first 5M retest
#      ->
# 5M confirmation
#      ->
# Entry at confirmation candle CLOSE
#
# VERSION 6.0.0:
# - Moderate pattern-quality filters
# - Better visual Double Top / Bottom detection
# - Better visual Head & Shoulders detection
# - Minimum pattern depth
# - Minimum separation between pattern sides
# - Only ONE best new signal is inserted per scan
# - Existing DB is preserved
# - Telegram NEW SIGNAL now includes chart image
# - Chart shows:
#       Pattern
#       Neckline
#       Breakout
#       Retest
#       Confirmation
#       Entry
#       TP
#       SL
# - Existing trade logic preserved
# - Opposite direction remains allowed
# - REAL_TRADING remains False
#
# ============================================================

import os
import time
import sqlite3
import tempfile
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

VERSION = "6.0.0"

REAL_TRADING = False

DB_FILE = "kraken_pattern_live_v52.db"

TP_PCT = 0.01
SL_PCT = 0.01
RR = 1.0

MAX_HOLD_HOURS = 48

PATTERN_LOOKBACK = 240
RECENT_PATTERN_HOURS = 48

BREAKOUT_LOOKAHEAD = 24
RETEST_LOOKAHEAD = 24
CONFIRMATION_LOOKAHEAD = 6

MAX_ENTRY_AGE_MINUTES = 10

PERIODIC_REPORT_SECONDS = 900

HTTP_RETRIES = 3


# ============================================================
# MODERATE PATTERN QUALITY FILTERS
# ============================================================
#
# These are intentionally moderate.
#
# Goal:
# - reduce obvious noise
# - preserve reasonable signal frequency
# - avoid extremely strict filtering
#
# The actual target of approximately one new signal per
# 5-minute scan is achieved mainly by selecting ONE best
# candidate per scan, rather than destroying the strategy
# with very strict pattern filters.
# ============================================================

DOUBLE_LEVEL_TOLERANCE = 0.0075
# 0.75% maximum difference between the two tops/bottoms

MIN_PATTERN_DEPTH_PCT = 0.004
# 0.40% minimum move from peaks to valley
# or valley to neckline area

MIN_PATTERN_SEPARATION_BARS = 2

HS_SHOULDER_TOLERANCE = 0.015
# 1.5% maximum difference between shoulders

HS_MIN_HEAD_ADVANTAGE_PCT = 0.003
# head must exceed shoulders by at least 0.30%

MIN_HS_DEPTH_PCT = 0.004


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
    "XBT": "PF_XBTUSD",
    "ETH": "PF_ETHUSD",
    "SOL": "PF_SOLUSD",
    "XRP": "PF_XRPUSD",
    "LTC": "PF_LTCUSD",
    "DOGE": "PF_DOGEUSD",
    "ADA": "PF_ADAUSD",
    "LINK": "PF_LINKUSD",
    "AVAX": "PF_AVAXUSD",
    "DOT": "PF_DOTUSD",
    "BNB": "PF_BNBUSD",
    "TRX": "PF_TRXUSD",
    "UNI": "PF_UNIUSD",
    "AAVE": "PF_AAVEUSD",
    "SUI": "PF_SUIUSD",
    "NEAR": "PF_NEARUSD",
    "ATOM": "PF_ATOMUSD",
    "FIL": "PF_FILUSD",
    "ARB": "PF_ARBUSD",
    "OP": "PF_OPUSD",
    "BCH": "PF_BCHUSD",
    "ETC": "PF_ETCUSD",
    "XMR": "PF_XMRUSD",
    "XLM": "PF_XLMUSD",
    "ALGO": "PF_ALGOUSD",
    "ICP": "PF_ICPUSD",
    "INJ": "PF_INJUSD",
    "TIA": "PF_TIAUSD",
    "SEI": "PF_SEIUSD",
    "RUNE": "PF_RUNEUSD",
    "CRV": "PF_CRVUSD",
    "HBAR": "PF_HBARUSD",
    "HYPE": "PF_HYPEUSD",
    "ENA": "PF_ENAUSD",
    "FET": "PF_FETUSD",
    "KAS": "PF_KASUSD",
    "STX": "PF_STXUSD",
    "JUP": "PF_JUPUSD",
    "PEPE": "PF_PEPEUSD",
    "WIF": "PF_WIFUSD",
}


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)


# ============================================================
# STATS
# ============================================================

STATS = {
    "patterns": 0,
    "recent_patterns": 0,
    "breakouts": 0,
    "old_breakouts": 0,
    "retests": 0,
    "confirmations": 0,
    "valid_entries": 0,

    "unique_entries": 0,

    "skipped_existing": 0,
    "skipped_open": 0,
    "insert_ignored": 0,

    "new_signals": 0,

    "closed_live": 0,
    "historical_reconciled": 0,
}


# ============================================================
# TIME
# ============================================================

def now_ts():
    return int(time.time())


def format_time(ts):

    if not ts:
        return "-"

    try:

        dt = datetime.fromtimestamp(
            int(ts),
            tz=timezone.utc
        )

        return dt.strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

    except Exception:

        return "-"


def duration_text(
    start_ts,
    end_ts=None
):

    if not start_ts:
        return "-"

    if end_ts is None:
        end_ts = now_ts()

    seconds = max(
        0,
        int(end_ts) - int(start_ts)
    )

    days = seconds // 86400
    seconds %= 86400

    hours = seconds // 3600
    seconds %= 3600

    minutes = seconds // 60

    if days > 0:
        return f"{days}d {hours}h {minutes}m"

    if hours > 0:
        return f"{hours}h {minutes}m"

    return f"{minutes}m"


# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()


def http_get(
    url,
    params=None
):

    last_error = None

    for attempt in range(
        1,
        HTTP_RETRIES + 1
    ):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=20
            )

            response.raise_for_status()

            return response

        except Exception as exc:

            last_error = exc

            print(
                f"HTTP error attempt "
                f"{attempt}/{HTTP_RETRIES}: {exc}"
            )

            if attempt < HTTP_RETRIES:
                time.sleep(1)

    raise last_error


# ============================================================
# TELEGRAM TEXT
# ============================================================

def telegram_send(message):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):

        print(
            "Telegram credentials are missing."
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=20
        )

        response.raise_for_status()

        print(
            "Telegram message sent."
        )

        return True

    except Exception as exc:

        print(
            f"Telegram send failed: {exc}"
        )

        return False


# ============================================================
# TELEGRAM PHOTO
# ============================================================

def telegram_send_photo(
    image_path,
    caption
):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):

        print(
            "Telegram credentials are missing."
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    try:

        with open(
            image_path,
            "rb"
        ) as image_file:

            files = {
                "photo": image_file
            }

            data = {
                "chat_id":
                    TELEGRAM_CHAT_ID,

                "caption":
                    caption,

                "parse_mode":
                    "HTML",
            }

            response = SESSION.post(
                url,
                data=data,
                files=files,
                timeout=30
            )

            response.raise_for_status()

        print(
            "Telegram chart sent."
        )

        return True

    except Exception as exc:

        print(
            f"Telegram chart send failed: "
            f"{exc}"
        )

        return False


# ============================================================
# DATABASE
# ============================================================

def get_connection():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.row_factory = sqlite3.Row

    return conn


def get_table_columns(
    conn,
    table
):

    rows = conn.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    return {
        row["name"]
        for row in rows
    }


def ensure_column(
    conn,
    table,
    column,
    definition
):

    columns = get_table_columns(
        conn,
        table
    )

    if column not in columns:

        print(
            f"Adding missing column: "
            f"{table}.{column}"
        )

        conn.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN {column} {definition}"
        )

        conn.commit()


def init_db(conn):

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset TEXT,
            direction TEXT,
            pattern TEXT,
            signal_key TEXT UNIQUE,
            pattern_time INTEGER,
            breakout_time INTEGER,
            retest_time INTEGER,
            confirmation_time INTEGER,
            entry_time INTEGER,
            entry_price REAL,
            sl_price REAL,
            tp_price REAL,
            current_price REAL,
            status TEXT,
            exit_time INTEGER,
            exit_price REAL,
            exit_reason TEXT,
            created_at INTEGER,
            updated_at INTEGER
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

    conn.commit()

    columns_to_ensure = [
        ("symbol", "TEXT"),
        ("asset", "TEXT"),
        ("direction", "TEXT"),
        ("pattern", "TEXT"),
        ("signal_key", "TEXT"),
        ("pattern_time", "INTEGER"),
        ("breakout_time", "INTEGER"),
        ("retest_time", "INTEGER"),
        ("confirmation_time", "INTEGER"),
        ("entry_time", "INTEGER"),
        ("entry_price", "REAL"),
        ("sl_price", "REAL"),
        ("tp_price", "REAL"),
        ("current_price", "REAL"),
        ("status", "TEXT"),
        ("exit_time", "INTEGER"),
        ("exit_price", "REAL"),
        ("exit_reason", "TEXT"),
        ("created_at", "INTEGER"),
        ("updated_at", "INTEGER"),
    ]

    for column, definition in columns_to_ensure:

        ensure_column(
            conn,
            "trades",
            column,
            definition
        )

    columns = get_table_columns(
        conn,
        "trades"
    )

    if (
        "symbol" in columns
        and "asset" in columns
    ):

        try:

            conn.execute(
                """
                UPDATE trades
                SET symbol = asset
                WHERE symbol IS NULL
                  AND asset IS NOT NULL
                """
            )

            conn.commit()

            print(
                "Legacy symbol values "
                "backfilled from asset."
            )

        except Exception as exc:

            print(
                f"Symbol backfill warning: "
                f"{exc}"
            )

    conn.execute(
        """
        UPDATE trades
        SET created_at = COALESCE(
            created_at,
            entry_time,
            strftime('%s','now')
        )
        WHERE created_at IS NULL
        """
    )

    conn.execute(
        """
        UPDATE trades
        SET updated_at = COALESCE(
            updated_at,
            created_at,
            entry_time,
            strftime('%s','now')
        )
        WHERE updated_at IS NULL
        """
    )

    conn.commit()

    print(
        "Database schema verified."
    )


# ============================================================
# DATABASE DIAGNOSTICS
# ============================================================

def print_unique_indexes(conn):

    print("")
    print(
        "DATABASE UNIQUE INDEX CHECK"
    )
    print("--------------------------------")

    try:

        indexes = conn.execute(
            "PRAGMA index_list(trades)"
        ).fetchall()

        found = False

        for idx in indexes:

            idx_name = idx["name"]
            is_unique = int(
                idx["unique"]
            )

            if not is_unique:
                continue

            found = True

            print(
                f"UNIQUE INDEX: {idx_name}"
            )

            columns = conn.execute(
                f"PRAGMA index_info('{idx_name}')"
            ).fetchall()

            names = [
                col["name"]
                for col in columns
            ]

            print(
                f"  columns: {names}"
            )

        if not found:

            print(
                "No UNIQUE indexes found."
            )

    except Exception as exc:

        print(
            f"Could not inspect indexes: {exc}"
        )

    print("--------------------------------")
    print("")


# ============================================================
# META
# ============================================================

def get_meta(
    conn,
    key
):

    row = conn.execute(
        """
        SELECT value
        FROM scanner_meta
        WHERE key = ?
        """,
        (key,)
    ).fetchone()

    if not row:
        return None

    return row["value"]


def set_meta(
    conn,
    key,
    value
):

    conn.execute(
        """
        INSERT INTO scanner_meta(key, value)
        VALUES (?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value = excluded.value
        """,
        (
            key,
            str(value)
        )
    )

    conn.commit()


# ============================================================
# KRAKEN CONTRACTS
# ============================================================

def get_kraken_contracts():

    url = (
        "https://futures.kraken.com/"
        "derivatives/api/v3/instruments"
    )

    response = http_get(
        url
    )

    data = response.json()

    instruments = data.get(
        "instruments",
        []
    )

    print(
        f"Kraken contracts loaded: "
        f"{len(instruments)}"
    )

    available = []

    for asset in ASSETS:

        contract = CONTRACTS.get(
            asset
        )

        if not contract:
            continue

        found = False

        for item in instruments:

            if item.get("symbol") == contract:

                found = True
                break

        if found:

            available.append(
                asset
            )

    print(
        f"Available assets: "
        f"{len(available)}/{len(ASSETS)}"
    )

    missing = [
        asset
        for asset in ASSETS
        if asset not in available
    ]

    if missing:

        print(
            "Missing contracts:",
            ", ".join(missing)
        )

    return available


# ============================================================
# LIVE PRICES
# ============================================================

def get_live_prices():

    url = (
        "https://futures.kraken.com/"
        "derivatives/api/v3/tickers"
    )

    response = http_get(
        url
    )

    data = response.json()

    tickers = data.get(
        "tickers",
        []
    )

    prices = {}

    for ticker in tickers:

        symbol = ticker.get(
            "symbol"
        )

        if not symbol:
            continue

        for asset, contract in CONTRACTS.items():

            if symbol == contract:

                try:

                    prices[asset] = float(
                        ticker["last"]
                    )

                except Exception:
                    pass

                break

    return prices


# ============================================================
# CANDLES
# ============================================================

def get_candles(
    contract,
    interval,
    count=300
):

    url = (
        "https://futures.kraken.com/"
        f"api/charts/v1/trade/"
        f"{contract}/{interval}"
    )

    params = {
        "count": count
    }

    response = http_get(
        url,
        params=params
    )

    data = response.json()

    candles = None

    if isinstance(data, dict):

        for key in (
            "candles",
            "data",
            "results",
            "ohlc"
        ):

            if key in data:

                candles = data[key]

                break

    elif isinstance(data, list):

        candles = data

    if not candles:

        return pd.DataFrame()

    rows = []

    for candle in candles:

        if isinstance(candle, dict):

            timestamp = (
                candle.get("time")
                or candle.get("timestamp")
            )

            open_price = candle.get(
                "open"
            )

            high = candle.get(
                "high"
            )

            low = candle.get(
                "low"
            )

            close = candle.get(
                "close"
            )

            volume = candle.get(
                "volume",
                0
            )

        else:

            if len(candle) < 5:
                continue

            timestamp = candle[0]
            open_price = candle[1]
            high = candle[2]
            low = candle[3]
            close = candle[4]

            volume = (
                candle[5]
                if len(candle) > 5
                else 0
            )

        try:

            ts = int(
                float(timestamp)
            )

            if ts > 10_000_000_000:
                ts //= 1000

            rows.append(
                {
                    "timestamp": ts,
                    "open": float(
                        open_price
                    ),
                    "high": float(
                        high
                    ),
                    "low": float(
                        low
                    ),
                    "close": float(
                        close
                    ),
                    "volume": float(
                        volume
                    ),
                }
            )

        except Exception:
            continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        rows
    )

    df = (
        df
        .drop_duplicates(
            subset=["timestamp"]
        )
        .sort_values(
            "timestamp"
        )
        .reset_index(
            drop=True
        )
    )

    interval_seconds = (
        3600
        if interval == "1h"
        else 300
    )

    current = now_ts()

    df = df[
        (
            df["timestamp"]
            + interval_seconds
            <= current
        )
    ].copy()

    return df.reset_index(
        drop=True
    )


# ============================================================
# HELPERS FOR PATTERN DETECTION
# ============================================================

def pct_distance(a, b):

    denominator = max(
        abs(float(a)),
        1e-12
    )

    return abs(
        float(a) - float(b)
    ) / denominator


def find_max_index(
    df,
    column
):

    if df.empty:
        return None

    return df[column].idxmax()


def find_min_index(
    df,
    column
):

    if df.empty:
        return None

    return df[column].idxmin()


# ============================================================
# PATTERN DETECTION
# ============================================================

def detect_patterns(df):

    patterns = []

    if df is None or len(df) < 20:
        return patterns

    for i in range(
        5,
        len(df) - 5
    ):

        current = df.iloc[i]

        left = df.iloc[
            i - 3:i
        ]

        right = df.iloc[
            i + 1:i + 4
        ]

        left_high = float(
            left["high"].max()
        )

        right_high = float(
            right["high"].max()
        )

        left_low = float(
            left["low"].min()
        )

        right_low = float(
            right["low"].min()
        )

        current_high = float(
            current["high"]
        )

        current_low = float(
            current["low"]
        )

        current_close = float(
            current["close"]
        )

        # ====================================================
        # DOUBLE TOP
        # ====================================================

        top_similarity = pct_distance(
            left_high,
            right_high
        )

        if top_similarity <= DOUBLE_LEVEL_TOLERANCE:

            valley_low = min(
                left_low,
                current_low,
                right_low
            )

            average_top = (
                left_high
                + right_high
            ) / 2.0

            depth = (
                average_top
                - valley_low
            ) / max(
                average_top,
                1e-12
            )

            # Current candle should participate in the valley.
            valley_position_ok = (
                current_low
                <= average_top
                * (
                    1
                    - MIN_PATTERN_DEPTH_PCT
                )
            )

            # Require meaningful depth.
            depth_ok = (
                depth
                >= MIN_PATTERN_DEPTH_PCT
            )

            # Avoid the two sides being adjacent.
            separation_ok = (
                (
                    i - 3
                )
                >=
                MIN_PATTERN_SEPARATION_BARS
            )

            if (
                valley_position_ok
                and depth_ok
                and separation_ok
            ):

                left_peak_idx = (
                    i - 3
                    + int(
                        left[
                            "high"
                        ].values.argmax()
                    )
                )

                right_peak_idx = (
                    i + 1
                    + int(
                        right[
                            "high"
                        ].values.argmax()
                    )
                )

                neckline = valley_low

                patterns.append(
                    {
                        "pattern":
                            "Double Top",

                        "direction":
                            "SHORT",

                        "pattern_time":
                            int(
                                current[
                                    "timestamp"
                                ]
                            ),

                        "level":
                            float(
                                neckline
                            ),

                        "index":
                            i,

                        "left_peak_index":
                            left_peak_idx,

                        "right_peak_index":
                            right_peak_idx,

                        "left_peak_price":
                            left_high,

                        "right_peak_price":
                            right_high,

                        "quality":
                            float(
                                depth
                                * 100
                            ),
                    }
                )

        # ====================================================
        # DOUBLE BOTTOM
        # ====================================================

        bottom_similarity = pct_distance(
            left_low,
            right_low
        )

        if bottom_similarity <= DOUBLE_LEVEL_TOLERANCE:

            rally_high = max(
                left_high,
                current_high,
                right_high
            )

            average_bottom = (
                left_low
                + right_low
            ) / 2.0

            depth = (
                rally_high
                - average_bottom
            ) / max(
                average_bottom,
                1e-12
            )

            valley_position_ok = (
                current_high
                >= average_bottom
                * (
                    1
                    + MIN_PATTERN_DEPTH_PCT
                )
            )

            depth_ok = (
                depth
                >= MIN_PATTERN_DEPTH_PCT
            )

            separation_ok = (
                (
                    i - 3
                )
                >=
                MIN_PATTERN_SEPARATION_BARS
            )

            if (
                valley_position_ok
                and depth_ok
                and separation_ok
            ):

                left_bottom_idx = (
                    i - 3
                    + int(
                        left[
                            "low"
                        ].values.argmin()
                    )
                )

                right_bottom_idx = (
                    i + 1
                    + int(
                        right[
                            "low"
                        ].values.argmin()
                    )
                )

                neckline = rally_high

                patterns.append(
                    {
                        "pattern":
                            "Double Bottom",

                        "direction":
                            "LONG",

                        "pattern_time":
                            int(
                                current[
                                    "timestamp"
                                ]
                            ),

                        "level":
                            float(
                                neckline
                            ),

                        "index":
                            i,

                        "left_peak_index":
                            left_bottom_idx,

                        "right_peak_index":
                            right_bottom_idx,

                        "left_peak_price":
                            left_low,

                        "right_peak_price":
                            right_low,

                        "quality":
                            float(
                                depth
                                * 100
                            ),
                    }
                )

        # ====================================================
        # HEAD & SHOULDERS
        # ====================================================

        left_shoulder = left_high
        right_shoulder = right_high
        head = current_high

        shoulder_similarity = pct_distance(
            left_shoulder,
            right_shoulder
        )

        head_advantage = (
            head
            - max(
                left_shoulder,
                right_shoulder
            )
        ) / max(
            max(
                left_shoulder,
                right_shoulder
            ),
            1e-12
        )

        neckline = min(
            left_low,
            right_low
        )

        hs_depth = (
            head
            - neckline
        ) / max(
            head,
            1e-12
        )

        if (
            shoulder_similarity
            <= HS_SHOULDER_TOLERANCE
            and head_advantage
            >= HS_MIN_HEAD_ADVANTAGE_PCT
            and hs_depth
            >= MIN_HS_DEPTH_PCT
        ):

            left_shoulder_idx = (
                i - 3
                + int(
                    left[
                        "high"
                    ].values.argmax()
                )
            )

            right_shoulder_idx = (
                i + 1
                + int(
                    right[
                        "high"
                    ].values.argmax()
                )
            )

            patterns.append(
                {
                    "pattern":
                        "Head & Shoulders",

                    "direction":
                        "SHORT",

                    "pattern_time":
                        int(
                            current[
                                "timestamp"
                            ]
                        ),

                    "level":
                        float(
                            neckline
                        ),

                    "index":
                        i,

                    "left_peak_index":
                        left_shoulder_idx,

                    "right_peak_index":
                        right_shoulder_idx,

                    "left_peak_price":
                        left_shoulder,

                    "right_peak_price":
                        right_shoulder,

                    "head_price":
                        head,

                    "quality":
                        float(
                            (
                                head_advantage
                                + hs_depth
                            )
                            * 100
                        ),
                }
            )

        # ====================================================
        # INVERSE HEAD & SHOULDERS
        # ====================================================

        left_shoulder_low = left_low
        right_shoulder_low = right_low
        head_low = current_low

        shoulder_similarity_low = pct_distance(
            left_shoulder_low,
            right_shoulder_low
        )

        head_advantage_low = (
            min(
                left_shoulder_low,
                right_shoulder_low
            )
            - head_low
        ) / max(
            min(
                left_shoulder_low,
                right_shoulder_low
            ),
            1e-12
        )

        neckline_low = max(
            left_high,
            right_high
        )

        ihs_depth = (
            neckline_low
            - head_low
        ) / max(
            head_low,
            1e-12
        )

        if (
            shoulder_similarity_low
            <= HS_SHOULDER_TOLERANCE
            and head_advantage_low
            >= HS_MIN_HEAD_ADVANTAGE_PCT
            and ihs_depth
            >= MIN_HS_DEPTH_PCT
        ):

            left_bottom_idx = (
                i - 3
                + int(
                    left[
                        "low"
                    ].values.argmin()
                )
            )

            right_bottom_idx = (
                i + 1
                + int(
                    right[
                        "low"
                    ].values.argmin()
                )
            )

            patterns.append(
                {
                    "pattern":
                        "Inverse Head & Shoulders",

                    "direction":
                        "LONG",

                    "pattern_time":
                        int(
                            current[
                                "timestamp"
                            ]
                        ),

                    "level":
                        float(
                            neckline_low
                        ),

                    "index":
                        i,

                    "left_peak_index":
                        left_bottom_idx,

                    "right_peak_index":
                        right_bottom_idx,

                    "left_peak_price":
                        left_shoulder_low,

                    "right_peak_price":
                        right_shoulder_low,

                    "head_price":
                        head_low,

                    "quality":
                        float(
                            (
                                head_advantage_low
                                + ihs_depth
                            )
                            * 100
                        ),
                }
            )

    return patterns


# ============================================================
# BREAKOUT
# ============================================================

def find_breakout(
    df,
    pattern
):

    pattern_time = int(
        pattern["pattern_time"]
    )

    level = float(
        pattern["level"]
    )

    direction = pattern[
        "direction"
    ]

    future = df[
        df["timestamp"]
        > pattern_time
    ].copy()

    future = future.head(
        BREAKOUT_LOOKAHEAD
    )

    if future.empty:
        return None

    for _, candle in future.iterrows():

        close_price = float(
            candle["close"]
        )

        if direction == "LONG":

            if close_price > level:

                return {
                    "breakout_time":
                        int(
                            candle[
                                "timestamp"
                            ]
                        ),

                    "breakout_price":
                        close_price,

                    "level":
                        level,
                }

        else:

            if close_price < level:

                return {
                    "breakout_time":
                        int(
                            candle[
                                "timestamp"
                            ]
                        ),

                    "breakout_price":
                        close_price,

                    "level":
                        level,
                }

    return None


# ============================================================
# RETEST
# ============================================================

def find_retest(
    df5,
    breakout,
    direction
):

    breakout_time = int(
        breakout["breakout_time"]
    )

    level = float(
        breakout["level"]
    )

    future = df5[
        df5["timestamp"]
        > breakout_time
    ].copy()

    future = future.head(
        RETEST_LOOKAHEAD
    )

    if future.empty:
        return None

    for _, candle in future.iterrows():

        high = float(
            candle["high"]
        )

        low = float(
            candle["low"]
        )

        if (
            low
            <= level
            <= high
        ):

            return {
                "retest_time":
                    int(
                        candle[
                            "timestamp"
                        ]
                    ),

                "retest_price":
                    level,
            }

    return None


# ============================================================
# CONFIRMATION
# ============================================================

def find_confirmation(
    df5,
    retest,
    direction
):

    retest_time = int(
        retest["retest_time"]
    )

    current_time = now_ts()

    freshness_seconds = (
        MAX_ENTRY_AGE_MINUTES
        * 60
    )

    candidates = df5[
        (df5["timestamp"] > retest_time)
        & (df5["timestamp"] <= current_time)
        & (
            (
                current_time
                - df5["timestamp"]
            )
            <= freshness_seconds
        )
    ].copy()

    if candidates.empty:
        return None

    candidates = candidates.head(
        CONFIRMATION_LOOKAHEAD
    )

    for _, candle in candidates.iterrows():

        open_price = float(
            candle["open"]
        )

        close_price = float(
            candle["close"]
        )

        high = float(
            candle["high"]
        )

        low = float(
            candle["low"]
        )

        if direction == "LONG":

            if close_price > open_price:

                return {
                    "confirmation_time":
                        int(
                            candle[
                                "timestamp"
                            ]
                        ),

                    "entry_price":
                        close_price,

                    "high":
                        high,

                    "low":
                        low,
                }

        else:

            if close_price < open_price:

                return {
                    "confirmation_time":
                        int(
                            candle[
                                "timestamp"
                            ]
                        ),

                    "entry_price":
                        close_price,

                    "high":
                        high,

                    "low":
                        low,
                }

    return None


# ============================================================
# ENTRY
# ============================================================

def find_entry(
    df5,
    confirmation,
    direction
):

    if confirmation is None:
        return None

    entry_time = int(
        confirmation[
            "confirmation_time"
        ]
    )

    entry_price = float(
        confirmation[
            "entry_price"
        ]
    )

    if direction == "LONG":

        tp_price = (
            entry_price
            * (1 + TP_PCT)
        )

        sl_price = (
            entry_price
            * (1 - SL_PCT)
        )

    else:

        tp_price = (
            entry_price
            * (1 - TP_PCT)
        )

        sl_price = (
            entry_price
            * (1 + SL_PCT)
        )

    return {
        "entry_time":
            entry_time,

        "entry_price":
            entry_price,

        "tp_price":
            tp_price,

        "sl_price":
            sl_price,
    }


# ============================================================
# BUILD SIGNAL
# ============================================================

def build_signal(
    asset,
    df1h,
    df5
):

    patterns = detect_patterns(
        df1h
    )

    STATS["patterns"] += len(
        patterns
    )

    current_time = now_ts()

    recent_seconds = (
        RECENT_PATTERN_HOURS
        * 3600
    )

    recent_patterns = []

    for pattern in patterns:

        age = (
            current_time
            - int(
                pattern[
                    "pattern_time"
                ]
            )
        )

        if (
            age >= 0
            and age <= recent_seconds
        ):

            recent_patterns.append(
                pattern
            )

    STATS["recent_patterns"] += len(
        recent_patterns
    )

    if not recent_patterns:
        return []

    candidates = []

    for pattern in recent_patterns:

        breakout = find_breakout(
            df1h,
            pattern
        )

        if breakout is None:
            continue

        STATS["breakouts"] += 1

        breakout_age = (
            current_time
            - int(
                breakout[
                    "breakout_time"
                ]
            )
        )

        if breakout_age > (
            BREAKOUT_LOOKAHEAD
            * 3600
        ):

            STATS[
                "old_breakouts"
            ] += 1

            continue

        retest = find_retest(
            df5,
            breakout,
            pattern["direction"]
        )

        if retest is None:
            continue

        STATS["retests"] += 1

        confirmation = find_confirmation(
            df5,
            retest,
            pattern["direction"]
        )

        if confirmation is None:
            continue

        STATS["confirmations"] += 1

        entry = find_entry(
            df5,
            confirmation,
            pattern["direction"]
        )

        if entry is None:
            continue

        STATS["valid_entries"] += 1

        signal_key = (
            f"{asset}|"
            f"{pattern['pattern']}|"
            f"{pattern['direction']}|"
            f"{breakout['breakout_time']}|"
            f"{entry['entry_time']}"
        )

        signal = {
            "symbol":
                asset,

            "asset":
                asset,

            "direction":
                pattern["direction"],

            "pattern":
                pattern["pattern"],

            "signal_key":
                signal_key,

            "pattern_time":
                int(
                    pattern[
                        "pattern_time"
                    ]
                ),

            "breakout_time":
                int(
                    breakout[
                        "breakout_time"
                    ]
                ),

            "retest_time":
                int(
                    retest[
                        "retest_time"
                    ]
                ),

            "confirmation_time":
                int(
                    confirmation[
                        "confirmation_time"
                    ]
                ),

            "entry_time":
                int(
                    entry[
                        "entry_time"
                    ]
                ),

            "entry_price":
                float(
                    entry[
                        "entry_price"
                    ]
                ),

            "sl_price":
                float(
                    entry[
                        "sl_price"
                    ]
                ),

            "tp_price":
                float(
                    entry[
                        "tp_price"
                    ]
                ),

            "current_price":
                float(
                    entry[
                        "entry_price"
                    ]
                ),

            "status":
                "OPEN",

            "exit_time":
                None,

            "exit_price":
                None,

            "exit_reason":
                None,

            "created_at":
                now_ts(),

            "updated_at":
                now_ts(),
        }

        chain = {
            "pattern":
                pattern,

            "breakout":
                breakout,

            "retest":
                retest,

            "confirmation":
                confirmation,
        }

        # ----------------------------------------------------
        # Candidate quality.
        #
        # Newer entries get priority.
        # Stronger pattern quality gets priority.
        # ----------------------------------------------------

        pattern_age_hours = (
            current_time
            - int(
                pattern[
                    "pattern_time"
                ]
            )
        ) / 3600.0

        breakout_age_hours = (
            current_time
            - int(
                breakout[
                    "breakout_time"
                ]
            )
        ) / 3600.0

        quality = float(
            pattern.get(
                "quality",
                0
            )
        )

        freshness_score = max(
            0,
            48
            - pattern_age_hours
        )

        breakout_freshness = max(
            0,
            24
            - breakout_age_hours
        )

        candidate_score = (
            quality * 10
            + freshness_score
            + breakout_freshness * 2
        )

        candidates.append(
            {
                "signal":
                    signal,

                "chain":
                    chain,

                "score":
                    candidate_score,
            }
        )

    return candidates


# ============================================================
# SIGNAL EXISTS
# ============================================================

def signal_exists(
    conn,
    signal_key
):

    row = conn.execute(
        """
        SELECT id
        FROM trades
        WHERE signal_key = ?
        LIMIT 1
        """,
        (
            signal_key,
        )
    ).fetchone()

    return row is not None


# ============================================================
# OPEN TRADE EXISTS
# ============================================================

def open_trade_exists(
    conn,
    asset,
    direction
):

    row = conn.execute(
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
        )
    ).fetchone()

    return row is not None


# ============================================================
# INSERT TRADE
# ============================================================

def insert_trade(
    conn,
    signal
):

    columns = get_table_columns(
        conn,
        "trades"
    )

    signal["symbol"] = (
        signal.get("symbol")
        or signal.get("asset")
    )

    base_fields = [
        "asset",
        "direction",
        "pattern",
        "signal_key",
        "pattern_time",
        "breakout_time",
        "retest_time",
        "confirmation_time",
        "entry_time",
        "entry_price",
        "sl_price",
        "tp_price",
        "current_price",
        "status",
        "exit_time",
        "exit_price",
        "exit_reason",
        "created_at",
        "updated_at",
    ]

    insert_fields = []

    if "symbol" in columns:

        insert_fields.append(
            "symbol"
        )

    for field in base_fields:

        if field in columns:

            insert_fields.append(
                field
            )

    placeholders = ", ".join(
        ["?"] * len(insert_fields)
    )

    sql = (
        "INSERT INTO trades ("
        + ", ".join(insert_fields)
        + ") VALUES ("
        + placeholders
        + ")"
    )

    values = []

    for field in insert_fields:

        if field == "symbol":

            values.append(
                signal["symbol"]
            )

        else:

            values.append(
                signal.get(field)
            )

    try:

        print(
            f"DB INSERT ATTEMPT: "
            f"{signal['asset']} "
            f"{signal['direction']}"
        )

        print(
            f"DB symbol = "
            f"{signal['symbol']}"
        )

        print(
            f"DB columns = "
            f"{insert_fields}"
        )

        cursor = conn.execute(
            sql,
            tuple(values)
        )

        conn.commit()

        rowcount = cursor.rowcount

        if rowcount > 0:

            print(
                f"DB INSERT SUCCESS: "
                f"{signal['asset']} "
                f"{signal['direction']} "
                f"{signal['pattern']} "
                f"key={signal['signal_key']}"
            )

        return rowcount

    except sqlite3.IntegrityError as exc:

        conn.rollback()

        print("")
        print(
            "!!! DB INSERT BLOCKED !!!"
        )

        print(
            f"Asset: {signal['asset']}"
        )

        print(
            f"Symbol: {signal['symbol']}"
        )

        print(
            f"Direction: {signal['direction']}"
        )

        print(
            f"Pattern: {signal['pattern']}"
        )

        print(
            f"Signal key: {signal['signal_key']}"
        )

        print(
            f"SQLite IntegrityError: {exc}"
        )

        print(
            "!!! END DB INSERT DIAGNOSTIC !!!"
        )

        print("")

        return 0

    except Exception as exc:

        conn.rollback()

        print(
            f"Unexpected DB insert error: "
            f"{exc}"
        )

        return 0


# ============================================================
# NEW SIGNAL CAPTION
# ============================================================

def new_signal_caption(
    signal
):

    direction = signal[
        "direction"
    ]

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    entry = float(
        signal["entry_price"]
    )

    sl = float(
        signal["sl_price"]
    )

    tp = float(
        signal["tp_price"]
    )

    return (
        "🚨 <b>NEW SIGNAL</b>\n"
        "\n"
        f"{emoji} <b>{signal['asset']} "
        f"{direction}</b>\n"
        f"📌 Pattern: "
        f"{signal['pattern']}\n"
        f"💰 Entry: "
        f"{entry:.8g}\n"
        f"🛑 SL: "
        f"{sl:.8g} "
        f"({SL_PCT * 100:.2f}%)\n"
        f"🎯 TP: "
        f"{tp:.8g} "
        f"({TP_PCT * 100:.2f}%)\n"
        f"⚙️ RR: {RR:.1f}\n"
        f"🕐 Entry Time: "
        f"{format_time(signal['entry_time'])}"
    )


# ============================================================
# CHART GENERATION
# ============================================================

def generate_pattern_chart(
    signal,
    chain,
    df1h
):

    pattern = chain[
        "pattern"
    ]

    breakout = chain[
        "breakout"
    ]

    retest = chain[
        "retest"
    ]

    confirmation = chain[
        "confirmation"
    ]

    pattern_time = int(
        pattern["pattern_time"]
    )

    entry_time = int(
        signal["entry_time"]
    )

    # --------------------------------------------------------
    # Display approximately 36 candles around the setup.
    # --------------------------------------------------------

    df = df1h.copy()

    if df.empty:
        return None

    entry_idx_candidates = df.index[
        df["timestamp"]
        <= entry_time
    ]

    if len(entry_idx_candidates) > 0:

        entry_idx = int(
            entry_idx_candidates[-1]
        )

    else:

        entry_idx = len(df) - 1

    start_idx = max(
        0,
        entry_idx - 36
    )

    end_idx = min(
        len(df),
        entry_idx + 4
    )

    chart_df = df.iloc[
        start_idx:end_idx
    ].copy()

    if chart_df.empty:
        return None

    # --------------------------------------------------------
    # Matplotlib
    # --------------------------------------------------------

    fig, ax = plt.subplots(
        figsize=(13, 7)
    )

    x = np.arange(
        len(chart_df)
    )

    # --------------------------------------------------------
    # Candles
    # --------------------------------------------------------

    candle_width = 0.65

    for pos, (_, row) in enumerate(
        chart_df.iterrows()
    ):

        open_price = float(
            row["open"]
        )

        high = float(
            row["high"]
        )

        low = float(
            row["low"]
        )

        close = float(
            row["close"]
        )

        if close >= open_price:
            candle_color = "green"

        else:
            candle_color = "red"

        ax.vlines(
            pos,
            low,
            high,
            linewidth=1
        )

        body_bottom = min(
            open_price,
            close
        )

        body_height = abs(
            close
            - open_price
        )

        if body_height <= 0:
            body_height = (
                max(
                    high - low,
                    1e-12
                )
                * 0.01
            )

        ax.bar(
            pos,
            body_height,
            bottom=body_bottom,
            width=candle_width,
            color=candle_color,
            alpha=0.75
        )

    # --------------------------------------------------------
    # Helpers
    # --------------------------------------------------------

    def chart_pos(timestamp):

        values = chart_df[
            "timestamp"
        ].values

        if len(values) == 0:
            return None

        distances = np.abs(
            values.astype(np.int64)
            - int(timestamp)
        )

        idx = int(
            distances.argmin()
        )

        return idx

    # --------------------------------------------------------
    # Pattern markers
    # --------------------------------------------------------

    left_idx = pattern.get(
        "left_peak_index"
    )

    right_idx = pattern.get(
        "right_peak_index"
    )

    if left_idx is not None:

        left_timestamp = int(
            df.iloc[
                int(left_idx)
            ]["timestamp"]
        )

        left_pos = chart_pos(
            left_timestamp
        )

    else:

        left_pos = None

    if right_idx is not None:

        right_timestamp = int(
            df.iloc[
                int(right_idx)
            ]["timestamp"]
        )

        right_pos = chart_pos(
            right_timestamp
        )

    else:

        right_pos = None

    pattern_pos = chart_pos(
        pattern_time
    )

    breakout_pos = chart_pos(
        breakout[
            "breakout_time"
        ]
    )

    retest_pos = chart_pos(
        retest[
            "retest_time"
        ]
    )

    confirmation_pos = chart_pos(
        confirmation[
            "confirmation_time"
        ]
    )

    entry_pos = chart_pos(
        signal[
            "entry_time"
        ]
    )

    # --------------------------------------------------------
    # Pattern points
    # --------------------------------------------------------

    if left_pos is not None:

        left_price = float(
            pattern[
                "left_peak_price"
            ]
        )

        ax.scatter(
            left_pos,
            left_price,
            s=90,
            marker="^"
            if pattern[
                "direction"
            ] == "SHORT"
            else "v"
        )

        ax.annotate(
            "Left",
            (
                left_pos,
                left_price
            ),
            xytext=(0, 12),
            textcoords="offset points",
            ha="center",
            fontsize=9
        )

    if right_pos is not None:

        right_price = float(
            pattern[
                "right_peak_price"
            ]
        )

        ax.scatter(
            right_pos,
            right_price,
            s=90,
            marker="^"
            if pattern[
                "direction"
            ] == "SHORT"
            else "v"
        )

        ax.annotate(
            "Right",
            (
                right_pos,
                right_price
            ),
            xytext=(0, 12),
            textcoords="offset points",
            ha="center",
            fontsize=9
        )

    # --------------------------------------------------------
    # Head
    # --------------------------------------------------------

    if (
        pattern["pattern"]
        in (
            "Head & Shoulders",
            "Inverse Head & Shoulders"
        )
    ):

        head_price = float(
            pattern[
                "head_price"
            ]
        )

        ax.scatter(
            pattern_pos,
            head_price,
            s=110,
            marker="^"
            if pattern[
                "direction"
            ] == "SHORT"
            else "v"
        )

        ax.annotate(
            "HEAD",
            (
                pattern_pos,
                head_price
            ),
            xytext=(0, 15),
            textcoords="offset points",
            ha="center",
            fontsize=10,
            fontweight="bold"
        )

    # --------------------------------------------------------
    # Pattern time
    # --------------------------------------------------------

    if pattern_pos is not None:

        ax.axvline(
            pattern_pos,
            linestyle=":",
            linewidth=1
        )

    # --------------------------------------------------------
    # Neckline
    # --------------------------------------------------------

    neckline = float(
        pattern["level"]
    )

    ax.axhline(
        neckline,
        linestyle="--",
        linewidth=1.5,
        label="Neckline"
    )

    # --------------------------------------------------------
    # Breakout
    # --------------------------------------------------------

    if breakout_pos is not None:

        ax.scatter(
            breakout_pos,
            float(
                breakout[
                    "breakout_price"
                ]
            ),
            s=100,
            marker="*"
        )

        ax.annotate(
            "BREAKOUT",
            (
                breakout_pos,
                float(
                    breakout[
                        "breakout_price"
                    ]
                )
            ),
            xytext=(0, 18),
            textcoords="offset points",
            ha="center",
            fontsize=9,
            fontweight="bold"
        )

    # --------------------------------------------------------
    # Retest
    # --------------------------------------------------------

    if retest_pos is not None:

        ax.scatter(
            retest_pos,
            neckline,
            s=80,
            marker="o"
        )

        ax.annotate(
            "RETEST",
            (
                retest_pos,
                neckline
            ),
            xytext=(0, -20),
            textcoords="offset points",
            ha="center",
            fontsize=9,
            fontweight="bold"
        )

    # --------------------------------------------------------
    # Confirmation
    # --------------------------------------------------------

    if confirmation_pos is not None:

        confirmation_price = float(
            confirmation[
                "entry_price"
            ]
        )

        ax.scatter(
            confirmation_pos,
            confirmation_price,
            s=80,
            marker="D"
        )

        ax.annotate(
            "CONFIRM",
            (
                confirmation_pos,
                confirmation_price
            ),
            xytext=(0, 18),
            textcoords="offset points",
            ha="center",
            fontsize=9
        )

    # --------------------------------------------------------
    # Entry
    # --------------------------------------------------------

    entry_price = float(
        signal["entry_price"]
    )

    if entry_pos is not None:

        ax.scatter(
            entry_pos,
            entry_price,
            s=120,
            marker="X"
        )

        ax.annotate(
            "ENTRY",
            (
                entry_pos,
                entry_price
            ),
            xytext=(8, 8),
            textcoords="offset points",
            fontsize=10,
            fontweight="bold"
        )

    # --------------------------------------------------------
    # TP / SL
    # --------------------------------------------------------

    tp = float(
        signal["tp_price"]
    )

    sl = float(
        signal["sl_price"]
    )

    ax.axhline(
        tp,
        linestyle=":",
        linewidth=1.2,
        label="TP"
    )

    ax.axhline(
        sl,
        linestyle=":",
        linewidth=1.2,
        label="SL"
    )

    # --------------------------------------------------------
    # Title
    # --------------------------------------------------------

    ax.set_title(
        f"{signal['asset']} "
        f"{signal['direction']} | "
        f"{signal['pattern']} | "
        f"1H Pattern"
    )

    ax.set_ylabel(
        "Price"
    )

    ax.set_xlabel(
        "Candles"
    )

    ax.grid(
        alpha=0.20
    )

    # --------------------------------------------------------
    # X labels
    # --------------------------------------------------------

    label_positions = list(
        range(
            0,
            len(chart_df),
            max(
                1,
                len(chart_df) // 8
            )
        )
    )

    labels = []

    for pos in label_positions:

        ts = int(
            chart_df.iloc[
                pos
            ]["timestamp"]
        )

        labels.append(
            datetime.fromtimestamp(
                ts,
                tz=timezone.utc
            ).strftime(
                "%m-%d %H:%M"
            )
        )

    ax.set_xticks(
        label_positions
    )

    ax.set_xticklabels(
        labels,
        rotation=45,
        ha="right"
    )

    ax.legend(
        loc="best"
    )

    fig.tight_layout()

    # --------------------------------------------------------
    # Temporary PNG
    # --------------------------------------------------------

    fd, path = tempfile.mkstemp(
        suffix=".png"
    )

    os.close(
        fd
    )

    fig.savefig(
        path,
        dpi=150,
        bbox_inches="tight"
    )

    plt.close(
        fig
    )

    return path


# ============================================================
# SEND NEW SIGNAL + CHART
# ============================================================

def send_new_signal(
    signal,
    chain,
    df1h
):

    caption = new_signal_caption(
        signal
    )

    chart_path = None

    try:

        chart_path = generate_pattern_chart(
            signal,
            chain,
            df1h
        )

        if chart_path:

            sent = telegram_send_photo(
                chart_path,
                caption
            )

            if sent:
                return True

            # Fallback:
            # If image failed, still send the signal text.
            return telegram_send(
                caption
            )

        return telegram_send(
            caption
        )

    except Exception as exc:

        print(
            f"Chart generation/send error: "
            f"{exc}"
        )

        return telegram_send(
            caption
        )

    finally:

        if (
            chart_path
            and os.path.exists(
                chart_path
            )
        ):

            try:

                os.remove(
                    chart_path
                )

            except Exception:
                pass


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    conn,
    trade_id,
    exit_price,
    exit_reason,
    exit_time=None
):

    if exit_time is None:
        exit_time = now_ts()

    conn.execute(
        """
        UPDATE trades
        SET
            status = 'CLOSED',
            exit_time = ?,
            exit_price = ?,
            exit_reason = ?,
            current_price = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            int(exit_time),
            float(exit_price),
            exit_reason,
            float(exit_price),
            now_ts(),
            int(trade_id),
        )
    )

    conn.commit()


# ============================================================
# CLOSE MESSAGE
# ============================================================

def close_message(
    trade
):

    direction = trade[
        "direction"
    ]

    entry = float(
        trade["entry_price"]
    )

    exit_price = float(
        trade["exit_price"]
    )

    if direction == "LONG":

        pct = (
            (
                exit_price
                - entry
            )
            / entry
            * 100
        )

    else:

        pct = (
            (
                entry
                - exit_price
            )
            / entry
            * 100
        )

    result_emoji = (
        "🟢"
        if pct >= 0
        else "🔴"
    )

    duration = duration_text(
        trade["entry_time"],
        trade["exit_time"]
    )

    return (
        "🔔 <b>TRADE CLOSED</b>\n"
        "\n"
        f"{result_emoji} "
        f"<b>{trade['asset']} "
        f"{direction}</b>\n"
        f"📌 Pattern: "
        f"{trade['pattern']}\n"
        f"💰 Entry: "
        f"{entry:.8g}\n"
        f"💵 Exit: "
        f"{exit_price:.8g}\n"
        f"📈 Result: "
        f"{pct:+.2f}%\n"
        f"📍 Reason: "
        f"{trade['exit_reason']}\n"
        f"⏱ Duration: "
        f"{duration}\n"
        f"🕐 Exit Time: "
        f"{format_time(trade['exit_time'])}"
    )


# ============================================================
# LIVE TRADE RECONCILIATION
# ============================================================

def reconcile_open_trades(
    conn,
    prices
):

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY entry_time ASC
        """
    ).fetchall()

    for trade in rows:

        asset = trade[
            "asset"
        ]

        price = prices.get(
            asset
        )

        if price is None:
            continue

        entry = float(
            trade["entry_price"]
        )

        tp = float(
            trade["tp_price"]
        )

        sl = float(
            trade["sl_price"]
        )

        direction = trade[
            "direction"
        ]

        exit_reason = None
        exit_price = None

        if direction == "LONG":

            if price <= sl:

                exit_reason = "SL"
                exit_price = sl

            elif price >= tp:

                exit_reason = "TP"
                exit_price = tp

        else:

            if price >= sl:

                exit_reason = "SL"
                exit_price = sl

            elif price <= tp:

                exit_reason = "TP"
                exit_price = tp

        if exit_reason is None:

            age_hours = (
                now_ts()
                - int(
                    trade[
                        "entry_time"
                    ]
                )
            ) / 3600

            if age_hours >= MAX_HOLD_HOURS:

                exit_reason = "TIME"
                exit_price = price

        conn.execute(
            """
            UPDATE trades
            SET
                current_price = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                float(price),
                now_ts(),
                int(trade["id"]),
            )
        )

        conn.commit()

        if exit_reason:

            close_trade(
                conn,
                trade["id"],
                exit_price,
                exit_reason
            )

            closed = conn.execute(
                """
                SELECT *
                FROM trades
                WHERE id = ?
                """,
                (
                    int(
                        trade["id"]
                    ),
                )
            ).fetchone()

            telegram_send(
                close_message(
                    closed
                )
            )

            STATS[
                "closed_live"
            ] += 1


# ============================================================
# HISTORICAL RECONCILIATION
# ============================================================

def historical_reconcile(
    conn,
    prices
):

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        """
    ).fetchall()

    for trade in rows:

        asset = trade[
            "asset"
        ]

        price = prices.get(
            asset
        )

        if price is None:
            continue

        entry_time = int(
            trade[
                "entry_time"
            ]
        )

        if (
            now_ts()
            - entry_time
            <= 0
        ):
            continue

        continue


# ============================================================
# SCAN ASSET
# ============================================================

def scan_asset(
    conn,
    asset
):

    contract = CONTRACTS.get(
        asset
    )

    if not contract:
        return None

    try:

        df1h = get_candles(
            contract,
            "1h",
            PATTERN_LOOKBACK
        )

        df5 = get_candles(
            contract,
            "5m",
            300
        )

    except Exception as exc:

        print(
            f"{asset} candle error: "
            f"{exc}"
        )

        return None

    if (
        df1h.empty
        or df5.empty
    ):

        print(
            f"{asset} "
            f"no candle data"
        )

        return None

    before_patterns = STATS[
        "patterns"
    ]

    before_recent = STATS[
        "recent_patterns"
    ]

    before_breakouts = STATS[
        "breakouts"
    ]

    before_old = STATS[
        "old_breakouts"
    ]

    before_retests = STATS[
        "retests"
    ]

    before_conf = STATS[
        "confirmations"
    ]

    before_valid = STATS[
        "valid_entries"
    ]

    candidates = build_signal(
        asset,
        df1h,
        df5
    )

    asset_patterns = (
        STATS["patterns"]
        - before_patterns
    )

    asset_recent = (
        STATS["recent_patterns"]
        - before_recent
    )

    asset_breakouts = (
        STATS["breakouts"]
        - before_breakouts
    )

    asset_old = (
        STATS["old_breakouts"]
        - before_old
    )

    asset_retests = (
        STATS["retests"]
        - before_retests
    )

    asset_conf = (
        STATS["confirmations"]
        - before_conf
    )

    asset_valid = (
        STATS["valid_entries"]
        - before_valid
    )

    print(
        f"{asset} "
        f"patterns={asset_patterns} "
        f"recent={asset_recent} "
        f"breakouts={asset_breakouts} "
        f"old={asset_old} "
        f"retests={asset_retests} "
        f"conf={asset_conf} "
        f"valid={asset_valid}"
    )

    return {
        "asset":
            asset,

        "candidates":
            candidates,

        "df1h":
            df1h,
    }


# ============================================================
# PERFORMANCE
# ============================================================

def performance_stats(
    conn
):

    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_trades,

            SUM(
                CASE
                    WHEN status = 'CLOSED'
                    THEN 1
                    ELSE 0
                END
            ) AS closed_trades,

            SUM(
                CASE
                    WHEN status = 'OPEN'
                    THEN 1
                    ELSE 0
                END
            ) AS open_trades
        FROM trades
        """
    ).fetchone()

    total_trades = int(
        row["total_trades"] or 0
    )

    closed_trades = int(
        row["closed_trades"] or 0
    )

    open_trades = int(
        row["open_trades"] or 0
    )

    pnl_row = conn.execute(
        """
        SELECT
            SUM(
                CASE
                    WHEN status = 'CLOSED'
                     AND direction = 'LONG'
                    THEN
                        (
                            (
                                exit_price
                                - entry_price
                            )
                            / entry_price
                        ) * 100

                    WHEN status = 'CLOSED'
                     AND direction = 'SHORT'
                    THEN
                        (
                            (
                                entry_price
                                - exit_price
                            )
                            / entry_price
                        ) * 100

                    ELSE 0
                END
            ) AS total_pnl,

            SUM(
                CASE
                    WHEN status = 'CLOSED'
                     AND direction = 'LONG'
                     AND exit_price > entry_price
                    THEN 1

                    WHEN status = 'CLOSED'
                     AND direction = 'SHORT'
                     AND exit_price < entry_price
                    THEN 1

                    ELSE 0
                END
            ) AS wins,

            SUM(
                CASE
                    WHEN status = 'CLOSED'
                     AND direction = 'LONG'
                     AND exit_price <= entry_price
                    THEN 1

                    WHEN status = 'CLOSED'
                     AND direction = 'SHORT'
                     AND exit_price >= entry_price
                    THEN 1

                    ELSE 0
                END
            ) AS losses

        FROM trades
        """
    ).fetchone()

    total_pnl = float(
        pnl_row["total_pnl"] or 0
    )

    wins = int(
        pnl_row["wins"] or 0
    )

    losses = int(
        pnl_row["losses"] or 0
    )

    if closed_trades > 0:

        win_rate = (
            wins
            / closed_trades
            * 100
        )

    else:

        win_rate = 0.0

    return {
        "total_trades":
            total_trades,

        "closed_trades":
            closed_trades,

        "open_trades":
            open_trades,

        "wins":
            wins,

        "losses":
            losses,

        "win_rate":
            win_rate,

        "total_pnl":
            total_pnl,
    }


# ============================================================
# PERIODIC REPORT
# ============================================================

def periodic_report(
    conn,
    prices
):

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY entry_time ASC
        """
    ).fetchall()

    lines = [
        "📊 <b>KRAKEN PATTERN SCANNER</b>",
        "",
        f"<b>🟢 OPEN TRADES: "
        f"{len(rows)}</b>",
        "",
    ]

    if not rows:

        lines.append(
            "No open trades."
        )

        lines.append("")

    else:

        for trade in rows:

            asset = trade[
                "asset"
            ]

            direction = trade[
                "direction"
            ]

            entry = float(
                trade[
                    "entry_price"
                ]
            )

            sl = float(
                trade[
                    "sl_price"
                ]
            )

            tp = float(
                trade[
                    "tp_price"
                ]
            )

            price = prices.get(
                asset,
                trade[
                    "current_price"
                ]
                or entry
            )

            price = float(
                price
            )

            if direction == "LONG":

                pct = (
                    (
                        price - entry
                    )
                    / entry
                    * 100
                )

            else:

                pct = (
                    (
                        entry - price
                    )
                    / entry
                    * 100
                )

            emoji = (
                "🟢"
                if direction == "LONG"
                else "🔴"
            )

            duration = duration_text(
                trade[
                    "entry_time"
                ]
            )

            lines.append(
                f"{emoji} "
                f"<b>{asset} "
                f"{direction}</b>"
            )

            lines.append(
                f"📌 Pattern: "
                f"{trade['pattern']}"
            )

            lines.append(
                f"💰 Entry: "
                f"{entry:.8g}"
            )

            lines.append(
                f"💵 Current: "
                f"{price:.8g} "
                f"({pct:+.2f}%)"
            )

            lines.append(
                f"🛑 SL: "
                f"{sl:.8g} "
                f"({SL_PCT * 100:.2f}%)"
            )

            lines.append(
                f"🎯 TP: "
                f"{tp:.8g} "
                f"({TP_PCT * 100:.2f}%)"
            )

            lines.append(
                f"⚙️ RR: {RR:.1f}"
            )

            lines.append(
                f"⏱ Duration: "
                f"{duration}"
            )

            lines.append("")

            conn.execute(
                """
                UPDATE trades
                SET
                    current_price = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    price,
                    now_ts(),
                    int(
                        trade["id"]
                    ),
                )
            )

        conn.commit()

    stats = performance_stats(
        conn
    )

    lines.append(
        "📈 <b>PERFORMANCE</b>"
    )

    lines.append(
        f"Trades: "
        f"{stats['total_trades']}"
    )

    lines.append(
        f"Closed: "
        f"{stats['closed_trades']}"
    )

    lines.append(
        f"Wins: "
        f"{stats['wins']}"
    )

    lines.append(
        f"Losses: "
        f"{stats['losses']}"
    )

    lines.append(
        f"Win Rate: "
        f"{stats['win_rate']:.2f}%"
    )

    lines.append(
        f"Total P&L: "
        f"{stats['total_pnl']:+.2f}%"
    )

    lines.append("")

    lines.append(
        "🔎 <b>LAST SCAN</b>"
    )

    lines.append(
        f"Valid entries: "
        f"{STATS['valid_entries']}"
    )

    lines.append(
        f"Unique entries: "
        f"{STATS['unique_entries']}"
    )

    lines.append(
        f"New signals: "
        f"{STATS['new_signals']}"
    )

    message = "\n".join(
        lines
    )

    sent = telegram_send(
        message
    )

    if sent:

        print(
            f"Periodic report sent "
            f"({len(rows)} open trades)."
        )

    else:

        print(
            "Periodic report failed."
        )

    return sent


# ============================================================
# SCAN SUMMARY
# ============================================================

def print_scan_summary(
    elapsed
):

    print("")
    print(
        "=========================================="
    )

    print(
        "SCAN SUMMARY"
    )

    print(
        "=========================================="
    )

    print(
        f"Patterns: "
        f"{STATS['patterns']}"
    )

    print(
        f"Recent patterns: "
        f"{STATS['recent_patterns']}"
    )

    print(
        f"Breakouts: "
        f"{STATS['breakouts']}"
    )

    print(
        f"Old breakouts: "
        f"{STATS['old_breakouts']}"
    )

    print(
        f"Retests: "
        f"{STATS['retests']}"
    )

    print(
        f"Confirmations: "
        f"{STATS['confirmations']}"
    )

    print(
        f"Valid entries: "
        f"{STATS['valid_entries']}"
    )

    print(
        f"Unique entries this scan: "
        f"{STATS['unique_entries']}"
    )

    print(
        f"Skipped - signal already exists: "
        f"{STATS['skipped_existing']}"
    )

    print(
        f"Skipped - open trade exists: "
        f"{STATS['skipped_open']}"
    )

    print(
        f"Insert blocked/ignored: "
        f"{STATS['insert_ignored']}"
    )

    print(
        f"New signals inserted: "
        f"{STATS['new_signals']}"
    )

    print(
        f"Trades closed by live price: "
        f"{STATS['closed_live']}"
    )

    print(
        f"Historical reconciled closes: "
        f"{STATS['historical_reconciled']}"
    )

    print(
        f"Scan completed in "
        f"{elapsed:.1f}s"
    )

    print(
        "=========================================="
    )

    print("")


# ============================================================
# MAIN
# ============================================================

def main():

    start = time.time()

    print("")
    print(
        "=" * 60
    )

    print(
        "KRAKEN FUTURES PATTERN LIVE "
        "SIGNAL SCANNER"
    )

    print(
        f"VERSION {VERSION}"
    )

    print(
        "=" * 60
    )

    print(
        f"REAL_TRADING = "
        f"{REAL_TRADING}"
    )

    print(
        f"ASSETS = "
        f"{len(ASSETS)}"
    )

    print(
        f"MAX_ENTRY_AGE_MINUTES = "
        f"{MAX_ENTRY_AGE_MINUTES}"
    )

    print(
        f"PERIODIC_REPORT_SECONDS = "
        f"{PERIODIC_REPORT_SECONDS}"
    )

    print(
        "ENTRY MODE = "
        "CONFIRMATION CANDLE CLOSE"
    )

    print(
        "FRESHNESS = "
        "CHECKED BEFORE ENTRY"
    )

    print(
        "PATTERN FILTER = "
        "MODERATE"
    )

    print(
        "NEW SIGNAL LIMIT = "
        "MAX 1 PER SCAN"
    )

    print(
        "CHART = "
        "ENABLED"
    )

    print(
        f"TP = "
        f"{TP_PCT * 100:.2f}%"
    )

    print(
        f"SL = "
        f"{SL_PCT * 100:.2f}%"
    )

    print(
        f"RR = "
        f"{RR:.1f}"
    )

    print("")

    # --------------------------------------------------------
    # PAPER ONLY
    # --------------------------------------------------------

    if REAL_TRADING:

        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    conn = get_connection()

    init_db(
        conn
    )

    print_unique_indexes(
        conn
    )

    # --------------------------------------------------------
    # AVAILABLE CONTRACTS
    # --------------------------------------------------------

    available_assets = (
        get_kraken_contracts()
    )

    # --------------------------------------------------------
    # LIVE PRICES
    # --------------------------------------------------------

    try:

        prices = get_live_prices()

        print(
            f"Live prices loaded: "
            f"{len(prices)}"
        )

    except Exception as exc:

        print(
            f"Could not load live prices: "
            f"{exc}"
        )

        prices = {}

    # --------------------------------------------------------
    # RECONCILE OPEN TRADES
    # --------------------------------------------------------

    try:

        reconcile_open_trades(
            conn,
            prices
        )

    except Exception as exc:

        print(
            f"Live reconciliation error: "
            f"{exc}"
        )

    # --------------------------------------------------------
    # HISTORICAL RECONCILIATION
    # --------------------------------------------------------

    try:

        historical_reconcile(
            conn,
            prices
        )

    except Exception as exc:

        print(
            f"Historical reconciliation "
            f"error: {exc}"
        )

    # ========================================================
    # SCAN ALL ASSETS
    # ========================================================
    #
    # IMPORTANT:
    # We collect candidates first.
    #
    # We DO NOT insert a signal immediately for every asset.
    #
    # After all 40 assets are scanned, we choose the best
    # eligible candidate and insert MAXIMUM ONE new signal.
    #
    # This is what keeps a 5-minute workflow from producing
    # a flood of simultaneous signals.
    # ========================================================

    all_candidates = []

    for asset in available_assets:

        try:

            result = scan_asset(
                conn,
                asset
            )

            if not result:
                continue

            candidates = result[
                "candidates"
            ]

            df1h = result[
                "df1h"
            ]

            for candidate in candidates:

                candidate["df1h"] = df1h

                all_candidates.append(
                    candidate
                )

        except Exception as exc:

            print(
                f"{asset} scan error: "
                f"{exc}"
            )

    # --------------------------------------------------------
    # Sort strongest candidate first.
    # --------------------------------------------------------

    all_candidates.sort(
        key=lambda item:
            item["score"],
        reverse=True
    )

    print(
        f"Total valid candidates: "
        f"{len(all_candidates)}"
    )

    # --------------------------------------------------------
    # Select ONE eligible signal.
    # --------------------------------------------------------

    selected = None

    for candidate in all_candidates:

        signal = candidate[
            "signal"
        ]

        if signal_exists(
            conn,
            signal["signal_key"]
        ):

            STATS[
                "skipped_existing"
            ] += 1

            print(
                f"SKIP EXISTING: "
                f"{signal['asset']} "
                f"{signal['direction']} "
                f"{signal['signal_key']}"
            )

            continue

        if open_trade_exists(
            conn,
            signal["asset"],
            signal["direction"]
        ):

            STATS[
                "skipped_open"
            ] += 1

            print(
                f"SKIP OPEN TRADE: "
                f"{signal['asset']} "
                f"{signal['direction']}"
            )

            continue

        selected = candidate

        break

    # --------------------------------------------------------
    # INSERT ONLY ONE
    # --------------------------------------------------------

    if selected is not None:

        signal = selected[
            "signal"
        ]

        chain = selected[
            "chain"
        ]

        df1h = selected[
            "df1h"
        ]

        STATS[
            "unique_entries"
        ] += 1

        inserted = insert_trade(
            conn,
            signal
        )

        if inserted > 0:

            STATS[
                "new_signals"
            ] += 1

            print(
                f"NEW SIGNAL INSERTED: "
                f"{signal['asset']} "
                f"{signal['direction']} "
                f"{signal['pattern']}"
            )

            send_new_signal(
                signal,
                chain,
                df1h
            )

        else:

            STATS[
                "insert_ignored"
            ] += 1

            print(
                f"INSERT FAILED/IGNORED: "
                f"{signal['asset']} "
                f"{signal['direction']}"
            )

    else:

        print(
            "No eligible new signal "
            "selected this scan."
        )

    # --------------------------------------------------------
    # PERIODIC REPORT
    # --------------------------------------------------------

    last_report = get_meta(
        conn,
        "last_periodic_report"
    )

    try:

        last_report_ts = (
            int(last_report)
            if last_report
            else 0
        )

    except Exception:

        last_report_ts = 0

    current_time = now_ts()

    should_report = (
        current_time
        - last_report_ts
        >= PERIODIC_REPORT_SECONDS
    )

    if should_report:

        print(
            "Periodic report is due."
        )

        try:

            report_prices = (
                get_live_prices()
            )

        except Exception:

            report_prices = prices

        sent = periodic_report(
            conn,
            report_prices
        )

        if sent:

            set_meta(
                conn,
                "last_periodic_report",
                now_ts()
            )

            print(
                "Periodic report timestamp saved."
            )

        else:

            print(
                "Periodic report was "
                "NOT marked as sent."
            )

    else:

        remaining = (
            PERIODIC_REPORT_SECONDS
            - (
                current_time
                - last_report_ts
            )
        )

        print(
            f"Periodic report not due. "
            f"~{max(0, remaining)}s remaining."
        )

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    elapsed = (
        time.time()
        - start
    )

    print_scan_summary(
        elapsed
    )

    conn.close()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
