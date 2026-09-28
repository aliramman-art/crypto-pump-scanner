# ============================================================
# NDS M30 -> M1 LIVE SCANNER
# VERSION 4.3.6
# ============================================================
#
# PAPER ONLY
#
# M30 SHORT HOOK:
#   H1 -> L1 -> H2 -> L2 -> H3
#   H2 > H1
#   L2 < L1
#   H3 > H2
#
# M30 LONG HOOK:
#   L1 -> H1 -> L2 -> H2 -> L3
#   L2 < L1
#   H2 > H1
#   L3 < L2
#
# M1 SHORT 123F:
#   1 < 2 < 3 < F
#
# M1 LONG 123F:
#   1 > 2 > 3 > F
#
# PAPER ONLY
# ============================================================

import os
import time
import sqlite3
import traceback
from pathlib import Path
from datetime import datetime, timezone

import requests
import pandas as pd
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


# ============================================================
# CONFIG
# ============================================================

VERSION = "4.3.6"

PAPER_ONLY = True

DB_FILE = "nds_m30_m1_v43.db"

CHART_DIR = Path("nds_charts")

KRAKEN_CHART_BASE = (
    "https://futures.kraken.com/api/charts/v1"
)

KRAKEN_API_BASE = (
    "https://futures.kraken.com/derivatives/api/v3"
)

TELEGRAM_BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("TELEGRAM_TOKEN")
    or ""
)

TELEGRAM_CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or os.getenv("TELEGRAM_CHAT")
    or ""
)

TARGET_ASSETS = 100

M30_CANDLES = 320
M1_CANDLES = 1500

M30_INTERVAL = "30m"
M1_INTERVAL = "1m"

M30_SECONDS = 30 * 60
M1_SECONDS = 60

M30_SCAN_SECONDS = 300
M1_SCAN_SECONDS = 60

REPORT_SECONDS = 900

RUN_DURATION_SECONDS = 12 * 60

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

M30_MIN_HOOK_RANGE_PCT = 0.20

M1_MIN_SWING_PCT = 0.07

M1_MAX_STRUCTURE_DISTANCE_PCT = 3.0

MAX_HOOK_AGE_SECONDS = 6 * 60 * 60

MAX_F_AGE_SECONDS = 5 * 60

MAX_OPEN_TRADES = 3

SIGNAL_COOLDOWN_SECONDS = 60 * 60

SL_BUFFER_PCT = 0.15

CHARTS_ENABLED = True

CHART_CANDLES = 240

REQUEST_TIMEOUT = 20

USER_AGENT = (
    f"NDS-M30-M1-Scanner/{VERSION}"
)


# ============================================================
# DIAGNOSTICS
# ============================================================

DIAGNOSTICS = {
    "m30_requests": 0,
    "m30_data_ok": 0,
    "m30_empty": 0,
    "m30_short": 0,

    "pivot_highs": 0,
    "pivot_lows": 0,

    "short_hooks": 0,
    "long_hooks": 0,
    "hooks_saved": 0,

    "m1_requests": 0,
    "m1_data_ok": 0,
    "m1_empty": 0,
    "m1_short": 0,

    "m1_123f_short": 0,
    "m1_123f_long": 0,

    "signals": 0,
    "charts": 0,
    "telegram_photos": 0,

    "errors": 0,
    "assets_scanned": 0,
}


# ============================================================
# HELPERS
# ============================================================

def now_ts():
    return int(time.time())


def utc_now_string():
    return datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def safe_int(value):
    try:
        if value is None:
            return None

        return int(float(value))

    except Exception:
        return None


def safe_float(value):
    try:
        if value is None:
            return None

        value = float(value)

        if not np.isfinite(value):
            return None

        return value

    except Exception:
        return None


def normalize_timestamp(value):
    """
    Supports:
      seconds
      milliseconds
      microseconds

    Microseconds MUST be checked first.
    """

    ts = safe_int(value)

    if ts is None:
        return None

    if ts > 100_000_000_000_000:
        ts //= 1_000_000

    elif ts > 100_000_000_000:
        ts //= 1_000

    return ts


def percent_change(a, b):

    a = safe_float(a)
    b = safe_float(b)

    if a is None or b is None or a == 0:
        return None

    return (
        (b - a)
        / a
    ) * 100.0


def age_seconds(ts):

    ts = safe_int(ts)

    if ts is None:
        return None

    return max(
        0,
        now_ts() - ts
    )


def format_price(value):

    value = safe_float(value)

    if value is None:
        return "-"

    if value >= 1000:
        return f"{value:,.2f}"

    if value >= 100:
        return f"{value:.3f}"

    if value >= 1:
        return f"{value:.4f}"

    if value >= 0.01:
        return f"{value:.6f}"

    return f"{value:.8f}"


def format_duration(seconds):

    seconds = int(
        max(0, seconds)
    )

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


def row_to_dict(row):

    if row is None:
        return None

    if isinstance(row, dict):
        return row

    if isinstance(row, sqlite3.Row):

        return {
            key: row[key]
            for key in row.keys()
        }

    try:
        return dict(row)

    except Exception:
        return row


# ============================================================
# TELEGRAM
# ============================================================

def telegram_enabled():

    return bool(
        TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    )


def send_telegram(text):

    if not telegram_enabled():
        print(
            "Telegram disabled: "
            "token/chat_id missing."
        )
        return False

    try:

        url = (
            "https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}"
            "/sendMessage"
        )

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "disable_web_page_preview": True,
        }

        response = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        if not response.ok:

            print(
                "Telegram message error:",
                response.text[:500],
            )

            return False

        return True

    except Exception as e:

        DIAGNOSTICS["errors"] += 1

        print(
            "Telegram error:",
            e,
        )

        return False


def send_telegram_photo(
    photo_path,
    caption="",
):

    if not telegram_enabled():

        print(
            "Telegram photo disabled: "
            "token/chat_id missing."
        )

        return False

    if not photo_path:

        print(
            "Telegram photo skipped: "
            "empty path."
        )

        return False

    path = Path(photo_path)

    if not path.exists():

        print(
            f"Telegram photo skipped: "
            f"file not found: {path}"
        )

        return False

    try:

        url = (
            "https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}"
            "/sendPhoto"
        )

        with open(
            path,
            "rb",
        ) as photo:

            files = {
                "photo": (
                    path.name,
                    photo,
                    "image/png",
                )
            }

            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
            }

            response = requests.post(
                url,
                data=data,
                files=files,
                timeout=REQUEST_TIMEOUT,
            )

        if not response.ok:

            print(
                "Telegram photo error:",
                response.text[:500],
            )

            return False

        DIAGNOSTICS[
            "telegram_photos"
        ] += 1

        return True

    except Exception as e:

        DIAGNOSTICS["errors"] += 1

        print(
            "Telegram photo exception:",
            e,
        )

        return False


# ============================================================
# DATABASE
# ============================================================

def db_connect():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.row_factory = sqlite3.Row

    return conn


def table_columns(
    conn,
    table,
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
    definition,
):

    columns = table_columns(
        conn,
        table,
    )

    if column not in columns:

        conn.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN {column} {definition}"
        )


