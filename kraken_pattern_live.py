# ============================================================
# KRAKEN FUTURES PATTERN LIVE SIGNAL SCANNER
# VERSION 6.4.0
# ============================================================
#
# PAPER ONLY
# REAL_TRADING = False
#
# STRATEGY
#
# 1H:
#   Pattern
#      ↓
#   1H Breakout
#      ↓
#
# 15M:
#   Pattern
#      ↓
#   15M Breakout
#      ↓
#   First Retest
#      ↓
#   First Confirmation
#      ↓
#   ENTRY
#
# IMPORTANT:
# - 1H and 15M pattern names do NOT need to match.
# - Their direction MUST match.
# - TP / SL come from the 15M pattern.
# - Only closed candles are used.
# - No lookahead.
# - First retest only.
# - First confirmation only.
# - Confirmation must be fresh.
# - Maximum confirmation age = 30 minutes AFTER
#   the confirmation candle has CLOSED.
#
# EXISTING DB PRESERVED:
#   kraken_pattern_live_v52.db
# ============================================================

import os
import sqlite3
import time
import traceback
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "6.4.0"

REAL_TRADING = False

DB_FILE = "kraken_pattern_live_v52.db"

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "",
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    "",
)

KRAKEN_TICKERS_URL = (
    "https://futures.kraken.com/derivatives/api/v3/tickers"
)

KRAKEN_CHART_URL = (
    "https://futures.kraken.com/api/charts/v1/trade"
)

HTTP_RETRIES = 3
HTTP_TIMEOUT = 20

# ------------------------------------------------------------
# TRADE MANAGEMENT
# ------------------------------------------------------------

MAX_HOLD_HOURS = 48

# ------------------------------------------------------------
# DATA
# ------------------------------------------------------------

PATTERN_LOOKBACK_1H = 240
PATTERN_LOOKBACK_15M = 240

RECENT_PATTERN_HOURS_1H = 72
RECENT_PATTERN_HOURS_15M = 36

BREAKOUT_LOOKAHEAD_1H = 24
BREAKOUT_LOOKAHEAD_15M = 32

RETEST_LOOKAHEAD_15M = 16
CONFIRMATION_LOOKAHEAD_15M = 4

# ------------------------------------------------------------
# SIGNAL FRESHNESS
# ------------------------------------------------------------
#
# Scanner runs every 5 minutes.
#
# A confirmation remains eligible for 30 minutes AFTER
# the confirmation candle has closed.
#
# Example:
# Confirmation candle starts: 23:00
# 15M candle closes:          23:15
# Last valid signal time:     23:45
#
# This prevents old signals from being discovered later.
# ------------------------------------------------------------

MAX_CONFIRMATION_AGE_MINUTES = 30

# ------------------------------------------------------------
# SIGNAL LIMIT
# ------------------------------------------------------------

MAX_SIGNALS_PER_SCAN = 1

# ------------------------------------------------------------
# PATTERN QUALITY
# ------------------------------------------------------------

DOUBLE_LEVEL_TOLERANCE = 0.012

MIN_PATTERN_DEPTH_PCT = 0.004

MIN_PATTERN_SEPARATION_BARS = 3

MAX_DOUBLE_PATTERN_BARS = 48

MAX_HS_PATTERN_BARS = 72

HS_SHOULDER_TOLERANCE = 0.025

HS_MIN_HEAD_ADVANTAGE_PCT = 0.003

MIN_HS_DEPTH_PCT = 0.004

INVALIDATION_BUFFER = 0.001

SWING_LEFT_RIGHT = 3

# ------------------------------------------------------------
# TELEGRAM / CHART
# ------------------------------------------------------------

CHART_CANDLES = 100

PERIODIC_REPORT_SECONDS = 900


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
# GLOBALS
# ============================================================

LIVE_PRICES = {}

BEST_CANDIDATE = None

STATS = {
    "scans": 0,
    "assets_scanned": 0,
    "patterns_1h": 0,
    "breakouts_1h": 0,
    "patterns_15m": 0,
    "breakouts_15m": 0,
    "retests_15m": 0,
    "confirmations_15m": 0,
    "fresh_confirmations": 0,
    "stale_confirmations": 0,
    "candidates": 0,
    "unique_entries": 0,
    "signals_sent": 0,
    "closed_trades": 0,
    "errors": 0,
}


# ============================================================
# TIME
# ============================================================

TEHRAN_TZ = timezone(
    timedelta(
        hours=3,
        minutes=30,
    )
)


def now_ts():
    return int(time.time())


def format_time(value):
    if value is None:
        return "-"

    try:
        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(
                    value.replace(
                        "Z",
                        "+00:00",
                    )
                )
            except Exception:
                dt = datetime.fromtimestamp(
                    float(value),
                    tz=timezone.utc,
                )
        else:
            dt = datetime.fromtimestamp(
                float(value),
                tz=timezone.utc,
            )

        return dt.astimezone(
            TEHRAN_TZ
        ).strftime(
            "%Y-%m-%d %H:%M"
        )

    except Exception:
        return "-"


def duration_text(
    start_value,
    end_value=None,
):
    if start_value is None:
        return "-"

    try:
        def to_timestamp(value):
            if isinstance(value, str):
                return datetime.fromisoformat(
                    value.replace(
                        "Z",
                        "+00:00",
                    )
                ).timestamp()

            return float(value)

        start = to_timestamp(
            start_value
        )

        end = (
            now_ts()
            if end_value is None
            else to_timestamp(end_value)
        )

        seconds = max(
            0,
            int(end - start),
        )

        minutes = seconds // 60

        hours = minutes // 60
        minutes = minutes % 60

        if hours:
            return f"{hours}h {minutes}m"

        return f"{minutes}m"

    except Exception:
        return "-"


def confirmation_age_minutes(
    confirmation_timestamp
):
    """
    Confirmation timestamp is candle START time.
    15M candle must close 15 minutes later.
    """

    confirmation_close = (
        int(confirmation_timestamp)
        + 15 * 60
    )

    age_seconds = (
        now_ts()
        - confirmation_close
    )

    return age_seconds / 60.0


# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()


def http_get(
    url,
    params=None,
):
    last_error = None

    for attempt in range(
        HTTP_RETRIES
    ):
        try:
            response = SESSION.get(
                url,
                params=params,
                timeout=HTTP_TIMEOUT,
            )

            response.raise_for_status()

            return response.json()

        except Exception as exc:
            last_error = exc

            if attempt < HTTP_RETRIES - 1:
                time.sleep(
                    1.5 * (
                        attempt + 1
                    )
                )

    raise last_error


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send_text(text):
    if not TELEGRAM_BOT_TOKEN:
        return False

    if not TELEGRAM_CHAT_ID:
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = SESSION.post(
            url,
            json=payload,
            timeout=20,
        )

        response.raise_for_status()

        return True

    except Exception as exc:
        print(
            "Telegram text error:",
            exc,
        )

        return False


def telegram_send_photo(
    photo_path,
    caption,
):
    if not TELEGRAM_BOT_TOKEN:
        return False

    if not TELEGRAM_CHAT_ID:
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    try:
        with open(
            photo_path,
            "rb",
        ) as photo:

            response = SESSION.post(
                url,
                data={
                    "chat_id":
                        TELEGRAM_CHAT_ID,
                    "caption":
                        caption,
                    "parse_mode":
                        "HTML",
                },
                files={
                    "photo":
                        photo,
                },
                timeout=30,
            )

        response.raise_for_status()

        return True

    except Exception as exc:
        print(
            "Telegram photo error:",
            exc,
        )

        return False


