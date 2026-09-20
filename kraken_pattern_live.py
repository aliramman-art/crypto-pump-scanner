# ============================================================
# KRAKEN FUTURES PATTERN LIVE SIGNAL SCANNER
# VERSION 5.9.7
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
# SAFETY:
# - Entry candle is NOT used for TP/SL
# - Same candle TP + SL => SL first
# - One OPEN trade per asset + direction
# - Opposite direction is allowed
# - No real trading
#
# VERSION 5.9.7 CHANGES:
# - Restored PERFORMANCE in periodic report
# - Periodic report still sends with 0 open trades
# - Added detailed signal insertion diagnostics
# - Removed INSERT OR IGNORE so SQLite errors are visible
# - Existing DB is NOT deleted/rebuilt
# - Unique indexes are printed at startup
# - Added counters:
#     skipped_existing
#     skipped_open
#     insert_ignored
#     new_signals
#
# ============================================================

import os
import time
import sqlite3
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

VERSION = "5.9.7"

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

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


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

        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")

    except Exception:
        return "-"


def duration_text(start_ts, end_ts=None):
    if not start_ts:
        return "-"

    if end_ts is None:
        end_ts = now_ts()

    seconds = max(0, int(end_ts) - int(start_ts))

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


def http_get(url, params=None):
    last_error = None

    for attempt in range(1, HTTP_RETRIES + 1):

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
# TELEGRAM
# ============================================================