def init_db():

    conn = db_connect()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS scanner_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at INTEGER,
            finished_at INTEGER,
            configured INTEGER DEFAULT 0,
            assets_scanned INTEGER DEFAULT 0,
            new_signals INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS hooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            side TEXT,
            hook_time INTEGER,

            h1_time INTEGER,
            h2_time INTEGER,
            h3_time INTEGER,

            l1_time INTEGER,
            l2_time INTEGER,
            l3_time INTEGER,

            h1 REAL,
            h2 REAL,
            h3 REAL,

            l1 REAL,
            l2 REAL,
            l3 REAL,

            target REAL,

            active INTEGER DEFAULT 1,
            invalidated INTEGER DEFAULT 0,
            created_at INTEGER
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            event_key TEXT UNIQUE,

            symbol TEXT,
            side TEXT,

            hook_id INTEGER,
            hook_time INTEGER,
            f_time INTEGER,

            entry REAL,
            sl REAL,
            tp REAL,

            f_price REAL,
            hook_target REAL,

            status TEXT,

            created_at INTEGER,
            closed_at INTEGER,

            close_price REAL,
            pnl_pct REAL,

            exit_reason TEXT,
            chart_path TEXT
        )
    """)

    ensure_column(
        conn,
        "scanner_runs",
        "configured",
        "INTEGER DEFAULT 0",
    )

    ensure_column(
        conn,
        "signals",
        "chart_path",
        "TEXT",
    )

    ensure_column(
        conn,
        "signals",
        "hook_time",
        "INTEGER",
    )

    ensure_column(
        conn,
        "signals",
        "f_time",
        "INTEGER",
    )

    ensure_column(
        conn,
        "signals",
        "hook_target",
        "REAL",
    )

    ensure_column(
        conn,
        "signals",
        "created_at",
        "INTEGER",
    )

    ensure_column(
        conn,
        "hooks",
        "l3_time",
        "INTEGER",
    )

    ensure_column(
        conn,
        "hooks",
        "l3",
        "REAL",
    )

    conn.commit()

    conn.close()


# ============================================================
# ASSET DISCOVERY
# ============================================================

def discover_assets():

    try:

        url = (
            f"{KRAKEN_API_BASE}"
            "/tickers"
        )

        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": USER_AGENT
            },
        )

        response.raise_for_status()

        data = response.json()

        tickers = data.get(
            "tickers",
            []
        )

        candidates = []

        for item in tickers:

            if not isinstance(
                item,
                dict,
            ):
                continue

            symbol = (
                item.get("symbol")
                or item.get("pair")
                or item.get("instrument")
            )

            if not symbol:
                continue

            symbol = str(
                symbol
            ).upper()

            if not symbol.startswith(
                "PF_"
            ):
                continue

            if not symbol.endswith(
                "USD"
            ):
                continue

            if symbol == "PF_SNXXUSD":
                continue

            volume = (
                item.get("volume")
                or item.get("volume24h")
                or item.get("vol24h")
                or 0
            )

            volume = (
                safe_float(volume)
                or 0.0
            )

            candidates.append(
                (
                    symbol,
                    volume,
                )
            )

        candidates.sort(
            key=lambda x: x[1],
            reverse=True,
        )

        selected = [
            symbol
            for symbol, _ in
            candidates[:TARGET_ASSETS]
        ]

        available = {
            symbol
            for symbol, _ in candidates
        }

        # Ensure LDO exists in selected list.
        if (
            "PF_LDOUSD" in available
            and
            "PF_LDOUSD" not in selected
        ):

            if len(selected) >= TARGET_ASSETS:

                selected[
                    -1
                ] = "PF_LDOUSD"

            else:

                selected.append(
                    "PF_LDOUSD"
                )

        selected = list(
            dict.fromkeys(
                selected
            )
        )

        print(
            f"Discovered "
            f"{len(selected)} Kraken PF assets."
        )

        return selected

    except Exception as e:

        DIAGNOSTICS["errors"] += 1

        print(
            "Asset discovery error:",
            e,
        )

        return []


# ============================================================
# CANDLE PARSER
# ============================================================

def parse_candle_row(row):

    if isinstance(
        row,
        dict,
    ):

        timestamp = (
            row.get("time")
            if row.get("time")
            is not None
            else row.get("timestamp")
        )

        if timestamp is None:
            timestamp = row.get("t")

        open_price = (
            row.get("open")
            if row.get("open")
            is not None
            else row.get("o")
        )

        high_price = (
            row.get("high")
            if row.get("high")
            is not None
            else row.get("h")
        )

        low_price = (
            row.get("low")
            if row.get("low")
            is not None
            else row.get("l")
        )

        close_price = (
            row.get("close")
            if row.get("close")
            is not None
            else row.get("c")
        )

        volume = (
            row.get("volume")
            if row.get("volume")
            is not None
            else row.get("v")
        )

    elif isinstance(
        row,
        (list, tuple),
    ):

        if len(row) < 5:
            return None

        timestamp = row[0]

        open_price = row[1]
        high_price = row[2]
        low_price = row[3]
        close_price = row[4]

        volume = (
            row[5]
            if len(row) > 5
            else None
        )

    else:

        return None

    timestamp = normalize_timestamp(
        timestamp
    )

    open_price = safe_float(
        open_price
    )

    high_price = safe_float(
        high_price
    )

    low_price = safe_float(
        low_price
    )

    close_price = safe_float(
        close_price
    )

    volume = safe_float(
        volume
    )

    if None in (
        timestamp,
        open_price,
        high_price,
        low_price,
        close_price,
    ):
        return None

    return {
        "time": timestamp,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "volume": volume or 0.0,
    }


# ============================================================
# FETCH CANDLES
# ============================================================

def fetch_candles(
    symbol,
    interval,
    limit,
):

    is_m30 = (
        interval == M30_INTERVAL
    )

    if is_m30:

        DIAGNOSTICS[
            "m30_requests"
        ] += 1

    else:

        DIAGNOSTICS[
            "m1_requests"
        ] += 1

    try:

        url = (
            f"{KRAKEN_CHART_BASE}"
            f"/trade/{symbol}/{interval}"
        )

        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": USER_AGENT
            },
        )

        response.raise_for_status()

        data = response.json()

        raw = data.get(
            "candles"
        )

        if raw is None:

            raw = data.get(
                "data"
            )

        if raw is None:
            raw = []

        parsed = []

        for row in raw:

            candle = parse_candle_row(
                row
            )

            if candle is not None:
                parsed.append(
                    candle
                )

        if not parsed:

            if is_m30:

                DIAGNOSTICS[
                    "m30_empty"
                ] += 1

            else:

                DIAGNOSTICS[
                    "m1_empty"
                ] += 1

            return pd.DataFrame(
                columns=[
                    "time",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                ]
            )

        df = pd.DataFrame(
            parsed
        )

        df = (
            df.drop_duplicates(
                subset=["time"],
                keep="last",
            )
            .sort_values(
                "time"
            )
            .reset_index(
                drop=True
            )
        )

        df = (
            df.tail(limit)
            .reset_index(
                drop=True
            )
        )

        minimum = (
            50
            if is_m30
            else 100
        )

        if len(df) < minimum:

            if is_m30:

                DIAGNOSTICS[
                    "m30_short"
                ] += 1

            else:

                DIAGNOSTICS[
                    "m1_short"
                ] += 1

        else:

            if is_m30:

                DIAGNOSTICS[
                    "m30_data_ok"
                ] += 1

            else:

                DIAGNOSTICS[
                    "m1_data_ok"
                ] += 1

        return df

    except Exception as e:

        DIAGNOSTICS["errors"] += 1

        print(
            f"Candle fetch error "
            f"{symbol} {interval}: {e}"
        )

        return pd.DataFrame(
            columns=[
                "time",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        )


# ============================================================
# CLOSED CANDLES
# ============================================================

def get_closed_candles(
    df,
    interval_seconds,
):

    if (
        df is None
        or df.empty
    ):
        return df

    current = now_ts()

    result = df[
        (
            df["time"]
            + interval_seconds
        )
        <= current
    ].copy()

    return (
        result
        .sort_values(
            "time"
        )
        .reset_index(
            drop=True
        )
    )


# ============================================================
# PIVOTS
# ============================================================

def detect_pivots(
    df,
    left=PIVOT_LEFT,
    right=PIVOT_RIGHT,
):

    highs = []
    lows = []

    if (
        df is None
        or len(df)
        < left + right + 1
    ):
        return highs, lows

    high_values = (
        df["high"].values
    )

    low_values = (
        df["low"].values
    )

    for i in range(
        left,
        len(df) - right,
    ):

        current_high = (
            high_values[i]
        )

        current_low = (
            low_values[i]
        )

        left_highs = (
            high_values[
                i - left:i
            ]
        )

        right_highs = (
            high_values[
                i + 1:
                i + 1 + right
            ]
        )

        left_lows = (
            low_values[
                i - left:i
            ]
        )

        right_lows = (
            low_values[
                i + 1:
                i + 1 + right
            ]
        )

        if (
            current_high
            > np.max(left_highs)
            and
            current_high
            >= np.max(right_highs)
        ):

            highs.append({
                "time": int(
                    df.iloc[i]["time"]
                ),
                "price": float(
                    current_high
                ),
                "index": i,
            })

        if (
            current_low
            < np.min(left_lows)
            and
            current_low
            <= np.min(right_lows)
        ):

            lows.append({
                "time": int(
                    df.iloc[i]["time"]
                ),
                "price": float(
                    current_low
                ),
                "index": i,
            })

    return highs, lows


# ============================================================
# M30 HOOK DETECTOR
# ============================================================

def detect_m30_hook(df):

    highs, lows = detect_pivots(
        df
    )

    DIAGNOSTICS[
        "pivot_highs"
    ] += len(highs)

    DIAGNOSTICS[
        "pivot_lows"
    ] += len(lows)

    if (
        len(highs) < 3
        or len(lows) < 2
    ):
        return None

    candidates = []

    # ========================================================
    # SHORT
    #
    # H1 -> L1 -> H2 -> L2 -> H3
    #
    # H2 > H1
    # L2 < L1
    # H3 > H2
    # ========================================================

    for h1 in highs:

        for l1 in lows:

            if (
                l1["time"]
                <= h1["time"]
            ):
                continue

            for h2 in highs:

                if (
                    h2["time"]
                    <= l1["time"]
                ):
                    continue

                if (
                    h2["price"]
                    <= h1["price"]
                ):
                    continue

                for l2 in lows:

                    if (
                        l2["time"]
                        <= h2["time"]
                    ):
                        continue

                    if (
                        l2["price"]
                        >= l1["price"]
                    ):
                        continue

                    for h3 in highs:

                        if (
                            h3["time"]
                            <= l2["time"]
                        ):
                            continue

                        if (
                            h3["price"]
                            <= h2["price"]
                        ):
                            continue

                        hook_range = (
                            (
                                h3["price"]
                                - l2["price"]
                            )
                            / h3["price"]
                        ) * 100.0

                        if (
                            hook_range
                            < M30_MIN_HOOK_RANGE_PCT
                        ):
                            continue

                        candidates.append({
                            "side": "SHORT",

                            "hook_time":
                                h3["time"],

                            "h1_time":
                                h1["time"],
                            "h2_time":
                                h2["time"],
                            "h3_time":
                                h3["time"],

                            "l1_time":
                                l1["time"],
                            "l2_time":
                                l2["time"],
                            "l3_time":
                                None,

                            "h1":
                                h1["price"],
                            "h2":
                                h2["price"],
                            "h3":
                                h3["price"],

                            "l1":
                                l1["price"],
                            "l2":
                                l2["price"],
                            "l3":
                                None,

                            "target":
                                l2["price"],

                            "hook_range":
                                hook_range,
                        })

    # ========================================================
    # LONG
    #
    # L1 -> H1 -> L2 -> H2 -> L3
    #
    # L2 < L1
    # H2 > H1
    # L3 < L2
    # ========================================================

    for l1 in lows:

        for h1 in highs:

            if (
                h1["time"]
                <= l1["time"]
            ):
                continue

            for l2 in lows:

                if (
                    l2["time"]
                    <= h1["time"]
                ):
                    continue

                if (
                    l2["price"]
                    >= l1["price"]
                ):
                    continue

                for h2 in highs:

                    if (
                        h2["time"]
                        <= l2["time"]
                    ):
                        continue

                    if (
                        h2["price"]
                        <= h1["price"]
                    ):
                        continue

                    for l3 in lows:

                        if (
                            l3["time"]
                            <= h2["time"]
                        ):
                            continue

                        if (
                            l3["price"]
                            >= l2["price"]
                        ):
                            continue

                        hook_range = (
                            (
                                h2["price"]
                                - l3["price"]
                            )
                            / h2["price"]
                        ) * 100.0

                        if (
                            hook_range
                            < M30_MIN_HOOK_RANGE_PCT
                        ):
                            continue

                        candidates.append({
                            "side": "LONG",

                            "hook_time":
                                l3["time"],

                            "h1_time":
                                h1["time"],
                            "h2_time":
                                h2["time"],
                            "h3_time":
                                None,

                            "l1_time":
                                l1["time"],
                            "l2_time":
                                l2["time"],
                            "l3_time":
                                l3["time"],

                            "h1":
                                h1["price"],
                            "h2":
                                h2["price"],
                            "h3":
                                None,

                            "l1":
                                l1["price"],
                            "l2":
                                l2["price"],
                            "l3":
                                l3["price"],

                            "target":
                                h2["price"],

                            "hook_range":
                                hook_range,
                        })

    if not candidates:
        return None

    DIAGNOSTICS[
        "short_hooks"
    ] += sum(
        1
        for x in candidates
        if x["side"] == "SHORT"
    )

    DIAGNOSTICS[
        "long_hooks"
    ] += sum(
        1
        for x in candidates
        if x["side"] == "LONG"
    )

    candidates.sort(
        key=lambda x: x["hook_time"],
        reverse=True,
    )

    return candidates[0]


# ============================================================
# HOOK VALIDITY
# ============================================================

def hook_is_expired(hook):

    hook_time = safe_int(
        hook.get("hook_time")
    )

    if hook_time is None:
        return True

    return (
        now_ts() - hook_time
        > MAX_HOOK_AGE_SECONDS
    )


def hook_is_invalidated(
    symbol,
    hook,
):

    try:

        df = fetch_candles(
            symbol,
            M30_INTERVAL,
            M30_CANDLES,
        )

        closed = get_closed_candles(
            df,
            M30_SECONDS,
        )

        if len(closed) < 30:
            return False

        highs, lows = detect_pivots(
            closed
        )

        hook_time = safe_int(
            hook.get("hook_time")
        )

        if hook_time is None:
            return True

        side = hook.get(
            "side"
        )

        # Current intended aggressive
        # invalidation logic.
        if side == "SHORT":

            for pivot in lows:

                if (
                    pivot["time"]
                    > hook_time
                ):
                    return True

        elif side == "LONG":

            for pivot in highs:

                if (
                    pivot["time"]
                    > hook_time
                ):
                    return True

        return False

    except Exception as e:

        DIAGNOSTICS["errors"] += 1

        print(
            f"Hook invalidation error "
            f"{symbol}: {e}"
        )

        return False


# ============================================================
# SAVE HOOK
# ============================================================

def save_hook(
    symbol,
    hook,
):

    conn = db_connect()

    try:

        conn.execute("""
            UPDATE hooks
            SET active = 0
            WHERE symbol = ?
              AND active = 1
        """, (
            symbol,
        ))

        conn.execute("""
            INSERT INTO hooks (
                symbol,
                side,
                hook_time,

                h1_time,
                h2_time,
                h3_time,

                l1_time,
                l2_time,
                l3_time,

                h1,
                h2,
                h3,

                l1,
                l2,
                l3,

                target,
                active,
                invalidated,
                created_at
            )
            VALUES (
                ?, ?, ?,

                ?, ?, ?,
                ?, ?, ?,

                ?, ?, ?,
                ?, ?, ?,

                ?, 1, 0, ?
            )
        """, (
            symbol,
            hook.get("side"),
            hook.get("hook_time"),

            hook.get("h1_time"),
            hook.get("h2_time"),
            hook.get("h3_time"),

            hook.get("l1_time"),
            hook.get("l2_time"),
            hook.get("l3_time"),

            hook.get("h1"),
            hook.get("h2"),
            hook.get("h3"),

            hook.get("l1"),
            hook.get("l2"),
            hook.get("l3"),

            hook.get("target"),

            now_ts(),
        ))

        conn.commit()

        DIAGNOSTICS[
            "hooks_saved"
        ] += 1

        return conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

    finally:

        conn.close()


# ============================================================
# GET ACTIVE HOOKS
# ============================================================

def get_active_hooks():

    conn = db_connect()

    try:

        rows = conn.execute("""
            SELECT *
            FROM hooks
            WHERE active = 1
              AND invalidated = 0
            ORDER BY hook_time DESC
        """).fetchall()

        return [
            row_to_dict(row)
            for row in rows
        ]

    finally:

        conn.close()


def deactivate_hook(
    hook_id,
):

    conn = db_connect()

    try:

        conn.execute("""
            UPDATE hooks
            SET active = 0
            WHERE id = ?
        """, (
            hook_id,
        ))

        conn.commit()

    finally:

        conn.close()


# ============================================================
# M1 123F
# ============================================================

def detect_m1_123f(
    df,
    hook,
):

    highs, lows = detect_pivots(
        df
    )

    # 123F consists ONLY of HIGH pivots.
    if len(highs) < 4:
        return None

    hook_time = safe_int(
        hook.get("hook_time")
    )

    side = hook.get(
        "side"
    )

    if hook_time is None:
        return None

    candidates = [
        x
        for x in highs
        if x["time"] > hook_time
    ]

    if len(candidates) < 4:
        return None

    candidates.sort(
        key=lambda x: x["time"],
        reverse=True,
    )

    for f in candidates:

        f_age = age_seconds(
            f["time"]
        )

        if f_age is None:
            continue

        if (
            f_age
            > MAX_F_AGE_SECONDS
        ):
            continue

        before_f = [
            x
            for x in candidates
            if x["time"]
            < f["time"]
        ]

        if len(before_f) < 3:
            continue

        p1, p2, p3 = (
            before_f[-3:]
        )

        # ====================================================
        # SHORT
        #
        # 1 < 2 < 3 < F
        # ====================================================

        if side == "SHORT":

            if not (
                p1["price"]
                < p2["price"]
                < p3["price"]
                < f["price"]
            ):
                continue

            hook_h3 = safe_float(
                hook.get("h3")
            )

            if hook_h3 is None:
                continue

            distance = (
                abs(
                    f["price"]
                    - hook_h3
                )
                / hook_h3
            ) * 100.0

            if (
                distance
                > M1_MAX_STRUCTURE_DISTANCE_PCT
            ):
                continue

            swing = (
                (
                    f["price"]
                    - p1["price"]
                )
                / p1["price"]
            ) * 100.0

            if (
                swing
                < M1_MIN_SWING_PCT
            ):
                continue

            return {
                "side": "SHORT",

                "p1_time":
                    p1["time"],
                "p2_time":
                    p2["time"],
                "p3_time":
                    p3["time"],
                "f_time":
                    f["time"],

                "p1":
                    p1["price"],
                "p2":
                    p2["price"],
                "p3":
                    p3["price"],
                "f":
                    f["price"],

                "swing_pct":
                    swing,

                "f_age":
                    f_age,
            }

        # ====================================================
        # LONG
        #
        # 1 > 2 > 3 > F
        # ====================================================

        if side == "LONG":

            if not (
                p1["price"]
                > p2["price"]
                > p3["price"]
                > f["price"]
            ):
                continue

            hook_l3 = safe_float(
                hook.get("l3")
            )

            if hook_l3 is None:
                continue

            distance = (
                abs(
                    f["price"]
                    - hook_l3
                )
                / hook_l3
            ) * 100.0

            if (
                distance
                > M1_MAX_STRUCTURE_DISTANCE_PCT
            ):
                continue

            swing = (
                (
                    p1["price"]
                    - f["price"]
                )
                / p1["price"]
            ) * 100.0

            if (
                swing
                < M1_MIN_SWING_PCT
            ):
                continue

            return {
                "side": "LONG",

                "p1_time":
                    p1["time"],
                "p2_time":
                    p2["time"],
                "p3_time":
                    p3["time"],
                "f_time":
                    f["time"],

                "p1":
                    p1["price"],
                "p2":
                    p2["price"],
                "p3":
                    p3["price"],
                "f":
                    f["price"],

                "swing_pct":
                    swing,

                "f_age":
                    f_age,
            }

    return None


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(
    symbol,
):

    try:

        url = (
            f"{KRAKEN_API_BASE}"
            "/tickers"
        )

        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": USER_AGENT
            },
        )

        response.raise_for_status()

        data = response.json()

        tickers = data.get(
            "tickers",
            []
        )

        for item in tickers:

            if not isinstance(
                item,
                dict,
            ):
                continue

            item_symbol = (
                item.get("symbol")
                or item.get("pair")
                or item.get("instrument")
            )

            if (
                str(item_symbol).upper()
                != symbol.upper()
            ):
                continue

            for key in (
                "last",
                "lastPrice",
                "markPrice",
                "price",
            ):

                price = safe_float(
                    item.get(key)
                )

                if price is not None:
                    return price

    except Exception as e:

        DIAGNOSTICS["errors"] += 1

        print(
            f"Price error "
            f"{symbol}: {e}"
        )

    return None


# ============================================================
# TRADE LEVELS
# ============================================================

def calculate_trade_levels(
    hook,
    f_structure,
):

    side = hook.get(
        "side"
    )

    entry = safe_float(
        f_structure.get("f")
    )

    if entry is None:
        return None

    if side == "LONG":

        lows = [
            hook.get("l1"),
            hook.get("l2"),
            hook.get("l3"),
            f_structure.get("f"),
        ]

        lows = [
            safe_float(x)
            for x in lows
            if safe_float(x)
            is not None
        ]

        tp = safe_float(
            hook.get("target")
        )

        if (
            not lows
            or tp is None
        ):
            return None

        lowest = min(lows)

        sl = (
            lowest
            * (
                1.0
                - SL_BUFFER_PCT / 100.0
            )
        )

        if not (
            sl
            < entry
            < tp
        ):
            return None

        return {
            "entry": entry,
            "sl": sl,
            "tp": tp,
        }

    if side == "SHORT":

        highs = [
            hook.get("h1"),
            hook.get("h2"),
            hook.get("h3"),
            f_structure.get("f"),
        ]

        highs = [
            safe_float(x)
            for x in highs
            if safe_float(x)
            is not None
        ]

        tp = safe_float(
            hook.get("target")
        )

        if (
            not highs
            or tp is None
        ):
            return None

        highest = max(highs)

        sl = (
            highest
            * (
                1.0
                + SL_BUFFER_PCT / 100.0
            )
        )

        if not (
            tp
            < entry
            < sl
        ):
            return None

        return {
            "entry": entry,
            "sl": sl,
            "tp": tp,
        }

    return None


# ============================================================
# SIGNAL DB
# ============================================================

def signal_exists(
    event_key,
):

    conn = db_connect()

    try:

        row = conn.execute("""
            SELECT id
            FROM signals
            WHERE event_key = ?
            LIMIT 1
        """, (
            event_key,
        )).fetchone()

        return row is not None

    finally:

        conn.close()


def recent_signal_for_symbol(
    symbol,
    seconds=SIGNAL_COOLDOWN_SECONDS,
):

    conn = db_connect()

    try:

        cutoff = (
            now_ts()
            - seconds
        )

        return conn.execute("""
            SELECT *
            FROM signals
            WHERE symbol = ?
              AND created_at >= ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (
            symbol,
            cutoff,
        )).fetchone()

    finally:

        conn.close()