def send_telegram(
    text,
):
    return telegram_send_text(
        text
    )


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(
        DB_FILE
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():
    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            asset TEXT,
            direction TEXT,
            pattern TEXT,
            signal_key TEXT UNIQUE,
            pattern_time TEXT,
            breakout_time TEXT,
            retest_time TEXT,
            confirmation_time TEXT,
            entry_time TEXT,
            entry_price REAL,
            sl_price REAL,
            tp_price REAL,
            current_price REAL,
            status TEXT,
            exit_time TEXT,
            exit_price REAL,
            exit_reason TEXT,
            created_at INTEGER,
            updated_at INTEGER
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS scanner_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

    conn.commit()

    cur.execute(
        "PRAGMA table_info(trades)"
    )

    existing_columns = {
        row["name"]
        for row in cur.fetchall()
    }

    required_columns = {
        "symbol": "TEXT",
        "asset": "TEXT",
        "direction": "TEXT",
        "pattern": "TEXT",
        "signal_key": "TEXT",
        "pattern_time": "TEXT",
        "breakout_time": "TEXT",
        "retest_time": "TEXT",
        "confirmation_time": "TEXT",
        "entry_time": "TEXT",
        "entry_price": "REAL",
        "sl_price": "REAL",
        "tp_price": "REAL",
        "current_price": "REAL",
        "status": "TEXT",
        "exit_time": "TEXT",
        "exit_price": "REAL",
        "exit_reason": "TEXT",
        "created_at": "INTEGER",
        "updated_at": "INTEGER",
    }

    for name, dtype in required_columns.items():

        if name not in existing_columns:

            try:
                cur.execute(
                    "ALTER TABLE trades ADD COLUMN "
                    f"{name} {dtype}"
                )

            except Exception as exc:
                print(
                    "DB migration error:",
                    name,
                    exc,
                )

    try:
        cur.execute(
            """
            UPDATE trades
            SET symbol = asset
            WHERE symbol IS NULL
            """
        )
    except Exception:
        pass

    try:
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_trades_signal_key
            ON trades(signal_key)
            """
        )
    except Exception:
        pass

    conn.commit()
    conn.close()


# ============================================================
# DATABASE HELPERS
# ============================================================

def has_signal(
    signal_key,
):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
        FROM trades
        WHERE signal_key = ?
        LIMIT 1
        """,
        (
            signal_key,
        ),
    ).fetchone()

    conn.close()

    return row is not None


def has_open_trade(
    asset,
    direction,
):
    conn = db_connect()

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
        ),
    ).fetchone()

    conn.close()

    return row is not None


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