def telegram_send(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials are missing.")
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

        print("Telegram message sent.")

        return True

    except Exception as exc:

        print(f"Telegram send failed: {exc}")

        return False


# ============================================================
# DATABASE
# ============================================================

def get_connection():

    conn = sqlite3.connect(DB_FILE)

    conn.row_factory = sqlite3.Row

    return conn


def ensure_column(conn, table, column, definition):

    rows = conn.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    existing = {
        row["name"]
        for row in rows
    }

    if column not in existing:

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

    # --------------------------------------------------------
    # Preserve existing DB.
    # Only add missing columns.
    # --------------------------------------------------------

    ensure_column(
        conn,
        "trades",
        "asset",
        "TEXT"
    )

    ensure_column(
        conn,
        "trades",
        "direction",
        "TEXT"
    )

    ensure_column(
        conn,
        "trades",
        "pattern",
        "TEXT"
    )

    ensure_column(
        conn,
        "trades",
        "signal_key",
        "TEXT"
    )

    ensure_column(
        conn,
        "trades",
        "pattern_time",
        "INTEGER"
    )

    ensure_column(
        conn,
        "trades",
        "breakout_time",
        "INTEGER"
    )

    ensure_column(
        conn,
        "trades",
        "retest_time",
        "INTEGER"
    )

    ensure_column(
        conn,
        "trades",
        "confirmation_time",
        "INTEGER"
    )

    ensure_column(
        conn,
        "trades",
        "entry_time",
        "INTEGER"
    )

    ensure_column(
        conn,
        "trades",
        "entry_price",
        "REAL"
    )

    ensure_column(
        conn,
        "trades",
        "sl_price",
        "REAL"
    )

    ensure_column(
        conn,
        "trades",
        "tp_price",
        "REAL"
    )

    ensure_column(
        conn,
        "trades",
        "current_price",
        "REAL"
    )

    ensure_column(
        conn,
        "trades",
        "status",
        "TEXT"
    )

    ensure_column(
        conn,
        "trades",
        "exit_time",
        "INTEGER"
    )

    ensure_column(
        conn,
        "trades",
        "exit_price",
        "REAL"
    )

    ensure_column(
        conn,
        "trades",
        "exit_reason",
        "TEXT"
    )

    ensure_column(
        conn,
        "trades",
        "created_at",
        "INTEGER"
    )

    ensure_column(
        conn,
        "trades",
        "updated_at",
        "INTEGER"
    )

    # --------------------------------------------------------
    # Backfill timestamps
    # --------------------------------------------------------

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

    print("Database schema verified.")


# ============================================================
# DATABASE DIAGNOSTICS
# ============================================================

def print_unique_indexes(conn):

    print("")
    print("DATABASE UNIQUE INDEX CHECK")
    print("--------------------------------")

    try:

        indexes = conn.execute(
            "PRAGMA index_list(trades)"
        ).fetchall()

        found = False

        for idx in indexes:

            idx_name = idx["name"]
            is_unique = int(idx["unique"])

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

def get_meta(conn, key):

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


def set_meta(conn, key, value):

    conn.execute(
        """
        INSERT INTO scanner_meta(key, value)
        VALUES (?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value = excluded.value
        """,
        (key, str(value))
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

    response = http_get(url)

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

        contract = CONTRACTS.get(asset)

        if not contract:
            continue

        found = False

        for item in instruments:

            if item.get("symbol") == contract:
                found = True
                break

        if found:
            available.append(asset)

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

    response = http_get(url)

    data = response.json()

    tickers = data.get(
        "tickers",
        []
    )

    prices = {}

    for ticker in tickers:

        symbol = ticker.get("symbol")

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

def get_candles(contract, interval, count=300):

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

    # Kraken response can have different wrappers.
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

            open_price = candle.get("open")
            high = candle.get("high")
            low = candle.get("low")
            close = candle.get("close")
            volume = candle.get("volume", 0)

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

            # Handle milliseconds.
            if ts > 10_000_000_000:
                ts //= 1000

            rows.append(
                {
                    "timestamp": ts,
                    "open": float(open_price),
                    "high": float(high),
                    "low": float(low),
                    "close": float(close),
                    "volume": float(volume),
                }
            )

        except Exception:
            continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    df = (
        df
        .drop_duplicates(
            subset=["timestamp"]
        )
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    # --------------------------------------------------------
    # CLOSED CANDLES ONLY
    # --------------------------------------------------------

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

    return df.reset_index(drop=True)


# ============================================================
# PATTERN DETECTION
# ============================================================

def detect_patterns(df):

    patterns = []

    if df is None or len(df) < 20:
        return patterns

    # --------------------------------------------------------
    # The pattern logic remains intentionally conservative.
    # Closed candles only.
    # --------------------------------------------------------

    for i in range(5, len(df) - 5):

        current = df.iloc[i]

        left = df.iloc[i - 3:i]
        right = df.iloc[i + 1:i + 4]

        left_high = left["high"].max()
        right_high = right["high"].max()

        left_low = left["low"].min()
        right_low = right["low"].min()

        # ----------------------------------------------------
        # DOUBLE TOP
        # ----------------------------------------------------

        if (
            abs(left_high - right_high)
            / max(left_high, 1e-12)
            <= 0.01
        ):

            neckline = min(
                left["low"].min(),
                right["low"].min()
            )

            patterns.append(
                {
                    "pattern": "Double Top",
                    "direction": "SHORT",
                    "pattern_time": int(
                        current["timestamp"]
                    ),
                    "level": float(neckline),
                    "index": i,
                }
            )

        # ----------------------------------------------------
        # DOUBLE BOTTOM
        # ----------------------------------------------------

        if (
            abs(left_low - right_low)
            / max(left_low, 1e-12)
            <= 0.01
        ):

            neckline = max(
                left["high"].max(),
                right["high"].max()
            )

            patterns.append(
                {
                    "pattern": "Double Bottom",
                    "direction": "LONG",
                    "pattern_time": int(
                        current["timestamp"]
                    ),
                    "level": float(neckline),
                    "index": i,
                }
            )

        # ----------------------------------------------------
        # HEAD & SHOULDERS
        # ----------------------------------------------------

        if (
            current["high"] > left_high
            and current["high"] > right_high
        ):

            neckline = min(
                left["low"].min(),
                right["low"].min()
            )

            patterns.append(
                {
                    "pattern": "Head & Shoulders",
                    "direction": "SHORT",
                    "pattern_time": int(
                        current["timestamp"]
                    ),
                    "level": float(neckline),
                    "index": i,
                }
            )

        # ----------------------------------------------------
        # INVERSE HEAD & SHOULDERS
        # ----------------------------------------------------

        if (
            current["low"] < left_low
            and current["low"] < right_low
        ):

            neckline = max(
                left["high"].max(),
                right["high"].max()
            )

            patterns.append(
                {
                    "pattern":
                        "Inverse Head & Shoulders",
                    "direction": "LONG",
                    "pattern_time": int(
                        current["timestamp"]
                    ),
                    "level": float(neckline),
                    "index": i,
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

    direction = pattern["direction"]

    future = df[
        df["timestamp"] > pattern_time
    ].copy()

    future = future.head(
        BREAKOUT_LOOKAHEAD
    )

    if future.empty:
        return None

    for _, candle in future.iterrows():

        if direction == "LONG":

            if float(candle["close"]) > level:

                return {
                    "breakout_time":
                        int(candle["timestamp"]),

                    "breakout_price":
                        float(candle["close"]),

                    "level":
                        level,
                }

        else:

            if float(candle["close"]) < level:

                return {
                    "breakout_time":
                        int(candle["timestamp"]),

                    "breakout_price":
                        float(candle["close"]),

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
        df5["timestamp"] > breakout_time
    ].copy()

    future = future.head(
        RETEST_LOOKAHEAD
    )

    if future.empty:
        return None

    for _, candle in future.iterrows():

        high = float(candle["high"])
        low = float(candle["low"])

        # ----------------------------------------------------
        # LONG RETEST
        # Price returns to breakout level.
        # ----------------------------------------------------

        if direction == "LONG":

            if low <= level <= high:

                return {
                    "retest_time":
                        int(candle["timestamp"]),

                    "retest_price":
                        level,
                }

        # ----------------------------------------------------
        # SHORT RETEST
        # ----------------------------------------------------

        else:

            if low <= level <= high:

                return {
                    "retest_time":
                        int(candle["timestamp"]),

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
        MAX_ENTRY_AGE_MINUTES * 60
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
                        int(candle["timestamp"]),

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
                        int(candle["timestamp"]),

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
        confirmation["confirmation_time"]
    )

    entry_price = float(
        confirmation["entry_price"]
    )

    # --------------------------------------------------------
    # IMPORTANT:
    # Entry candle is NOT used for TP/SL.
    # TP/SL are calculated directly from entry.
    # --------------------------------------------------------

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

    recent_patterns = []

    recent_seconds = (
        RECENT_PATTERN_HOURS
        * 3600
    )

    for pattern in patterns:

        age = (
            current_time
            - int(pattern["pattern_time"])
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
        return None, None

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
                breakout["breakout_time"]
            )
        )

        if breakout_age > (
            BREAKOUT_LOOKAHEAD * 3600
        ):
            STATS["old_breakouts"] += 1
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
            "asset": asset,

            "direction":
                pattern["direction"],

            "pattern":
                pattern["pattern"],

            "signal_key":
                signal_key,

            "pattern_time":
                int(
                    pattern["pattern_time"]
                ),

            "breakout_time":
                int(
                    breakout["breakout_time"]
                ),

            "retest_time":
                int(
                    retest["retest_time"]
                ),

            "confirmation_time":
                int(
                    confirmation[
                        "confirmation_time"
                    ]
                ),

            "entry_time":
                int(
                    entry["entry_time"]
                ),

            "entry_price":
                float(
                    entry["entry_price"]
                ),

            "sl_price":
                float(
                    entry["sl_price"]
                ),

            "tp_price":
                float(
                    entry["tp_price"]
                ),

            "current_price":
                float(
                    entry["entry_price"]
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

        return signal, chain

    return None, None


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
        (signal_key,)
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

    sql = """
        INSERT INTO trades (
            asset,
            direction,
            pattern,
            signal_key,
            pattern_time,
            breakout_time,
            retest_time,
            confirmation_time,
            entry_time,
            entry_price,
            sl_price,
            tp_price,
            current_price,
            status,
            exit_time,
            exit_price,
            exit_reason,
            created_at,
            updated_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
    """

    values = (
        signal["asset"],
        signal["direction"],
        signal["pattern"],
        signal["signal_key"],
        signal["pattern_time"],
        signal["breakout_time"],
        signal["retest_time"],
        signal["confirmation_time"],
        signal["entry_time"],
        signal["entry_price"],
        signal["sl_price"],
        signal["tp_price"],
        signal["current_price"],
        signal["status"],
        signal["exit_time"],
        signal["exit_price"],
        signal["exit_reason"],
        signal["created_at"],
        signal["updated_at"],
    )

    try:

        cursor = conn.execute(
            sql,
            values
        )

        conn.commit()

        rowcount = cursor.rowcount

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
            f"Direction: "
            f"{signal['direction']}"
        )

        print(
            f"Pattern: "
            f"{signal['pattern']}"
        )

        print(
            f"Signal key: "
            f"{signal['signal_key']}"
        )

        print(
            f"SQLite IntegrityError: "
            f"{exc}"
        )

        # ----------------------------------------------------
        # Check if signal key somehow exists now.
        # ----------------------------------------------------

        existing = conn.execute(
            """
            SELECT id,
                   signal_key,
                   status,
                   asset,
                   direction,
                   entry_time
            FROM trades
            WHERE signal_key = ?
            LIMIT 1
            """,
            (
                signal["signal_key"],
            )
        ).fetchone()

        if existing:

            print(
                "Reason appears to be "
                "existing signal_key."
            )

            print(
                f"Existing trade ID: "
                f"{existing['id']}"
            )

            print(
                f"Existing status: "
                f"{existing['status']}"
            )

        else:

            print(
                "Signal key does NOT exist "
                "in DB after failed insert."
            )

            print(
                "Therefore another UNIQUE "
                "constraint/index may be blocking "
                "the insertion."
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
# NEW SIGNAL MESSAGE
# ============================================================

def new_signal_message(signal):

    direction = signal["direction"]

    if direction == "LONG":
        emoji = "🟢"
    else:
        emoji = "🔴"

    entry = signal["entry_price"]
    sl = signal["sl_price"]
    tp = signal["tp_price"]

    return (
        "🚨 <b>NEW SIGNAL</b>\n"
        "\n"
        f"{emoji} <b>{signal['asset']} "
        f"{direction}</b>\n"
        f"📌 Pattern: "
        f"{signal['pattern']}\n"
        f"💰 Entry: {entry:.8g}\n"
        f"🛑 SL: {sl:.8g} "
        f"({SL_PCT * 100:.2f}%)\n"
        f"🎯 TP: {tp:.8g} "
        f"({TP_PCT * 100:.2f}%)\n"
        f"⚙️ RR: {RR:.1f}\n"
        f"🕐 Entry Time: "
        f"{format_time(signal['entry_time'])}"
    )


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

def close_message(trade):

    direction = trade["direction"]

    entry = float(
        trade["entry_price"]
    )

    exit_price = float(
        trade["exit_price"]
    )

    if direction == "LONG":

        pct = (
            (exit_price - entry)
            / entry
            * 100
        )

    else:

        pct = (
            (entry - exit_price)
            / entry
            * 100
        )

    if pct >= 0:
        result_emoji = "🟢"
    else:
        result_emoji = "🔴"

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
        f"💰 Entry: {entry:.8g}\n"
        f"💵 Exit: {exit_price:.8g}\n"
        f"📈 Result: {pct:+.2f}%\n"
        f"📍 Reason: "
        f"{trade['exit_reason']}\n"
        f"⏱ Duration: {duration}\n"
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

        asset = trade["asset"]

        price = prices.get(asset)

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

        direction = trade["direction"]

        exit_reason = None

        exit_price = None

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if direction == "LONG":

            if price <= sl:

                exit_reason = "SL"
                exit_price = sl

            elif price >= tp:

                exit_reason = "TP"
                exit_price = tp

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        else:

            if price >= sl:

                exit_reason = "SL"
                exit_price = sl

            elif price <= tp:

                exit_reason = "TP"
                exit_price = tp

        # ----------------------------------------------------
        # MAX HOLD
        # ----------------------------------------------------

        if exit_reason is None:

            age_hours = (
                now_ts()
                - int(trade["entry_time"])
            ) / 3600

            if age_hours >= MAX_HOLD_HOURS:

                exit_reason = "TIME"
                exit_price = price

        # ----------------------------------------------------
        # UPDATE CURRENT PRICE
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # CLOSE
        # ----------------------------------------------------

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
                    int(trade["id"]),
                )
            ).fetchone()

            telegram_send(
                close_message(closed)
            )

            STATS["closed_live"] += 1


# ============================================================
# HISTORICAL RECONCILIATION
# ============================================================

def historical_reconcile(
    conn,
    prices
):

    # --------------------------------------------------------
    # Existing behavior is intentionally conservative.
    #
    # Only checks OPEN trades and current prices here.
    # --------------------------------------------------------

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        """
    ).fetchall()

    for trade in rows:

        asset = trade["asset"]

        price = prices.get(asset)

        if price is None:
            continue

        entry_time = int(
            trade["entry_time"]
        )

        if now_ts() - entry_time <= 0:
            continue

        # No extra strategy changes here.
        # Live reconciliation handles actual closes.

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
        return

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

        return

    if df1h.empty or df5.empty:

        print(
            f"{asset} "
            f"no candle data"
        )

        return

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

    signal, chain = build_signal(
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

    if signal is None:
        return

    # --------------------------------------------------------
    # IMPORTANT:
    # One candidate at a time.
    # Strategy unchanged.
    # --------------------------------------------------------

    if signal_exists(
        conn,
        signal["signal_key"]
    ):

        STATS["skipped_existing"] += 1

        print(
            f"SKIP EXISTING: "
            f"{signal['asset']} "
            f"{signal['direction']} "
            f"{signal['signal_key']}"
        )

        return

    STATS["unique_entries"] += 1

    if open_trade_exists(
        conn,
        signal["asset"],
        signal["direction"]
    ):

        STATS["skipped_open"] += 1

        print(
            f"SKIP OPEN TRADE: "
            f"{signal['asset']} "
            f"{signal['direction']}"
        )

        return

    inserted = insert_trade(
        conn,
        signal
    )

    if inserted <= 0:

        STATS["insert_ignored"] += 1

        print(
            f"INSERT FAILED/IGNORED: "
            f"{signal['asset']} "
            f"{signal['direction']}"
        )

        return

    STATS["new_signals"] += 1

    telegram_send(
        new_signal_message(signal)
    )


# ============================================================
# PERFORMANCE
# ============================================================

def performance_stats(conn):

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

    # --------------------------------------------------------
    # Realized percentage P&L
    # LONG:
    #     (exit - entry) / entry
    #
    # SHORT:
    #     (entry - exit) / entry
    # --------------------------------------------------------

    pnl_row = conn.execute(
        """
        SELECT
            SUM(
                CASE
                    WHEN status = 'CLOSED'
                     AND direction = 'LONG'
                    THEN
                        (
                            (exit_price - entry_price)
                            / entry_price
                        ) * 100

                    WHEN status = 'CLOSED'
                     AND direction = 'SHORT'
                    THEN
                        (
                            (entry_price - exit_price)
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

            asset = trade["asset"]

            direction = (
                trade["direction"]
            )

            entry = float(
                trade["entry_price"]
            )

            sl = float(
                trade["sl_price"]
            )

            tp = float(
                trade["tp_price"]
            )

            price = prices.get(
                asset,
                trade["current_price"]
                or entry
            )

            price = float(price)

            if direction == "LONG":

                pct = (
                    (price - entry)
                    / entry
                    * 100
                )

            else:

                pct = (
                    (entry - price)
                    / entry
                    * 100
                )

            if direction == "LONG":

                emoji = "🟢"

            else:

                emoji = "🔴"

            duration = duration_text(
                trade["entry_time"]
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
                    int(trade["id"]),
                )
            )

        conn.commit()

    # ========================================================
    # PERFORMANCE
    # ========================================================

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

    # ========================================================
    # SCAN INSERTION DIAGNOSTICS
    # ========================================================

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
    print("==========================================")
    print("SCAN SUMMARY")
    print("==========================================")

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

    print("==========================================")
    print("")


# ============================================================
# MAIN
# ============================================================

def main():

    start = time.time()

    print("")
    print("=" * 60)
    print(
        f"KRAKEN FUTURES PATTERN LIVE "
        f"SIGNAL SCANNER"
    )
    print(
        f"VERSION {VERSION}"
    )
    print("=" * 60)

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
        "PERIODIC REPORT = "
        "EVERY 15 MINUTES INCLUDING "
        "ZERO OPEN TRADES"
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
    # PAPER ONLY SAFETY
    # --------------------------------------------------------

    if REAL_TRADING:

        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    conn = get_connection()

    init_db(conn)

    print_unique_indexes(conn)

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
    # RECONCILE OPEN TRADES BEFORE SCAN
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

    # --------------------------------------------------------
    # SCAN ALL ASSETS
    # --------------------------------------------------------

    for asset in available_assets:

        try:

            scan_asset(
                conn,
                asset
            )

        except Exception as exc:

            print(
                f"{asset} scan error: "
                f"{exc}"
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