def count_open_trades():

    conn = db_connect()

    try:

        row = conn.execute("""
            SELECT COUNT(*)
            FROM signals
            WHERE status = 'OPEN'
        """).fetchone()

        return int(
            row[0]
        )

    finally:

        conn.close()


def insert_signal(
    event_key,
    symbol,
    side,
    hook,
    f_structure,
    levels,
    chart_path,
):

    conn = db_connect()

    try:

        cursor = conn.execute("""
            INSERT INTO signals (
                event_key,
                symbol,
                side,
                hook_id,
                hook_time,
                f_time,

                entry,
                sl,
                tp,

                f_price,
                hook_target,

                status,
                created_at,
                chart_path
            )
            VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                'OPEN', ?, ?
            )
        """, (
            event_key,
            symbol,
            side,
            hook.get("id"),
            hook.get("hook_time"),
            f_structure.get("f_time"),

            levels.get("entry"),
            levels.get("sl"),
            levels.get("tp"),

            f_structure.get("f"),
            hook.get("target"),

            now_ts(),
            chart_path,
        ))

        conn.commit()

        return cursor.lastrowid

    except sqlite3.IntegrityError:

        return None

    finally:

        conn.close()


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():

    conn = db_connect()

    try:

        rows = conn.execute("""
            SELECT *
            FROM signals
            WHERE status = 'OPEN'
            ORDER BY created_at ASC
        """).fetchall()

        return [
            row_to_dict(row)
            for row in rows
        ]

    finally:

        conn.close()


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade_id,
    close_price,
    pnl_pct,
    reason,
):

    conn = db_connect()

    try:

        conn.execute("""
            UPDATE signals
            SET
                status = 'CLOSED',
                closed_at = ?,
                close_price = ?,
                pnl_pct = ?,
                exit_reason = ?
            WHERE id = ?
        """, (
            now_ts(),
            close_price,
            pnl_pct,
            reason,
            trade_id,
        ))

        conn.commit()

    finally:

        conn.close()