def insert_trade(
    trade,
):
    conn = db_connect()

    try:

        cur = conn.execute(
            """
            INSERT OR IGNORE INTO trades (
                symbol,
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
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade["symbol"],
                trade["asset"],
                trade["direction"],
                trade["pattern"],
                trade["signal_key"],
                trade["pattern15m"][
                    "pattern_time"
                ],
                trade["breakout15m"][
                    "timestamp"
                ],
                trade["retest15m"][
                    "timestamp"
                ],
                trade["confirmation15m"][
                    "timestamp"
                ],
                trade["entry_time"],
                trade["entry_price"],
                trade["sl_price"],
                trade["tp_price"],
                trade["entry_price"],
                "OPEN",
                now_ts(),
                now_ts(),
            ),
        )

        conn.commit()

        inserted = (
            cur.rowcount == 1
        )

        return inserted

    finally:
        conn.close()


# ============================================================
# KRAKEN
# ============================================================

def get_contract(
    asset,
):
    return CONTRACTS.get(
        asset,
        f"PF_{asset}USD",
    )


def load_prices():
    global LIVE_PRICES

    data = http_get(
        KRAKEN_TICKERS_URL
    )

    tickers = data.get(
        "tickers",
        [],
    )

    prices = {}

    for ticker in tickers:

        symbol = ticker.get(
            "symbol"
        )

        if not symbol:
            continue

        value = (
            ticker.get("last")
            or ticker.get("lastPrice")
            or ticker.get("markPrice")
        )

        if value is None:
            continue

        try:
            prices[symbol] = float(
                value
            )
        except Exception:
            continue

    LIVE_PRICES = prices


def get_live_price(
    asset,
):
    return LIVE_PRICES.get(
        get_contract(asset)
    )


def get_all_current_prices():
    load_prices()

    result = {}

    for asset in ASSETS:

        price = get_live_price(
            asset
        )

        if price is not None:
            result[
                get_contract(asset)
            ] = price

    return result


# ============================================================
# CANDLES
# ============================================================

def get_candles(
    asset,
    interval,
    limit=300,
):
    contract = get_contract(
        asset
    )

    url = (
        f"{KRAKEN_CHART_URL}/"
        f"{contract}/{interval}"
    )

    data = http_get(url)

    candles = data.get(
        "candles",
        data,
    )

    if not isinstance(
        candles,
        list,
    ):
        return pd.DataFrame()

    rows = []

    for candle in candles:

        try:

            if isinstance(
                candle,
                dict,
            ):

                timestamp = (
                    candle.get("time")
                    or candle.get(
                        "timestamp"
                    )
                    or candle.get("t")
                )

                open_price = (
                    candle.get("open")
                    if candle.get("open")
                    is not None
                    else candle.get("o")
                )

                high_price = (
                    candle.get("high")
                    if candle.get("high")
                    is not None
                    else candle.get("h")
                )

                low_price = (
                    candle.get("low")
                    if candle.get("low")
                    is not None
                    else candle.get("l")
                )

                close_price = (
                    candle.get("close")
                    if candle.get("close")
                    is not None
                    else candle.get("c")
                )

            else:

                if len(candle) < 5:
                    continue

                timestamp = candle[0]
                open_price = candle[1]
                high_price = candle[2]
                low_price = candle[3]
                close_price = candle[4]

            timestamp = int(
                float(timestamp)
            )

            # Some APIs return milliseconds.
            if timestamp > 10_000_000_000:
                timestamp //= 1000

            rows.append(
                {
                    "timestamp":
                        timestamp,
                    "open":
                        float(open_price),
                    "high":
                        float(high_price),
                    "low":
                        float(low_price),
                    "close":
                        float(close_price),
                }
            )

        except Exception:
            continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        rows
    )

    df = df.drop_duplicates(
        subset=[
            "timestamp"
        ]
    )

    df = df.sort_values(
        "timestamp"
    )

    interval_seconds = {
        "1h": 3600,
        "15m": 900,
        "5m": 300,
    }.get(
        interval,
        900,
    )

    # --------------------------------------------------------
    # CLOSED CANDLES ONLY
    # --------------------------------------------------------

    current_time = now_ts()

    df = df[
        (
            df["timestamp"]
            + interval_seconds
        ) <= current_time
    ]

    if limit:
        df = df.tail(
            limit
        )

    return df.reset_index(
        drop=True
    )


# ============================================================
# PIVOTS
# ============================================================

def is_local_high(
    df,
    index,
    left=SWING_LEFT_RIGHT,
    right=SWING_LEFT_RIGHT,
):
    if index < left:
        return False

    if index + right >= len(df):
        return False

    value = float(
        df.iloc[index]["high"]
    )

    left_values = df.iloc[
        index - left:index
    ]["high"]

    right_values = df.iloc[
        index + 1:
        index + right + 1
    ]["high"]

    return (
        value >= left_values.max()
        and value >= right_values.max()
    )


def is_local_low(
    df,
    index,
    left=SWING_LEFT_RIGHT,
    right=SWING_LEFT_RIGHT,
):
    if index < left:
        return False

    if index + right >= len(df):
        return False

    value = float(
        df.iloc[index]["low"]
    )

    left_values = df.iloc[
        index - left:index
    ]["low"]

    right_values = df.iloc[
        index + 1:
        index + right + 1
    ]["low"]

    return (
        value <= left_values.min()
        and value <= right_values.min()
    )


def pivot_highs(df):
    return [
        i
        for i in range(len(df))
        if is_local_high(
            df,
            i,
        )
    ]


def pivot_lows(df):
    return [
        i
        for i in range(len(df))
        if is_local_low(
            df,
            i,
        )
    ]


# ============================================================
# HELPERS
# ============================================================

def pct_diff(
    a,
    b,
):
    if a == 0:
        return 999.0

    return abs(a - b) / abs(a)


def make_pattern(
    name,
    direction,
    points,
    neckline,
    neckline_idx,
    target,
    stop,
    pattern_time,
    score,
):
    return {
        "pattern":
            name,
        "direction":
            direction,
        "points":
            points,
        "neckline":
            float(neckline),
        "neckline_idx":
            neckline_idx,
        "target":
            float(target),
        "stop":
            float(stop),
        "pattern_time":
            int(pattern_time),
        "score":
            float(score),
    }


# ============================================================
# DOUBLE TOP
# ============================================================

def detect_double_top(
    df,
):
    highs = pivot_highs(df)
    lows = pivot_lows(df)

    patterns = []

    for a in range(
        len(highs)
    ):

        left = highs[a]

        for b in range(
            a + 1,
            len(highs),
        ):

            right = highs[b]

            distance = (
                right - left
            )

            if distance < MIN_PATTERN_SEPARATION_BARS:
                continue

            if distance > MAX_DOUBLE_PATTERN_BARS:
                break

            left_high = float(
                df.iloc[left]["high"]
            )

            right_high = float(
                df.iloc[right]["high"]
            )

            similarity = pct_diff(
                left_high,
                right_high,
            )

            if similarity > DOUBLE_LEVEL_TOLERANCE:
                continue

            between_lows = [
                x
                for x in lows
                if left < x < right
            ]

            if not between_lows:
                continue

            valley = min(
                between_lows,
                key=lambda x:
                    float(
                        df.iloc[x]["low"]
                    ),
            )

            valley_price = float(
                df.iloc[valley]["low"]
            )

            peak = max(
                left_high,
                right_high,
            )

            depth = (
                peak - valley_price
            ) / peak

            if depth < MIN_PATTERN_DEPTH_PCT:
                continue

            neckline = valley_price

            height = (
                peak
                - neckline
            )

            target = (
                neckline
                - height
            )

            stop = (
                peak
                * (
                    1
                    + INVALIDATION_BUFFER
                )
            )

            score = (
                min(
                    10.0,
                    depth * 1000,
                )
                + (
                    1
                    - similarity
                ) * 5
            )

            patterns.append(
                make_pattern(
                    "Double Top",
                    "SHORT",
                    {
                        "left_idx":
                            left,
                        "valley_idx":
                            valley,
                        "right_idx":
                            right,
                    },
                    neckline,
                    valley,
                    target,
                    stop,
                    df.iloc[
                        right
                    ]["timestamp"],
                    score,
                )
            )

    return patterns


# ============================================================
# DOUBLE BOTTOM
# ============================================================

def detect_double_bottom(
    df,
):
    lows = pivot_lows(df)
    highs = pivot_highs(df)

    patterns = []

    for a in range(
        len(lows)
    ):

        left = lows[a]

        for b in range(
            a + 1,
            len(lows),
        ):

            right = lows[b]

            distance = (
                right - left
            )

            if distance < MIN_PATTERN_SEPARATION_BARS:
                continue

            if distance > MAX_DOUBLE_PATTERN_BARS:
                break

            left_low = float(
                df.iloc[left]["low"]
            )

            right_low = float(
                df.iloc[right]["low"]
            )

            similarity = pct_diff(
                left_low,
                right_low,
            )

            if similarity > DOUBLE_LEVEL_TOLERANCE:
                continue

            between_highs = [
                x
                for x in highs
                if left < x < right
            ]

            if not between_highs:
                continue

            peak = max(
                between_highs,
                key=lambda x:
                    float(
                        df.iloc[x]["high"]
                    ),
            )

            peak_price = float(
                df.iloc[peak]["high"]
            )

            bottom = min(
                left_low,
                right_low,
            )

            depth = (
                peak_price
                - bottom
            ) / peak_price

            if depth < MIN_PATTERN_DEPTH_PCT:
                continue

            neckline = peak_price

            height = (
                neckline
                - bottom
            )

            target = (
                neckline
                + height
            )

            stop = (
                bottom
                * (
                    1
                    - INVALIDATION_BUFFER
                )
            )

            score = (
                min(
                    10.0,
                    depth * 1000,
                )
                + (
                    1
                    - similarity
                ) * 5
            )

            patterns.append(
                make_pattern(
                    "Double Bottom",
                    "LONG",
                    {
                        "left_idx":
                            left,
                        "peak_idx":
                            peak,
                        "right_idx":
                            right,
                    },
                    neckline,
                    peak,
                    target,
                    stop,
                    df.iloc[
                        right
                    ]["timestamp"],
                    score,
                )
            )

    return patterns


# ============================================================
# HEAD & SHOULDERS
# ============================================================

def detect_head_shoulders(
    df,
):
    highs = pivot_highs(df)
    lows = pivot_lows(df)

    patterns = []

    for a in range(
        len(highs)
    ):

        left = highs[a]

        for b in range(
            a + 1,
            len(highs),
        ):

            head = highs[b]

            if head - left < MIN_PATTERN_SEPARATION_BARS:
                continue

            if head - left > MAX_HS_PATTERN_BARS:
                break

            for c in range(
                b + 1,
                len(highs),
            ):

                right = highs[c]

                if (
                    right - head
                    < MIN_PATTERN_SEPARATION_BARS
                ):
                    continue

                if (
                    right - left
                    > MAX_HS_PATTERN_BARS
                ):
                    break

                left_price = float(
                    df.iloc[left]["high"]
                )

                head_price = float(
                    df.iloc[head]["high"]
                )

                right_price = float(
                    df.iloc[right]["high"]
                )

                shoulder_diff = pct_diff(
                    left_price,
                    right_price,
                )

                if (
                    shoulder_diff
                    > HS_SHOULDER_TOLERANCE
                ):
                    continue

                if (
                    head_price
                    <= max(
                        left_price,
                        right_price,
                    )
                    * (
                        1
                        + HS_MIN_HEAD_ADVANTAGE_PCT
                    )
                ):
                    continue

                between_1 = [
                    x
                    for x in lows
                    if left < x < head
                ]

                between_2 = [
                    x
                    for x in lows
                    if head < x < right
                ]

                if not between_1 or not between_2:
                    continue

                low1 = min(
                    between_1,
                    key=lambda x:
                        float(
                            df.iloc[x]["low"]
                        ),
                )

                low2 = min(
                    between_2,
                    key=lambda x:
                        float(
                            df.iloc[x]["low"]
                        ),
                )

                neckline = (
                    float(
                        df.iloc[
                            low1
                        ]["low"]
                    )
                    + float(
                        df.iloc[
                            low2
                        ]["low"]
                    )
                ) / 2.0

                depth = (
                    head_price
                    - neckline
                ) / head_price

                if depth < MIN_HS_DEPTH_PCT:
                    continue

                target = (
                    neckline
                    - (
                        head_price
                        - neckline
                    )
                )

                stop = (
                    head_price
                    * (
                        1
                        + INVALIDATION_BUFFER
                    )
                )

                score = (
                    min(
                        10.0,
                        depth * 1000,
                    )
                    + (
                        1
                        - shoulder_diff
                    ) * 5
                )

                patterns.append(
                    make_pattern(
                        "Head & Shoulders",
                        "SHORT",
                        {
                            "left_idx":
                                left,
                            "head_idx":
                                head,
                            "right_idx":
                                right,
                            "neck_left_idx":
                                low1,
                            "neck_right_idx":
                                low2,
                        },
                        neckline,
                        low2,
                        target,
                        stop,
                        df.iloc[
                            right
                        ]["timestamp"],
                        score,
                    )
                )

    return patterns


# ============================================================
# INVERSE HEAD & SHOULDERS
# ============================================================

def detect_inverse_head_shoulders(
    df,
):
    lows = pivot_lows(df)
    highs = pivot_highs(df)

    patterns = []

    for a in range(
        len(lows)
    ):

        left = lows[a]

        for b in range(
            a + 1,
            len(lows),
        ):

            head = lows[b]

            if head - left < MIN_PATTERN_SEPARATION_BARS:
                continue

            if head - left > MAX_HS_PATTERN_BARS:
                break

            for c in range(
                b + 1,
                len(lows),
            ):

                right = lows[c]

                if (
                    right - head
                    < MIN_PATTERN_SEPARATION_BARS
                ):
                    continue

                if (
                    right - left
                    > MAX_HS_PATTERN_BARS
                ):
                    break

                left_price = float(
                    df.iloc[left]["low"]
                )

                head_price = float(
                    df.iloc[head]["low"]
                )

                right_price = float(
                    df.iloc[right]["low"]
                )

                shoulder_diff = pct_diff(
                    left_price,
                    right_price,
                )

                if (
                    shoulder_diff
                    > HS_SHOULDER_TOLERANCE
                ):
                    continue

                if (
                    head_price
                    >= min(
                        left_price,
                        right_price,
                    )
                    * (
                        1
                        - HS_MIN_HEAD_ADVANTAGE_PCT
                    )
                ):
                    continue

                between_1 = [
                    x
                    for x in highs
                    if left < x < head
                ]

                between_2 = [
                    x
                    for x in highs
                    if head < x < right
                ]

                if not between_1 or not between_2:
                    continue

                high1 = max(
                    between_1,
                    key=lambda x:
                        float(
                            df.iloc[x]["high"]
                        ),
                )

                high2 = max(
                    between_2,
                    key=lambda x:
                        float(
                            df.iloc[x]["high"]
                        ),
                )

                neckline = (
                    float(
                        df.iloc[
                            high1
                        ]["high"]
                    )
                    + float(
                        df.iloc[
                            high2
                        ]["high"]
                    )
                ) / 2.0

                depth = (
                    neckline
                    - head_price
                ) / neckline

                if depth < MIN_HS_DEPTH_PCT:
                    continue

                target = (
                    neckline
                    + (
                        neckline
                        - head_price
                    )
                )

                stop = (
                    head_price
                    * (
                        1
                        - INVALIDATION_BUFFER
                    )
                )

                score = (
                    min(
                        10.0,
                        depth * 1000,
                    )
                    + (
                        1
                        - shoulder_diff
                    ) * 5
                )

                patterns.append(
                    make_pattern(
                        "Inverse Head & Shoulders",
                        "LONG",
                        {
                            "left_idx":
                                left,
                            "head_idx":
                                head,
                            "right_idx":
                                right,
                            "neck_left_idx":
                                high1,
                            "neck_right_idx":
                                high2,
                        },
                        neckline,
                        high2,
                        target,
                        stop,
                        df.iloc[
                            right
                        ]["timestamp"],
                        score,
                    )
                )

    return patterns


# ============================================================
# ALL PATTERNS
# ============================================================

def detect_patterns(
    df,
):
    patterns = []

    patterns.extend(
        detect_double_top(
            df
        )
    )

    patterns.extend(
        detect_double_bottom(
            df
        )
    )

    patterns.extend(
        detect_head_shoulders(
            df
        )
    )

    patterns.extend(
        detect_inverse_head_shoulders(
            df
        )
    )

    # Remove duplicates.
    unique = {}

    for p in patterns:

        key = (
            p["pattern"],
            p["direction"],
            p["pattern_time"],
            round(
                p["neckline"],
                10,
            ),
        )

        old = unique.get(key)

        if (
            old is None
            or p["score"]
            > old["score"]
        ):
            unique[key] = p

    return list(
        unique.values()
    )


# ============================================================
# 1H PATTERN
# ============================================================

def find_1h_pattern(
    df1h,
):
    patterns = detect_patterns(
        df1h
    )

    if not patterns:
        return None

    cutoff = (
        now_ts()
        - RECENT_PATTERN_HOURS_1H
        * 3600
    )

    candidates = [
        p
        for p in patterns
        if p["pattern_time"]
        >= cutoff
    ]

    if not candidates:
        return None

    candidates.sort(
        key=lambda p: (
            p["pattern_time"],
            p["score"],
        ),
        reverse=True,
    )

    return candidates[0]


# ============================================================
# 1H BREAKOUT
# ============================================================

def find_1h_breakout(
    df1h,
    pattern,
):
    pattern_time = (
        pattern["pattern_time"]
    )

    end_time = (
        pattern_time
        + BREAKOUT_LOOKAHEAD_1H
        * 3600
    )

    direction = (
        pattern["direction"]
    )

    for i in range(
        len(df1h)
    ):

        row = df1h.iloc[i]

        ts = int(
            row["timestamp"]
        )

        if ts <= pattern_time:
            continue

        if ts > end_time:
            break

        close = float(
            row["close"]
        )

        neckline = float(
            pattern["neckline"]
        )

        if direction == "LONG":

            if close > neckline:
                return {
                    "index":
                        i,
                    "timestamp":
                        ts,
                    "price":
                        close,
                }

        else:

            if close < neckline:
                return {
                    "index":
                        i,
                    "timestamp":
                        ts,
                    "price":
                        close,
                }

    return None


# ============================================================
# 15M PATTERN
# ============================================================

def find_15m_pattern(
    df15,
    breakout_1h,
    required_direction,
):
    patterns = detect_patterns(
        df15
    )

    cutoff_start = (
        breakout_1h["timestamp"]
    )

    cutoff_end = (
        cutoff_start
        + RECENT_PATTERN_HOURS_15M
        * 3600
    )

    candidates = []

    for pattern in patterns:

        # Pattern name may differ.
        # Direction MUST match 1H.
        if (
            pattern["direction"]
            != required_direction
        ):
            continue

        pt = pattern[
            "pattern_time"
        ]

        if pt <= cutoff_start:
            continue

        if pt > cutoff_end:
            continue

        candidates.append(
            pattern
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda p: (
            p["pattern_time"],
            p["score"],
        ),
        reverse=True,
    )

    return candidates[0]


# ============================================================
# 15M BREAKOUT
# ============================================================

def find_15m_breakout(
    df15,
    pattern15,
):
    pattern_time = (
        pattern15["pattern_time"]
    )

    end_time = (
        pattern_time
        + BREAKOUT_LOOKAHEAD_15M
        * 900
    )

    direction = (
        pattern15["direction"]
    )

    for i in range(
        len(df15)
    ):

        row = df15.iloc[i]

        ts = int(
            row["timestamp"]
        )

        if ts <= pattern_time:
            continue

        if ts > end_time:
            break

        close = float(
            row["close"]
        )

        neckline = float(
            pattern15["neckline"]
        )

        if direction == "LONG":

            if close > neckline:
                return {
                    "index":
                        i,
                    "timestamp":
                        ts,
                    "price":
                        close,
                }

        else:

            if close < neckline:
                return {
                    "index":
                        i,
                    "timestamp":
                        ts,
                    "price":
                        close,
                }

    return None


# ============================================================
# FIRST RETEST
# ============================================================

def find_first_retest(
    df15,
    pattern15,
    breakout15,
):
    neckline = float(
        pattern15["neckline"]
    )

    direction = (
        pattern15["direction"]
    )

    start_index = (
        breakout15["index"]
        + 1
    )

    end_index = min(
        len(df15),
        start_index
        + RETEST_LOOKAHEAD_15M
        + 1,
    )

    for i in range(
        start_index,
        end_index,
    ):

        row = df15.iloc[i]

        high = float(
            row["high"]
        )

        low = float(
            row["low"]
        )

        close = float(
            row["close"]
        )

        touched = (
            low
            <= neckline
            <= high
        )

        if not touched:
            continue

        if direction == "LONG":

            if close >= neckline:

                return {
                    "index":
                        i,
                    "timestamp":
                        int(
                            row[
                                "timestamp"
                            ]
                        ),
                    "price":
                        close,
                }

        else:

            if close <= neckline:

                return {
                    "index":
                        i,
                    "timestamp":
                        int(
                            row[
                                "timestamp"
                            ]
                        ),
                    "price":
                        close,
                }

    return None


# ============================================================
# CONFIRMATION
# ============================================================

def find_confirmation(
    df15,
    pattern15,
    retest15,
):
    direction = (
        pattern15["direction"]
    )

    neckline = float(
        pattern15["neckline"]
    )

    start_index = (
        retest15["index"]
        + 1
    )

    end_index = min(
        len(df15),
        start_index
        + CONFIRMATION_LOOKAHEAD_15M
        + 1,
    )

    for i in range(
        start_index,
        end_index,
    ):

        row = df15.iloc[i]

        open_price = float(
            row["open"]
        )

        close = float(
            row["close"]
        )

        high = float(
            row["high"]
        )

        low = float(
            row["low"]
        )

        ts = int(
            row["timestamp"]
        )

        if direction == "LONG":

            bullish = (
                close
                > open_price
            )

            if (
                bullish
                and close
                > neckline
            ):

                return {
                    "index":
                        i,
                    "timestamp":
                        ts,
                    "price":
                        close,
                    "high":
                        high,
                    "low":
                        low,
                }

        else:

            bearish = (
                close
                < open_price
            )

            if (
                bearish
                and close
                < neckline
            ):

                return {
                    "index":
                        i,
                    "timestamp":
                        ts,
                    "price":
                        close,
                    "high":
                        high,
                    "low":
                        low,
                }

    return None


# ============================================================
# TRADE LEVEL VALIDATION
# ============================================================

def validate_trade_levels(
    direction,
    entry,
    tp,
    sl,
):
    if direction == "LONG":

        return (
            tp > entry
            and sl < entry
        )

    return (
        tp < entry
        and sl > entry
    )


# ============================================================
# ANALYZE ASSET
# ============================================================

def analyze_asset(
    asset,
    symbol,
):
    global BEST_CANDIDATE

    # --------------------------------------------------------
    # 1H DATA
    # --------------------------------------------------------

    df1h = get_candles(
        asset,
        "1h",
        PATTERN_LOOKBACK_1H,
    )

    if (
        df1h.empty
        or len(df1h) < 60
    ):
        return None

    # --------------------------------------------------------
    # 15M DATA
    # --------------------------------------------------------

    df15 = get_candles(
        asset,
        "15m",
        PATTERN_LOOKBACK_15M,
    )

    if (
        df15.empty
        or len(df15) < 60
    ):
        return None

    # --------------------------------------------------------
    # 1H PATTERN
    # --------------------------------------------------------

    pattern1h = find_1h_pattern(
        df1h
    )

    if not pattern1h:
        return None

    STATS[
        "patterns_1h"
    ] += 1

    # --------------------------------------------------------
    # 1H BREAKOUT
    # --------------------------------------------------------

    breakout1h = find_1h_breakout(
        df1h,
        pattern1h,
    )

    if not breakout1h:
        return None

    STATS[
        "breakouts_1h"
    ] += 1

    direction = (
        pattern1h["direction"]
    )

    # --------------------------------------------------------
    # 15M PATTERN
    # --------------------------------------------------------

    pattern15 = find_15m_pattern(
        df15,
        breakout1h,
        direction,
    )

    if not pattern15:
        return None

    STATS[
        "patterns_15m"
    ] += 1

    # --------------------------------------------------------
    # 15M BREAKOUT
    # --------------------------------------------------------

    breakout15 = find_15m_breakout(
        df15,
        pattern15,
    )

    if not breakout15:
        return None

    STATS[
        "breakouts_15m"
    ] += 1

    # --------------------------------------------------------
    # FIRST RETEST
    # --------------------------------------------------------

    retest15 = find_first_retest(
        df15,
        pattern15,
        breakout15,
    )

    if not retest15:
        return None

    STATS[
        "retests_15m"
    ] += 1

    # --------------------------------------------------------
    # FIRST CONFIRMATION
    # --------------------------------------------------------

    confirmation15 = find_confirmation(
        df15,
        pattern15,
        retest15,
    )

    if not confirmation15:
        return None

    STATS[
        "confirmations_15m"
    ] += 1

    # --------------------------------------------------------
    # FRESHNESS FILTER
    # --------------------------------------------------------
    #
    # IMPORTANT:
    #
    # confirmation timestamp = candle START
    #
    # Therefore we calculate age AFTER the 15M candle
    # has actually closed.
    # --------------------------------------------------------

    age_minutes = (
        confirmation_age_minutes(
            confirmation15[
                "timestamp"
            ]
        )
    )

    if age_minutes < 0:
        return None

    if (
        age_minutes
        > MAX_CONFIRMATION_AGE_MINUTES
    ):

        STATS[
            "stale_confirmations"
        ] += 1

        return None

    STATS[
        "fresh_confirmations"
    ] += 1

    # --------------------------------------------------------
    # ENTRY
    # --------------------------------------------------------

    entry = float(
        confirmation15["price"]
    )

    tp = float(
        pattern15["target"]
    )

    sl = float(
        pattern15["stop"]
    )

    # --------------------------------------------------------
    # LEVEL VALIDATION
    # --------------------------------------------------------

    if not validate_trade_levels(
        direction,
        entry,
        tp,
        sl,
    ):
        return None

    # --------------------------------------------------------
    # SIGNAL KEY
    # --------------------------------------------------------

    signal_key = (
        f"{asset}|"
        f"{direction}|"
        f"{int(confirmation15['timestamp'])}"
    )

    # --------------------------------------------------------
    # DATABASE DUPLICATE CHECK
    # --------------------------------------------------------

    if has_signal(
        signal_key
    ):
        return None

    # --------------------------------------------------------
    # ONE OPEN TRADE PER ASSET + DIRECTION
    # --------------------------------------------------------

    if has_open_trade(
        asset,
        direction,
    ):
        return None

    # --------------------------------------------------------
    # BUILD TRADE
    # --------------------------------------------------------

    trade = {
        "symbol":
            symbol,

        "asset":
            asset,

        "direction":
            direction,

        "pattern":
            pattern15["pattern"],

        "signal_key":
            signal_key,

        "pattern1h":
            pattern1h,

        "breakout1h":
            breakout1h,

        "pattern15m":
            pattern15,

        "breakout15m":
            breakout15,

        "retest15m":
            retest15,

        "confirmation15m":
            confirmation15,

        "entry_time":
            int(
                confirmation15[
                    "timestamp"
                ]
            ),

        "entry_price":
            entry,

        "tp_price":
            tp,

        "sl_price":
            sl,

        "confirmation_age":
            age_minutes,

        "df15":
            df15.copy(),
    }

    STATS[
        "candidates"
    ] += 1

    # --------------------------------------------------------
    # BEST CANDIDATE
    # --------------------------------------------------------

    BEST_CANDIDATE = {
        "asset":
            asset,
        "stage":
            "Signal Ready",
        "score":
            pattern15["score"],
        "reason":
            (
                f"{pattern15['pattern']} | "
                f"Confirmation age "
                f"{age_minutes:.1f}m"
            ),
    }

    return trade


# ============================================================
# CHART HELPERS
# ============================================================

def draw_annotation(
    ax,
    x,
    y,
    text,
    offset_y=10,
):
    ax.annotate(
        text,
        (
            x,
            y,
        ),
        xytext=(
            0,
            offset_y,
        ),
        textcoords="offset points",
        ha="center",
        va="center",
        fontsize=9,
        fontweight="bold",
        bbox={
            "boxstyle":
                "round,pad=0.25",
            "alpha":
                0.85,
        },
    )


def mark_chart_point(
    ax,
    chart_df,
    original_df,
    idx,
    label,
    prefer_high=True,
):
    if idx is None:
        return

    if idx not in chart_df.index:
        return

    start_index = chart_df.index[0]

    x = (
        idx
        - start_index
    )

    row = original_df.iloc[
        idx
    ]

    if prefer_high:
        y = float(
            row["high"]
        )
        offset = 12
    else:
        y = float(
            row["low"]
        )
        offset = -16

    draw_annotation(
        ax,
        x,
        y,
        label,
        offset,
    )


# ============================================================
# CHART
# ============================================================

def make_chart(
    trade,
):
    df = trade.get(
        "df15"
    )

    if (
        df is None
        or df.empty
    ):
        return None

    chart_df = df.tail(
        CHART_CANDLES
    ).copy()

    if chart_df.empty:
        return None

    fig, ax = plt.subplots(
        figsize=(
            16,
            9,
        )
    )

    # --------------------------------------------------------
    # CANDLES
    # --------------------------------------------------------

    start_index = chart_df.index[0]

    for idx, row in chart_df.iterrows():

        x = (
            idx
            - start_index
        )

        open_price = float(
            row["open"]
        )

        close = float(
            row["close"]
        )

        high = float(
            row["high"]
        )

        low = float(
            row["low"]
        )

        ax.vlines(
            x,
            low,
            high,
            linewidth=1,
        )

        bottom = min(
            open_price,
            close,
        )

        height = abs(
            close
            - open_price
        )

        if height == 0:
            height = max(
                close
                * 0.00005,
                1e-12,
            )

        ax.bar(
            x,
            height,
            bottom=bottom,
            width=0.65,
        )

    pattern = trade[
        "pattern15m"
    ]

    # --------------------------------------------------------
    # IMPORTANT LEVELS
    # --------------------------------------------------------

    ax.axhline(
        pattern[
            "neckline"
        ],
        linestyle="--",
        linewidth=1.3,
        label="NECKLINE",
    )

    ax.axhline(
        trade[
            "entry_price"
        ],
        linestyle="-",
        linewidth=1.5,
        label="ENTRY",
    )

    ax.axhline(
        trade[
            "tp_price"
        ],
        linestyle="--",
        linewidth=1.3,
        label="TP",
    )

    ax.axhline(
        trade[
            "sl_price"
        ],
        linestyle="--",
        linewidth=1.3,
        label="SL",
    )

    # --------------------------------------------------------
    # PATTERN POINTS
    # --------------------------------------------------------

    p = pattern

    if p["pattern"] == "Double Top":

        mark_chart_point(
            ax,
            chart_df,
            df,
            p.get("left_idx"),
            "P1",
            True,
        )

        mark_chart_point(
            ax,
            chart_df,
            df,
            p.get("right_idx"),
            "P2",
            True,
        )

        mark_chart_point(
            ax,
            chart_df,
            df,
            p.get("valley_idx"),
            "NL",
            False,
        )

    elif p["pattern"] == "Double Bottom":

        mark_chart_point(
            ax,
            chart_df,
            df,
            p.get("left_idx"),
            "P1",
            False,
        )

        mark_chart_point(
            ax,
            chart_df,
            df,
            p.get("right_idx"),
            "P2",
            False,
        )

        mark_chart_point(
            ax,
            chart_df,
            df,
            p.get("peak_idx"),
            "NL",
            True,
        )

    elif p["pattern"] == "Head & Shoulders":

        mark_chart_point(
            ax,
            chart_df,
            df,
            p.get("left_idx"),
            "LS",
            True,
        )

        mark_chart_point(
            ax,
            chart_df,
            df,
            p.get("head_idx"),
            "H",
            True,
        )

        mark_chart_point(
            ax,
            chart_df,
            df,
            p.get("right_idx"),
            "RS",
            True,
        )

        mark_chart_point(
            ax,
            chart_df,
            df,
            p.get("neck_left_idx"),
            "NL1",
            False,
        )

        mark_chart_point(
            ax,
            chart_df,
            df,
            p.get("neck_right_idx"),
            "NL2",
            False,
        )

    elif (
        p["pattern"]
        == "Inverse Head & Shoulders"
    ):

        mark_chart_point(
            ax,
            chart_df,
            df,
            p.get("left_idx"),
            "LS",
            False,
        )

        mark_chart_point(
            ax,
            chart_df,
            df,
            p.get("head_idx"),
            "H",
            False,
        )

        mark_chart_point(
            ax,
            chart_df,
            df,
            p.get("right_idx"),
            "RS",
            False,
        )

        mark_chart_point(
            ax,
            chart_df,
            df,
            p.get("neck_left_idx"),
            "NL1",
            True,
        )

        mark_chart_point(
            ax,
            chart_df,
            df,
            p.get("neck_right_idx"),
            "NL2",
            True,
        )

    # --------------------------------------------------------
    # BREAKOUT / RETEST / CONFIRMATION / ENTRY
    # --------------------------------------------------------

    def stage_point(
        stage,
        label,
        prefer_high=True,
    ):
        item = trade.get(
            stage
        )

        if not item:
            return

        idx = item.get(
            "index"
        )

        if idx is None:
            return

        if idx not in chart_df.index:
            return

        x = (
            idx
            - start_index
        )

        row = df.iloc[
            idx
        ]

        if prefer_high:
            y = float(
                row["high"]
            )
            offset = 24
        else:
            y = float(
                row["low"]
            )
            offset = -28

        draw_annotation(
            ax,
            x,
            y,
            label,
            offset,
        )

    direction = trade[
        "direction"
    ]

    if direction == "LONG":

        stage_point(
            "breakout15m",
            "BO",
            True,
        )

        stage_point(
            "retest15m",
            "RT",
            False,
        )

        stage_point(
            "confirmation15m",
            "CONF",
            True,
        )

    else:

        stage_point(
            "breakout15m",
            "BO",
            False,
        )

        stage_point(
            "retest15m",
            "RT",
            True,
        )

        stage_point(
            "confirmation15m",
            "CONF",
            False,
        )

    # Entry marker
    confirmation = trade[
        "confirmation15m"
    ]

    if (
        confirmation
        and confirmation["index"]
        in chart_df.index
    ):

        x = (
            confirmation["index"]
            - start_index
        )

        ax.scatter(
            [x],
            [trade["entry_price"]],
            marker="o",
            s=50,
            zorder=5,
            label="ENTRY POINT",
        )

        draw_annotation(
            ax,
            x,
            trade["entry_price"],
            "ENTRY",
            34,
        )

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    ax.set_title(
        (
            f"{trade['asset']} "
            f"{trade['direction']} | "
            f"15M "
            f"{trade['pattern15m']['pattern']}"
        ),
        fontsize=14,
        fontweight="bold",
    )

    ax.set_xlabel(
        "15M Closed Candles"
    )

    ax.set_ylabel(
        "Price"
    )

    ax.grid(
        alpha=0.20
    )

    ax.legend(
        loc="best",
        fontsize=8,
    )

    plt.tight_layout()

    filename = (
        f"signal_"
        f"{trade['asset']}_"
        f"{int(time.time())}.png"
    )

    plt.savefig(
        filename,
        dpi=150,
        bbox_inches="tight",
    )

    plt.close(fig)

    return filename


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def build_signal_message(
    trade,
):
    direction = trade[
        "direction"
    ]

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    p1 = trade[
        "pattern1h"
    ]

    p15 = trade[
        "pattern15m"
    ]

    age = trade[
        "confirmation_age"
    ]

    return f"""
📊 <b>KRAKEN PATTERN SCANNER</b>

{emoji} <b>{trade['asset']} {direction}</b>

📌 <b>15M Pattern:</b> {p15['pattern']}
📌 <b>1H Pattern:</b> {p1['pattern']}

💰 <b>Entry:</b> {trade['entry_price']:.10g}
🎯 <b>TP:</b> {trade['tp_price']:.10g}
🛑 <b>SL:</b> {trade['sl_price']:.10g}

📐 <b>Pattern Score:</b> {p15['score']:.2f}

🕐 <b>1H Pattern:</b> {format_time(p1['pattern_time'])}
🕐 <b>1H Breakout:</b> {format_time(trade['breakout1h']['timestamp'])}

🕐 <b>15M Pattern:</b> {format_time(p15['pattern_time'])}
🕐 <b>15M Breakout:</b> {format_time(trade['breakout15m']['timestamp'])}
🕐 <b>15M Retest:</b> {format_time(trade['retest15m']['timestamp'])}
🕐 <b>15M Confirmation:</b> {format_time(trade['confirmation15m']['timestamp'])}

⏱ <b>Confirmation Age:</b> {age:.1f} min
⏱ <b>Entry Time:</b> {format_time(trade['entry_time'])}

⚙️ <b>Mode:</b> PAPER ONLY
"""


# ============================================================
# OPEN TRADES MESSAGE
# ============================================================

def build_open_trades_message():
    rows = get_open_trades()

    if not rows:
        return (
            "🟢 <b>OPEN TRADES: 0</b>"
        )

    prices = get_all_current_prices()

    lines = [
        f"🟢 <b>OPEN TRADES: {len(rows)}</b>",
        "",
    ]

    for row in rows:

        asset = row[
            "asset"
        ]

        direction = row[
            "direction"
        ]

        entry = float(
            row["entry_price"]
        )

        tp = float(
            row["tp_price"]
        )

        sl = float(
            row["sl_price"]
        )

        current = prices.get(
            row["symbol"]
        )

        if current is None:
            current = entry

        if direction == "LONG":

            pnl = (
                (
                    current
                    - entry
                )
                / entry
            ) * 100

        else:

            pnl = (
                (
                    entry
                    - current
                )
                / entry
            ) * 100

        emoji = (
            "🟢"
            if direction == "LONG"
            else "🔴"
        )

        lines.extend(
            [
                (
                    f"{emoji} "
                    f"<b>{asset} "
                    f"{direction}</b>"
                ),
                (
                    f"📌 Pattern: "
                    f"{row['pattern']}"
                ),
                (
                    f"💰 Entry: "
                    f"{entry:.10g}"
                ),
                (
                    f"💵 Current: "
                    f"{current:.10g} "
                    f"({pnl:+.2f}%)"
                ),
                (
                    f"🛑 SL: "
                    f"{sl:.10g}"
                ),
                (
                    f"🎯 TP: "
                    f"{tp:.10g}"
                ),
                (
                    f"⏱ Duration: "
                    f"{duration_text(row['entry_time'])}"
                ),
                "",
            ]
        )

    return "\n".join(
        lines
    )


# ============================================================
# PERFORMANCE
# ============================================================

def build_performance_message():
    conn = db_connect()

    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(
                CASE
                    WHEN status='CLOSED'
                    THEN 1
                    ELSE 0
                END
            ) AS closed,
            SUM(
                CASE
                    WHEN status='CLOSED'
                     AND exit_reason='TP'
                    THEN 1
                    ELSE 0
                END
            ) AS wins,
            SUM(
                CASE
                    WHEN status='CLOSED'
                     AND exit_reason='SL'
                    THEN 1
                    ELSE 0
                END
            ) AS losses
        FROM trades
        """
    ).fetchone()

    conn.close()

    total = int(
        row["total"] or 0
    )

    closed = int(
        row["closed"] or 0
    )

    wins = int(
        row["wins"] or 0
    )

    losses = int(
        row["losses"] or 0
    )

    if closed:
        win_rate = (
            wins
            / closed
        ) * 100
    else:
        win_rate = 0.0

    return (
        "📈 <b>PERFORMANCE</b>\n\n"
        f"Trades: {total}\n"
        f"Closed: {closed}\n"
        f"TP: {wins}\n"
        f"SL: {losses}\n"
        f"Win Rate: {win_rate:.2f}%"
    )