# ============================================================
# MONITOR OPEN TRADES
# ============================================================

def monitor_open_trades():

    trades = get_open_trades()

    for trade in trades:

        try:

            symbol = trade["symbol"]

            side = trade["side"]

            entry = safe_float(
                trade["entry"]
            )

            sl = safe_float(
                trade["sl"]
            )

            tp = safe_float(
                trade["tp"]
            )

            if None in (
                entry,
                sl,
                tp,
            ):
                continue

            current = get_current_price(
                symbol
            )

            if current is None:
                continue

            if side == "LONG":

                pnl_pct = (
                    (
                        current
                        - entry
                    )
                    / entry
                ) * 100.0

                if current >= tp:

                    close_trade(
                        trade["id"],
                        current,
                        pnl_pct,
                        "TP",
                    )

                    send_telegram(
                        "🚨 NDS TRADE CLOSED\n\n"
                        f"{symbol} 🟢 LONG\n"
                        f"Entry: "
                        f"{format_price(entry)}\n"
                        f"Close: "
                        f"{format_price(current)}\n"
                        f"P/L: "
                        f"{pnl_pct:+.2f}%\n"
                        f"Reason: TP"
                    )

                    continue

                if current <= sl:

                    close_trade(
                        trade["id"],
                        current,
                        pnl_pct,
                        "SL",
                    )

                    send_telegram(
                        "🚨 NDS TRADE CLOSED\n\n"
                        f"{symbol} 🟢 LONG\n"
                        f"Entry: "
                        f"{format_price(entry)}\n"
                        f"Close: "
                        f"{format_price(current)}\n"
                        f"P/L: "
                        f"{pnl_pct:+.2f}%\n"
                        f"Reason: SL"
                    )

                    continue

            elif side == "SHORT":

                pnl_pct = (
                    (
                        entry
                        - current
                    )
                    / entry
                ) * 100.0

                if current <= tp:

                    close_trade(
                        trade["id"],
                        current,
                        pnl_pct,
                        "TP",
                    )

                    send_telegram(
                        "🚨 NDS TRADE CLOSED\n\n"
                        f"{symbol} 🔴 SHORT\n"
                        f"Entry: "
                        f"{format_price(entry)}\n"
                        f"Close: "
                        f"{format_price(current)}\n"
                        f"P/L: "
                        f"{pnl_pct:+.2f}%\n"
                        f"Reason: TP"
                    )

                    continue

                if current >= sl:

                    close_trade(
                        trade["id"],
                        current,
                        pnl_pct,
                        "SL",
                    )

                    send_telegram(
                        "🚨 NDS TRADE CLOSED\n\n"
                        f"{symbol} 🔴 SHORT\n"
                        f"Entry: "
                        f"{format_price(entry)}\n"
                        f"Close: "
                        f"{format_price(current)}\n"
                        f"P/L: "
                        f"{pnl_pct:+.2f}%\n"
                        f"Reason: SL"
                    )

                    continue

        except Exception as e:

            DIAGNOSTICS["errors"] += 1

            print(
                "Trade monitor error:",
                e,
            )


# ============================================================
# M30 SIGNAL CHART
# ============================================================

def draw_candle(
    ax,
    x,
    candle,
    width=0.65,
):

    o = candle["open"]
    h = candle["high"]
    l = candle["low"]
    c = candle["close"]

    ax.vlines(
        x,
        l,
        h,
        linewidth=0.8,
    )

    body_low = min(
        o,
        c,
    )

    body_height = abs(
        c - o
    )

    if body_height == 0:

        body_height = max(
            abs(h - l) * 0.002,
            1e-12,
        )

    rect = Rectangle(
        (
            x - width / 2,
            body_low,
        ),
        width,
        body_height,
        fill=False,
        linewidth=1.0,
    )

    ax.add_patch(
        rect
    )


def create_signal_chart(
    symbol,
    hook,
    f_structure,
    levels,
):

    if not CHARTS_ENABLED:
        return None

    try:

        # ----------------------------------------------------
        # Fetch M30 candles.
        # ----------------------------------------------------

        df = fetch_candles(
            symbol,
            M30_INTERVAL,
            M30_CANDLES,
        )

        df = get_closed_candles(
            df,
            M30_SECONDS,
        )

        if df is None or df.empty:
            return None

        df = (
            df.tail(
                CHART_CANDLES
            )
            .reset_index(
                drop=True
            )
        )

        if df.empty:
            return None

        fig, ax = plt.subplots(
            figsize=(18, 10)
        )

        # ----------------------------------------------------
        # Candles
        # ----------------------------------------------------

        for i, row in df.iterrows():

            draw_candle(
                ax,
                i,
                row,
            )

        time_to_x = {
            int(row["time"]): i
            for i, row
            in df.iterrows()
        }

        # ----------------------------------------------------
        # Nearest x
        # ----------------------------------------------------

        def get_x(timestamp):

            timestamp = safe_int(
                timestamp
            )

            if timestamp is None:
                return None

            if timestamp in time_to_x:

                return time_to_x[
                    timestamp
                ]

            if not time_to_x:
                return None

            nearest = min(
                time_to_x.keys(),
                key=lambda t:
                abs(t - timestamp),
            )

            return time_to_x[
                nearest
            ]

        # ----------------------------------------------------
        # Plot point
        # ----------------------------------------------------

        def plot_point(
            label,
            timestamp,
            price,
            marker="o",
            size=100,
            y_offset=10,
        ):

            price = safe_float(
                price
            )

            if (
                timestamp is None
                or price is None
            ):
                return

            x = get_x(
                timestamp
            )

            if x is None:
                return

            ax.scatter(
                [x],
                [price],
                s=size,
                marker=marker,
                zorder=10,
            )

            ax.annotate(
                (
                    f"{label}\n"
                    f"{format_price(price)}"
                ),
                (
                    x,
                    price,
                ),
                xytext=(
                    0,
                    y_offset,
                ),
                textcoords="offset points",
                ha="center",
                fontsize=9,
                fontweight="bold",
                zorder=11,
            )

        # ====================================================
        # M30 HOOK POINTS
        # ====================================================

        plot_point(
            "H1",
            hook.get("h1_time"),
            hook.get("h1"),
            marker="^",
            size=120,
            y_offset=12,
        )

        plot_point(
            "H2",
            hook.get("h2_time"),
            hook.get("h2"),
            marker="^",
            size=120,
            y_offset=12,
        )

        plot_point(
            "H3",
            hook.get("h3_time"),
            hook.get("h3"),
            marker="^",
            size=120,
            y_offset=12,
        )

        plot_point(
            "L1",
            hook.get("l1_time"),
            hook.get("l1"),
            marker="v",
            size=120,
            y_offset=-30,
        )

        plot_point(
            "L2",
            hook.get("l2_time"),
            hook.get("l2"),
            marker="v",
            size=120,
            y_offset=-30,
        )

        plot_point(
            "L3",
            hook.get("l3_time"),
            hook.get("l3"),
            marker="v",
            size=120,
            y_offset=-30,
        )

        # ====================================================
        # M30 HOOK CONNECTION
        # ====================================================

        side = hook.get(
            "side"
        )

        if side == "SHORT":

            sequence = [
                (
                    hook.get("h1_time"),
                    hook.get("h1"),
                ),
                (
                    hook.get("l1_time"),
                    hook.get("l1"),
                ),
                (
                    hook.get("h2_time"),
                    hook.get("h2"),
                ),
                (
                    hook.get("l2_time"),
                    hook.get("l2"),
                ),
                (
                    hook.get("h3_time"),
                    hook.get("h3"),
                ),
            ]

        else:

            sequence = [
                (
                    hook.get("l1_time"),
                    hook.get("l1"),
                ),
                (
                    hook.get("h1_time"),
                    hook.get("h1"),
                ),
                (
                    hook.get("l2_time"),
                    hook.get("l2"),
                ),
                (
                    hook.get("h2_time"),
                    hook.get("h2"),
                ),
                (
                    hook.get("l3_time"),
                    hook.get("l3"),
                ),
            ]

        hook_x = []
        hook_y = []

        for timestamp, price in sequence:

            price = safe_float(
                price
            )

            x = get_x(
                timestamp
            )

            if (
                x is None
                or price is None
            ):
                continue

            hook_x.append(x)
            hook_y.append(price)

        if len(hook_x) >= 2:

            ax.plot(
                hook_x,
                hook_y,
                linewidth=2.5,
                linestyle="-",
                label="M30 HOOK",
            )

        # ====================================================
        # M1 123F
        # ====================================================

        plot_point(
            "1",
            f_structure.get(
                "p1_time"
            ),
            f_structure.get(
                "p1"
            ),
            marker="D",
            size=80,
            y_offset=14,
        )

        plot_point(
            "2",
            f_structure.get(
                "p2_time"
            ),
            f_structure.get(
                "p2"
            ),
            marker="D",
            size=80,
            y_offset=14,
        )

        plot_point(
            "3",
            f_structure.get(
                "p3_time"
            ),
            f_structure.get(
                "p3"
            ),
            marker="D",
            size=80,
            y_offset=14,
        )

        plot_point(
            "F",
            f_structure.get(
                "f_time"
            ),
            f_structure.get(
                "f"
            ),
            marker="D",
            size=110,
            y_offset=18,
        )

        # ====================================================
        # 123F CONNECTION
        # ====================================================

        f_x = []
        f_y = []

        for timestamp, price in [
            (
                f_structure.get(
                    "p1_time"
                ),
                f_structure.get(
                    "p1"
                ),
            ),
            (
                f_structure.get(
                    "p2_time"
                ),
                f_structure.get(
                    "p2"
                ),
            ),
            (
                f_structure.get(
                    "p3_time"
                ),
                f_structure.get(
                    "p3"
                ),
            ),
            (
                f_structure.get(
                    "f_time"
                ),
                f_structure.get(
                    "f"
                ),
            ),
        ]:

            x = get_x(
                timestamp
            )

            price = safe_float(
                price
            )

            if (
                x is None
                or price is None
            ):
                continue

            f_x.append(x)
            f_y.append(price)

        if len(f_x) >= 2:

            ax.plot(
                f_x,
                f_y,
                linewidth=1.8,
                linestyle=":",
                label="M1 123F",
            )

        # ====================================================
        # ENTRY / SL / TP
        # ====================================================

        entry = safe_float(
            levels.get("entry")
        )

        sl = safe_float(
            levels.get("sl")
        )

        tp = safe_float(
            levels.get("tp")
        )

        if entry is not None:

            ax.axhline(
                entry,
                linestyle="--",
                linewidth=1.8,
                label=(
                    "ENTRY "
                    f"{format_price(entry)}"
                ),
            )

        if sl is not None:

            ax.axhline(
                sl,
                linestyle="--",
                linewidth=1.8,
                label=(
                    "SL "
                    f"{format_price(sl)}"
                ),
            )

        if tp is not None:

            ax.axhline(
                tp,
                linestyle="--",
                linewidth=1.8,
                label=(
                    "TP "
                    f"{format_price(tp)}"
                ),
            )

        # ====================================================
        # TITLE
        # ====================================================

        direction = (
            "LONG 🟢"
            if side == "LONG"
            else "SHORT 🔴"
        )

        hook_range = (
            safe_float(
                hook.get(
                    "hook_range"
                )
            )
            or 0.0
        )

        swing_pct = (
            safe_float(
                f_structure.get(
                    "swing_pct"
                )
            )
            or 0.0
        )

        ax.set_title(
            (
                f"NDS M30 → M1 SIGNAL | "
                f"{symbol} | {direction}\n"
                f"M30 HOOK "
                f"{hook_range:.2f}% | "
                f"M1 123F "
                f"{swing_pct:.2f}%"
            ),
            fontsize=15,
            fontweight="bold",
        )

        ax.set_xlabel(
            "M30 candles"
        )

        ax.set_ylabel(
            "Price"
        )

        ax.grid(
            True,
            alpha=0.25,
        )

        ax.legend(
            loc="best",
            fontsize=9,
        )

        plt.tight_layout()

        CHART_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        safe_symbol = (
            str(symbol)
            .replace(
                "/",
                "_",
            )
            .replace(
                ":",
                "_",
            )
        )

        filename = (
            f"signal_"
            f"{safe_symbol}_"
            f"{side}_"
            f"{hook.get('hook_time')}.png"
        )

        path = (
            CHART_DIR
            / filename
        )

        fig.savefig(
            path,
            dpi=160,
            bbox_inches="tight",
        )

        plt.close(
            fig
        )

        DIAGNOSTICS[
            "charts"
        ] += 1

        print(
            f"Chart created: {path}"
        )

        return str(path)

    except Exception as e:

        DIAGNOSTICS["errors"] += 1

        print(
            "Chart creation error:",
            e,
        )

        traceback.print_exc()

        try:
            plt.close("all")
        except Exception:
            pass

        return None