# ============================================================
# SCAN SUMMARY
# ============================================================

def build_scan_summary():
    scanned = STATS[
        "assets_scanned"
    ]

    total_assets = len(
        ASSETS
    )

    lines = [
        "📊 <b>KRAKEN PATTERN SCANNER</b>",
        "",
        f"<b>Version:</b> {VERSION}",
        "<b>Mode:</b> PAPER ONLY",
        f"<b>Time:</b> {format_time(now_ts())}",
        "",
        "🔎 <b>SCAN SUMMARY</b>",
        "",
        (
            f"Assets Scanned: "
            f"{scanned}/{total_assets}"
        ),
        (
            f"1H Patterns: "
            f"{STATS['patterns_1h']}"
        ),
        (
            f"1H Breakouts: "
            f"{STATS['breakouts_1h']}"
        ),
        (
            f"15M Patterns: "
            f"{STATS['patterns_15m']}"
        ),
        (
            f"15M Breakouts: "
            f"{STATS['breakouts_15m']}"
        ),
        (
            f"15M Retests: "
            f"{STATS['retests_15m']}"
        ),
        (
            f"15M Confirmations: "
            f"{STATS['confirmations_15m']}"
        ),
        (
            f"Fresh Confirmations: "
            f"{STATS['fresh_confirmations']}"
        ),
        (
            f"Stale Confirmations: "
            f"{STATS['stale_confirmations']}"
        ),
        (
            f"New Signals: "
            f"{STATS['signals_sent']}"
        ),
        (
            f"Errors: "
            f"{STATS['errors']}"
        ),
        "",
    ]

    if BEST_CANDIDATE:

        lines.extend(
            [
                "🏆 <b>BEST CANDIDATE</b>",
                "",
                (
                    f"Asset: "
                    f"{BEST_CANDIDATE['asset']}"
                ),
                (
                    f"Stage: "
                    f"{BEST_CANDIDATE['stage']}"
                ),
                (
                    f"Score: "
                    f"{BEST_CANDIDATE['score']:.2f}"
                ),
                (
                    f"Info: "
                    f"{BEST_CANDIDATE['reason']}"
                ),
                "",
            ]
        )

    else:

        if (
            scanned
            < total_assets
        ):

            lines.extend(
                [
                    "⚠️ <b>SCAN INCOMPLETE</b>",
                    "",
                    "Some assets failed during data retrieval.",
                    "",
                ]
            )

        else:

            lines.extend(
                [
                    "ℹ️ <b>NO VIABLE CANDIDATE</b>",
                    "",
                    (
                        "No fresh valid setup "
                        "reached Entry."
                    ),
                    "",
                ]
            )

    lines.append(
        build_open_trades_message()
    )

    lines.append("")

    lines.append(
        build_performance_message()
    )

    return "\n".join(
        lines
    )


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade_id,
    exit_price,
    reason,
):
    conn = db_connect()

    try:

        conn.execute(
            """
            UPDATE trades
            SET
                status='CLOSED',
                exit_time=?,
                exit_price=?,
                exit_reason=?,
                current_price=?,
                updated_at=?
            WHERE id=?
              AND status='OPEN'
            """,
            (
                now_ts(),
                exit_price,
                reason,
                exit_price,
                now_ts(),
                trade_id,
            ),
        )

        conn.commit()

    finally:
        conn.close()