# ============================================================
# NEW SIGNAL MESSAGE
# ============================================================

def send_new_signal(
    symbol,
    hook,
    f_structure,
    levels,
):

    side = hook.get(
        "side"
    )

    direction = (
        "🟢 LONG"
        if side == "LONG"
        else "🔴 SHORT"
    )

    hook_range = (
        safe_float(
            hook.get(
                "hook_range"
            )
        )
        or 0.0
    )

    swing_pct = (
        safe_float(
            f_structure.get(
                "swing_pct"
            )
        )
        or 0.0
    )

    f_age = (
        safe_int(
            f_structure.get(
                "f_age"
            )
        )
        or 0
    )

    text = (
        "🚨 NDS NEW SIGNAL\n\n"

        f"{symbol} {direction}\n\n"

        f"Entry: "
        f"{format_price(levels['entry'])}\n"

        f"SL: "
        f"{format_price(levels['sl'])}\n"

        f"TP: "
        f"{format_price(levels['tp'])}\n\n"

        f"M30 Hook: "
        f"{hook_range:.2f}%\n"

        f"M1 123F Swing: "
        f"{swing_pct:.2f}%\n"

        f"F Age: "
        f"{f_age // 60}m\n\n"

        "PAPER ONLY"
    )

    send_telegram(
        text
    )


# ============================================================
# M30 SCAN
# ============================================================

def scan_m30(
    assets,
):

    print(
        f"[{utc_now_string()}] "
        f"M30 scan: "
        f"{len(assets)} assets"
    )

    for symbol in assets:

        try:

            df = fetch_candles(
                symbol,
                M30_INTERVAL,
                M30_CANDLES,
            )

            closed = get_closed_candles(
                df,
                M30_SECONDS,
            )

            if len(closed) < 30:
                continue

            hook = detect_m30_hook(
                closed
            )

            if hook is None:
                continue

            if hook_is_expired(
                hook
            ):
                continue

            conn = db_connect()

            try:

                existing = conn.execute("""
                    SELECT id
                    FROM hooks
                    WHERE symbol = ?
                      AND side = ?
                      AND hook_time = ?
                    LIMIT 1
                """, (
                    symbol,
                    hook.get("side"),
                    hook.get("hook_time"),
                )).fetchone()

            finally:

                conn.close()

            if existing is not None:
                continue

            hook_id = save_hook(
                symbol,
                hook,
            )

            print(
                f"NEW M30 HOOK: "
                f"{symbol} "
                f"{hook.get('side')} "
                f"id={hook_id}"
            )

        except Exception as e:

            DIAGNOSTICS["errors"] += 1

            print(
                f"M30 scan error "
                f"{symbol}: {e}"
            )


# ============================================================
# M1 SCAN
# ============================================================

def scan_m1(
    assets,
):

    hooks = get_active_hooks()

    if not hooks:
        return

    for hook in hooks:

        try:

            symbol = hook.get(
                "symbol"
            )

            hook_id = hook.get(
                "id"
            )

            if not symbol:
                continue

            if hook_is_expired(
                hook
            ):

                deactivate_hook(
                    hook_id
                )

                continue

            if hook_is_invalidated(
                symbol,
                hook,
            ):

                deactivate_hook(
                    hook_id
                )

                continue

            df = fetch_candles(
                symbol,
                M1_INTERVAL,
                M1_CANDLES,
            )

            closed = get_closed_candles(
                df,
                M1_SECONDS,
            )

            if len(closed) < 30:
                continue

            f_structure = detect_m1_123f(
                closed,
                hook,
            )

            if f_structure is None:
                continue

            if (
                count_open_trades()
                >= MAX_OPEN_TRADES
            ):
                continue

            event_key = (
                f"{symbol}|"
                f"{hook.get('side')}|"
                f"{hook.get('hook_time')}|"
                f"{f_structure.get('f_time')}"
            )

            if signal_exists(
                event_key
            ):
                continue

            recent = (
                recent_signal_for_symbol(
                    symbol
                )
            )

            if recent is not None:
                continue

            levels = (
                calculate_trade_levels(
                    hook,
                    f_structure,
                )
            )

            if levels is None:
                continue

            # Final directional validation.
            if hook.get("side") == "LONG":

                if not (
                    levels["sl"]
                    < levels["entry"]
                    < levels["tp"]
                ):
                    continue

            elif hook.get("side") == "SHORT":

                if not (
                    levels["tp"]
                    < levels["entry"]
                    < levels["sl"]
                ):
                    continue

            else:
                continue

            # =================================================
            # CREATE M30 CHART
            # =================================================

            chart_path = (
                create_signal_chart(
                    symbol,
                    hook,
                    f_structure,
                    levels,
                )
            )

            # =================================================
            # INSERT SIGNAL
            # =================================================

            signal_id = insert_signal(
                event_key,
                symbol,
                hook.get("side"),
                hook,
                f_structure,
                levels,
                chart_path,
            )

            if signal_id is None:
                continue

            DIAGNOSTICS[
                "signals"
            ] += 1

            # =================================================
            # SEND SIGNAL TEXT
            # =================================================

            send_new_signal(
                symbol,
                hook,
                f_structure,
                levels,
            )

            # =================================================
            # SEND CHART PHOTO
            # =================================================

            if chart_path:

                caption = (
                    "📊 NDS M30 CHART\n\n"
                    f"{symbol} "
                    f"{hook.get('side')}\n"
                    "M30 Hook + Pivot Points\n"
                    "M1 123F + Entry/SL/TP"
                )

                photo_sent = (
                    send_telegram_photo(
                        chart_path,
                        caption,
                    )
                )

                if photo_sent:

                    print(
                        f"Chart sent: "
                        f"{chart_path}"
                    )

                else:

                    print(
                        f"Chart created but "
                        f"Telegram send failed: "
                        f"{chart_path}"
                    )

            print(
                f"NEW SIGNAL: "
                f"{symbol} "
                f"{hook.get('side')} "
                f"Entry="
                f"{format_price(levels['entry'])}"
            )

        except Exception as e:

            DIAGNOSTICS["errors"] += 1

            print(
                f"M1 scan error "
                f"{hook.get('symbol')}: {e}"
            )

            traceback.print_exc()


# ============================================================
# OPEN TRADES REPORT
# ============================================================

def open_trades_text():

    trades = get_open_trades()

    if not trades:

        return (
            "📂 OPEN TRADES: 0"
        )

    lines = [
        f"📂 OPEN TRADES: "
        f"{len(trades)}"
    ]

    for trade in trades:

        try:

            symbol = trade[
                "symbol"
            ]

            side = trade[
                "side"
            ]

            entry = safe_float(
                trade["entry"]
            )

            sl = safe_float(
                trade["sl"]
            )

            tp = safe_float(
                trade["tp"]
            )

            created_at = safe_int(
                trade["created_at"]
            )

            if None in (
                entry,
                sl,
                tp,
            ):
                continue

            current = (
                get_current_price(
                    symbol
                )
            )

            if current is None:
                current = entry

            # =================================================
            # LONG
            # =================================================

            if side == "LONG":

                pnl_pct = (
                    (
                        current
                        - entry
                    )
                    / entry
                ) * 100.0

                tp_pct = (
                    (
                        tp
                        - current
                    )
                    / current
                ) * 100.0

                sl_pct = (
                    (
                        sl
                        - current
                    )
                    / current
                ) * 100.0

                icon = "🟢"

            # =================================================
            # SHORT
            # =================================================

            else:

                pnl_pct = (
                    (
                        entry
                        - current
                    )
                    / entry
                ) * 100.0

                tp_pct = (
                    (
                        current
                        - tp
                    )
                    / current
                ) * 100.0

                sl_pct = (
                    (
                        current
                        - sl
                    )
                    / current
                ) * 100.0

                icon = "🔴"

            duration = "-"

            if created_at:

                duration = (
                    format_duration(
                        now_ts()
                        - created_at
                    )
                )

            lines.append("")

            lines.append(
                f"{icon} "
                f"{symbol} "
                f"{side}"
            )

            lines.append(
                f"Entry: "
                f"{format_price(entry)}"
            )

            lines.append(
                f"Current: "
                f"{format_price(current)} "
                f"({pnl_pct:+.2f}%)"
            )

            lines.append(
                f"TP: "
                f"{format_price(tp)} "
                f"({tp_pct:+.2f}%)"
            )

            lines.append(
                f"SL: "
                f"{format_price(sl)} "
                f"({sl_pct:+.2f}%)"
            )

            lines.append(
                f"Duration: "
                f"{duration}"
            )

        except Exception as e:

            DIAGNOSTICS[
                "errors"
            ] += 1

            print(
                "Open trade report error:",
                e,
            )

    return "\n".join(
        lines
    )


# ============================================================
# CLOSED STATS
# ============================================================

def get_closed_stats():

    conn = db_connect()

    try:

        row = conn.execute("""
            SELECT
                COUNT(*) AS closed,

                SUM(
                    CASE
                        WHEN pnl_pct > 0
                        THEN 1
                        ELSE 0
                    END
                ) AS wins,

                SUM(
                    CASE
                        WHEN pnl_pct <= 0
                        THEN 1
                        ELSE 0
                    END
                ) AS losses,

                COALESCE(
                    SUM(pnl_pct),
                    0
                ) AS pnl

            FROM signals
            WHERE status = 'CLOSED'
        """).fetchone()

        return {
            "closed": int(
                row["closed"]
                or 0
            ),

            "wins": int(
                row["wins"]
                or 0
            ),

            "losses": int(
                row["losses"]
                or 0
            ),

            "pnl": float(
                row["pnl"]
                or 0.0
            ),
        }

    finally:

        conn.close()


# ============================================================
# DIAGNOSTICS
# ============================================================