# ============================================================
# RECONCILE OPEN TRADES
# ============================================================

def reconcile_open_trades():
    rows = get_open_trades()

    if not rows:
        return

    prices = get_all_current_prices()

    for row in rows:

        symbol = str(
            row["symbol"]
        ).upper()

        current = prices.get(
            symbol
        )

        if current is None:
            continue

        entry = float(
            row["entry_price"]
        )

        tp = float(
            row["tp_price"]
        )

        sl = float(
            row["sl_price"]
        )

        direction = row[
            "direction"
        ]

        # ----------------------------------------------------
        # UPDATE CURRENT PRICE
        # ----------------------------------------------------

        conn = db_connect()

        try:
            conn.execute(
                """
                UPDATE trades
                SET
                    current_price=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    current,
                    now_ts(),
                    row["id"],
                ),
            )

            conn.commit()

        finally:
            conn.close()

        # ----------------------------------------------------
        # MAX HOLD
        # ----------------------------------------------------

        if (
            row["entry_time"]
            and (
                now_ts()
                - int(
                    row["entry_time"]
                )
            )
            >= MAX_HOLD_HOURS
            * 3600
        ):

            close_trade(
                row["id"],
                current,
                "TIME",
            )

            STATS[
                "closed_trades"
            ] += 1

            message = f"""
📕 <b>CLOSE {row['asset']} {direction}</b>

📌 Pattern: {row['pattern']}

💰 Entry: {entry:.10g}
💵 Exit: {current:.10g}

⏱ Duration: {duration_text(row['entry_time'])}

⚠️ Reason: TIME
"""

            send_telegram(
                message
            )

            continue

        # ----------------------------------------------------
        # TP / SL
        # ----------------------------------------------------

        reason = None

        if direction == "LONG":

            if current >= tp:
                reason = "TP"

            elif current <= sl:
                reason = "SL"

        else:

            if current <= tp:
                reason = "TP"

            elif current >= sl:
                reason = "SL"

        if not reason:
            continue

        close_trade(
            row["id"],
            current,
            reason,
        )

        STATS[
            "closed_trades"
        ] += 1

        if direction == "LONG":

            pnl = (
                (
                    current
                    - entry
                )
                / entry
            ) * 100

        else:

            pnl = (
                (
                    entry
                    - current
                )
                / entry
            ) * 100

        emoji = (
            "🎯"
            if reason == "TP"
            else "🛑"
        )

        message = f"""
{emoji} <b>CLOSE {row['asset']} {direction}</b>

📌 Pattern: {row['pattern']}

💰 Entry: {entry:.10g}
💵 Exit: {current:.10g}

📊 PnL: {pnl:+.2f}%

⏱ Duration: {duration_text(row['entry_time'])}

<b>Reason:</b> {reason}
"""

        send_telegram(
            message
        )


# ============================================================
# SCAN
# ============================================================

def scan():
    global BEST_CANDIDATE

    STATS["scans"] += 1

    STATS[
        "assets_scanned"
    ] = 0

    for key in (
        "patterns_1h",
        "breakouts_1h",
        "patterns_15m",
        "breakouts_15m",
        "retests_15m",
        "confirmations_15m",
        "fresh_confirmations",
        "stale_confirmations",
        "candidates",
    ):
        STATS[key] = 0

    BEST_CANDIDATE = None

    try:
        load_prices()
    except Exception as exc:
        print(
            "Price loading error:",
            exc,
        )

    candidates = []

    # --------------------------------------------------------
    # SCAN ALL 40 ASSETS
    # --------------------------------------------------------

    for asset in ASSETS:

        symbol = CONTRACTS[
            asset
        ]

        try:

            trade = analyze_asset(
                asset,
                symbol,
            )

            STATS[
                "assets_scanned"
            ] += 1

            if trade:
                candidates.append(
                    trade
                )

        except Exception as exc:

            STATS[
                "errors"
            ] += 1

            print(
                f"{asset} scan error:",
                exc,
            )

            traceback.print_exc()

    # --------------------------------------------------------
    # SORT CANDIDATES
    # --------------------------------------------------------

    candidates.sort(
        key=lambda t: (
            t["pattern15m"][
                "score"
            ],
            t["confirmation_age"],
        ),
        reverse=True,
    )

    # --------------------------------------------------------
    # SEND NEW SIGNAL
    # --------------------------------------------------------

    sent = 0

    for trade in candidates:

        if sent >= MAX_SIGNALS_PER_SCAN:
            break

        inserted = insert_trade(
            trade
        )

        if not inserted:
            continue

        STATS[
            "unique_entries"
        ] += 1

        message = build_signal_message(
            trade
        )

        chart_path = None

        try:

            chart_path = make_chart(
                trade
            )

            if chart_path:

                caption = message

                ok = telegram_send_photo(
                    chart_path,
                    caption,
                )

                if ok:
                    sent += 1
                    STATS[
                        "signals_sent"
                    ] += 1

                else:

                    # If photo fails, still try text.
                    if send_telegram(
                        message
                    ):
                        sent += 1
                        STATS[
                            "signals_sent"
                        ] += 1

            else:

                if send_telegram(
                    message
                ):
                    sent += 1
                    STATS[
                        "signals_sent"
                    ] += 1

        except Exception as exc:

            print(
                "Signal chart/send error:",
                exc,
            )

            if send_telegram(
                message
            ):
                sent += 1
                STATS[
                    "signals_sent"
                ] += 1

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

    # --------------------------------------------------------
    # PERIODIC REPORT
    # --------------------------------------------------------

    last_report = get_meta(
        "last_periodic_report"
    )

    should_report = False

    if last_report is None:
        should_report = True

    else:

        try:

            should_report = (
                now_ts()
                - int(last_report)
                >= PERIODIC_REPORT_SECONDS
            )

        except Exception:
            should_report = True

    if should_report:

        try:
            report = build_scan_summary()

            send_telegram(
                report
            )

            set_meta(
                "last_periodic_report",
                now_ts(),
            )

        except Exception as exc:

            print(
                "Periodic report error:",
                exc,
            )


# ============================================================
# MAIN
# ============================================================

def main():
    print(
        "=" * 60
    )

    print(
        f"KRAKEN PATTERN SCANNER "
        f"VERSION {VERSION}"
    )

    print(
        "MODE: PAPER ONLY"
    )

    print(
        f"Assets: {len(ASSETS)}"
    )

    print(
        "Confirmation freshness: "
        f"{MAX_CONFIRMATION_AGE_MINUTES} minutes "
        "after candle close"
    )

    print(
        "=" * 60
    )

    init_db()

    # --------------------------------------------------------
    # FIRST: RECONCILE EXISTING TRADES
    # --------------------------------------------------------

    try:
        reconcile_open_trades()

    except Exception as exc:

        STATS[
            "errors"
        ] += 1

        print(
            "Reconcile error:",
            exc,
        )

        traceback.print_exc()

    # --------------------------------------------------------
    # SCAN
    # --------------------------------------------------------

    try:
        scan()

    except Exception as exc:

        STATS[
            "errors"
        ] += 1

        print(
            "Scan error:",
            exc,
        )

        traceback.print_exc()

    # --------------------------------------------------------
    # FINAL LOG
    # --------------------------------------------------------

    print(
        "=" * 60
    )

    print(
        "SCAN COMPLETE"
    )

    print(
        f"Assets: "
        f"{STATS['assets_scanned']}/"
        f"{len(ASSETS)}"
    )

    print(
        f"Fresh confirmations: "
        f"{STATS['fresh_confirmations']}"
    )

    print(
        f"Stale confirmations: "
        f"{STATS['stale_confirmations']}"
    )

    print(
        f"Signals sent: "
        f"{STATS['signals_sent']}"
    )

    print(
        f"Errors: "
        f"{STATS['errors']}"
    )

    print(
        "=" * 60
    )


if __name__ == "__main__":
    main()