def diagnostic_text():

    return (
        "\n"
        "🔎 DIAGNOSTIC\n"

        f"M30 requests: "
        f"{DIAGNOSTICS['m30_requests']}\n"

        f"M30 OK: "
        f"{DIAGNOSTICS['m30_data_ok']}\n"

        f"M30 empty: "
        f"{DIAGNOSTICS['m30_empty']}\n"

        f"M30 short: "
        f"{DIAGNOSTICS['m30_short']}\n"

        f"Pivot highs: "
        f"{DIAGNOSTICS['pivot_highs']}\n"

        f"Pivot lows: "
        f"{DIAGNOSTICS['pivot_lows']}\n"

        f"SHORT hooks: "
        f"{DIAGNOSTICS['short_hooks']}\n"

        f"LONG hooks: "
        f"{DIAGNOSTICS['long_hooks']}\n"

        f"Hooks saved: "
        f"{DIAGNOSTICS['hooks_saved']}\n"

        f"M1 requests: "
        f"{DIAGNOSTICS['m1_requests']}\n"

        f"M1 OK: "
        f"{DIAGNOSTICS['m1_data_ok']}\n"

        f"M1 empty: "
        f"{DIAGNOSTICS['m1_empty']}\n"

        f"M1 short: "
        f"{DIAGNOSTICS['m1_short']}\n"

        f"M1 123F SHORT: "
        f"{DIAGNOSTICS['m1_123f_short']}\n"

        f"M1 123F LONG: "
        f"{DIAGNOSTICS['m1_123f_long']}\n"

        f"Signals: "
        f"{DIAGNOSTICS['signals']}\n"

        f"Charts: "
        f"{DIAGNOSTICS['charts']}\n"

        f"Telegram photos: "
        f"{DIAGNOSTICS['telegram_photos']}\n"

        f"Errors: "
        f"{DIAGNOSTICS['errors']}"
    )


def reset_diagnostics():

    for key in DIAGNOSTICS:

        DIAGNOSTICS[key] = 0


# ============================================================
# REPORT
# ============================================================

def send_report(
    assets,
    new_signals=0,
):

    open_text = (
        open_trades_text()
    )

    stats = (
        get_closed_stats()
    )

    report = (
        "📊 NDS M30 → M1 REPORT\n\n"

        f"Version: {VERSION}\n"
        f"Time: {utc_now_string()}\n\n"

        f"Assets scanned: "
        f"{len(assets)}\n"

        f"New signals: "
        f"{new_signals}\n\n"

        f"{open_text}\n\n"

        "📈 PERFORMANCE\n"

        f"Closed: "
        f"{stats['closed']}\n"

        f"Wins: "
        f"{stats['wins']}\n"

        f"Losses: "
        f"{stats['losses']}\n"

        f"PnL: "
        f"{stats['pnl']:+.2f}%\n"

        f"{diagnostic_text()}\n\n"

        "PAPER ONLY"
    )

    send_telegram(
        report
    )

    print(
        report
    )

    reset_diagnostics()


# ============================================================
# RUN RECORD
# ============================================================

def record_run_start():

    conn = db_connect()

    try:

        cursor = conn.execute("""
            INSERT INTO scanner_runs (
                started_at,
                configured
            )
            VALUES (?, ?)
        """, (
            now_ts(),
            1,
        ))

        conn.commit()

        return cursor.lastrowid

    finally:

        conn.close()


def record_run_finish(
    run_id,
    assets_scanned,
    new_signals,
    errors,
):

    conn = db_connect()

    try:

        conn.execute("""
            UPDATE scanner_runs
            SET
                finished_at = ?,
                assets_scanned = ?,
                new_signals = ?,
                errors = ?
            WHERE id = ?
        """, (
            now_ts(),
            assets_scanned,
            new_signals,
            errors,
            run_id,
        ))

        conn.commit()

    finally:

        conn.close()


# ============================================================
# STARTUP
# ============================================================

def send_startup():

    text = (
        "🚀 NDS M30 → M1 SCANNER\n\n"

        f"Version: {VERSION}\n"
        "Mode: PAPER ONLY\n\n"

        "M30 SHORT HOOK:\n"
        "H1 → L1 → H2 → L2 → H3\n"
        "H2 > H1\n"
        "L2 < L1\n"
        "H3 > H2\n\n"

        "M30 LONG HOOK:\n"
        "L1 → H1 → L2 → H2 → L3\n"
        "L2 < L1\n"
        "H2 > H1\n"
        "L3 < L2\n\n"

        "M1 SHORT 123F:\n"
        "1 < 2 < 3 < F\n\n"

        "M1 LONG 123F:\n"
        "1 > 2 > 3 > F\n\n"

        f"Max open trades: "
        f"{MAX_OPEN_TRADES}\n"

        f"Hook max age: "
        f"{MAX_HOOK_AGE_SECONDS // 3600}h\n"

        f"F max age: "
        f"{MAX_F_AGE_SECONDS // 60}m\n\n"

        "M30 charts: ON\n"
        "Telegram chart upload: ON\n"

        "PAPER ONLY"
    )

    send_telegram(
        text
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not PAPER_ONLY:

        raise RuntimeError(
            "SAFETY STOP: "
            "PAPER_ONLY must remain True."
        )

    init_db()

    CHART_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    run_id = (
        record_run_start()
    )

    print(
        "\n"
        "==================================================\n"
        f"NDS M30 -> M1 SCANNER {VERSION}\n"
        "PAPER ONLY\n"
        "==================================================\n"
    )

    send_startup()

    assets = discover_assets()

    DIAGNOSTICS[
        "assets_scanned"
    ] = len(assets)

    if not assets:

        record_run_finish(
            run_id,
            0,
            0,
            DIAGNOSTICS["errors"],
        )

        print(
            "No assets discovered."
        )

        return

    # ========================================================
    # INITIAL SCAN
    # ========================================================

    scan_m30(
        assets
    )

    scan_m1(
        assets
    )

    monitor_open_trades()

    initial_signals = (
        DIAGNOSTICS["signals"]
    )

    send_report(
        assets,
        initial_signals,
    )

    # ========================================================
    # LOOP
    # ========================================================

    started = time.time()

    last_m30 = time.time()
    last_m1 = time.time()
    last_report = time.time()
    last_asset_refresh = time.time()

    total_new_signals = 0

    while (
        time.time()
        - started
        < RUN_DURATION_SECONDS
    ):

        try:

            current_time = time.time()

            # ------------------------------------------------
            # Refresh assets hourly.
            # ------------------------------------------------

            if (
                current_time
                - last_asset_refresh
                >= 3600
            ):

                new_assets = (
                    discover_assets()
                )

                if new_assets:
                    assets = new_assets

                last_asset_refresh = (
                    current_time
                )

            # ------------------------------------------------
            # M30 every 5 minutes.
            # ------------------------------------------------

            if (
                current_time
                - last_m30
                >= M30_SCAN_SECONDS
            ):

                before = (
                    DIAGNOSTICS[
                        "signals"
                    ]
                )

                scan_m30(
                    assets
                )

                last_m30 = (
                    current_time
                )

                after = (
                    DIAGNOSTICS[
                        "signals"
                    ]
                )

                total_new_signals += (
                    after - before
                )

            # ------------------------------------------------
            # M1 every minute.
            # ------------------------------------------------

            if (
                current_time
                - last_m1
                >= M1_SCAN_SECONDS
            ):

                before = (
                    DIAGNOSTICS[
                        "signals"
                    ]
                )

                scan_m1(
                    assets
                )

                last_m1 = (
                    current_time
                )

                after = (
                    DIAGNOSTICS[
                        "signals"
                    ]
                )

                total_new_signals += (
                    after - before
                )

            # ------------------------------------------------
            # Monitor open trades.
            # ------------------------------------------------

            monitor_open_trades()

            # ------------------------------------------------
            # Report every 15 minutes.
            # ------------------------------------------------

            if (
                current_time
                - last_report
                >= REPORT_SECONDS
            ):

                send_report(
                    assets,
                    total_new_signals,
                )

                total_new_signals = 0

                last_report = (
                    current_time
                )

            time.sleep(5)

        except KeyboardInterrupt:

            print(
                "Scanner interrupted."
            )

            break

        except Exception as e:

            DIAGNOSTICS[
                "errors"
            ] += 1

            print(
                "Main loop error:",
                e,
            )

            traceback.print_exc()

            time.sleep(5)

    # ========================================================
    # FINAL
    # ========================================================

    monitor_open_trades()

    send_report(
        assets,
        total_new_signals,
    )

    record_run_finish(
        run_id,
        len(assets),
        DIAGNOSTICS["signals"],
        DIAGNOSTICS["errors"],
    )

    print(
        "\n"
        "==================================================\n"
        "NDS SCANNER FINISHED\n"
        "==================================================\n"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
