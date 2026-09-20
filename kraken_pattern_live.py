# ============================================================
# KRAKEN FUTURES PATTERN LIVE SIGNAL SCANNER
# VERSION 6.2.1
# ============================================================
#
# PAPER ONLY
#
# STRATEGY:
# 1H Pattern
#    ↓
# 1H Closed-Candle Breakout
#    ↓
# 15M Same Pattern
#    ↓
# 15M Closed-Candle Breakout
#    ↓
# 15M Retest
#    ↓
# 15M Confirmation
#    ↓
# ENTRY
#
# TP / SL:
# Natural 15M Pattern Target / Invalidation
#
# IMPORTANT:
# - Existing DB is preserved
# - No lookahead
# - Closed candles only
# - First retest only
# - First valid confirmation
# - No fixed 1% TP
# - No fixed 1% SL
# - No RR filter
# - REAL_TRADING is always False
# ============================================================

import os
import sqlite3
import time
import html
import traceback
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "6.2.1"

REAL_TRADING = False

DB_FILE = "kraken_pattern_live_v52.db"

MAX_HOLD_HOURS = 48

PATTERN_LOOKBACK_1H = 240
PATTERN_LOOKBACK_15M = 240

RECENT_PATTERN_HOURS_1H = 72
RECENT_PATTERN_HOURS_15M = 36

BREAKOUT_LOOKAHEAD_1H = 24
BREAKOUT_LOOKAHEAD_15M = 32

RETEST_LOOKAHEAD_15M = 16
CONFIRMATION_LOOKAHEAD_15M = 4

PERIODIC_REPORT_SECONDS = 900

MAX_SIGNALS_PER_SCAN = 1

DOUBLE_LEVEL_TOLERANCE = 0.0075
MIN_PATTERN_DEPTH_PCT = 0.004
MIN_PATTERN_SEPARATION_BARS = 2

HS_SHOULDER_TOLERANCE = 0.015
HS_MIN_HEAD_ADVANTAGE_PCT = 0.003
MIN_HS_DEPTH_PCT = 0.004

INVALIDATION_BUFFER = 0.001

CHART_CANDLES = 80


# ============================================================
# TIME
# ============================================================

TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))


def now_ts():
    return int(time.time())


def format_time(value):
    if value is None:
        return "-"

    try:
        value = float(value)

        if value > 10_000_000_000:
            value /= 1000

        dt = datetime.fromtimestamp(value, tz=timezone.utc)
        return dt.astimezone(TEHRAN_TZ).strftime("%Y-%m-%d %H:%M")

    except Exception:
        return "-"


def duration_text(start_ts, end_ts=None):
    if not start_ts:
        return "-"

    if end_ts is None:
        end_ts = now_ts()

    seconds = max(0, int(end_ts - start_ts))

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60

    if hours > 0:
        return f"{hours}h {minutes}m"

    return f"{minutes}m"


# ============================================================
# ASSETS
# ============================================================

ASSETS = [
    "XBT", "ETH", "SOL", "XRP", "LTC",
    "DOGE", "ADA", "LINK", "AVAX", "DOT",
    "BNB", "TRX", "UNI", "AAVE", "SUI",
    "NEAR", "ATOM", "FIL", "ARB", "OP",
    "BCH", "ETC", "XMR", "XLM", "ALGO",
    "ICP", "INJ", "TIA", "SEI", "RUNE",
    "CRV", "HBAR", "HYPE", "ENA", "FET",
    "KAS", "STX", "JUP", "PEPE", "WIF"
]


CONTRACTS = {
    asset: f"PF_{asset}USD"
    for asset in ASSETS
}


# ============================================================
# KRAKEN
# ============================================================

KRAKEN_BASE = "https://futures.kraken.com"

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 Kraken Pattern Scanner"
})


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def telegram_send(message, image_path=None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials are not configured.")
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
                        "caption": message,
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
                    "text": message,
                    "parse_mode": "HTML",
                },
                timeout=30,
            )

        response.raise_for_status()

        return True

    except Exception as exc:

        print(
            f"Telegram error: {exc}"
        )

        return False


# ============================================================
# STATS
# ============================================================

STATS = {
    "scans": 0,
    "assets_scanned": 0,

    "patterns_1h": 0,
    "breakouts_1h": 0,

    "patterns_15m": 0,
    "breakouts_15m": 0,

    "retests_15m": 0,
    "confirmations_15m": 0,

    "candidates": 0,
    "unique_entries": 0,

    "signals_sent": 0,
    "closed_trades": 0,

    "errors": 0,
}


# ============================================================
# BEST CANDIDATE
# ============================================================

STAGE_RANK = {
    "None": 0,
    "1H Pattern": 1,
    "1H Breakout": 2,
    "15M Pattern": 3,
    "15M Breakout": 4,
    "15M Retest": 5,
    "15M Confirmation": 6,
    "Direction Check": 7,
    "DB Check": 8,
    "Signal Ready": 9,
}


BEST_CANDIDATE = None


def reset_best_candidate():

    global BEST_CANDIDATE

    BEST_CANDIDATE = None


def consider_candidate(
    asset,
    direction,
    pattern,
    score,
    stage_reached,
    rejected_at,
    reason,
):

    global BEST_CANDIDATE

    # --------------------------------------------------------
    # IMPORTANT:
    # Never create a fake candidate with score 0.
    # --------------------------------------------------------

    if not asset or not pattern:
        return

    try:
        score = float(score or 0)
    except Exception:
        score = 0.0

    rank = STAGE_RANK.get(stage_reached, 0)

    candidate = {
        "asset": asset,
        "direction": direction,
        "pattern": pattern,
        "score": score,
        "stage_reached": stage_reached,
        "rejected_at": rejected_at,
        "reason": reason,
        "rank": rank,
    }

    if BEST_CANDIDATE is None:
        BEST_CANDIDATE = candidate
        return

    current_rank = BEST_CANDIDATE.get("rank", 0)
    current_score = BEST_CANDIDATE.get("score", 0)

    if rank > current_rank:

        BEST_CANDIDATE = candidate

    elif rank == current_rank and score > current_score:

        BEST_CANDIDATE = candidate


# ============================================================
# DATABASE
# ============================================================

def get_db():

    conn = sqlite3.connect(DB_FILE)

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = get_db()

    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
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
    """)

    # --------------------------------------------------------
    # Existing DB migration
    # --------------------------------------------------------

    columns = {
        row["name"]
        for row in cur.execute(
            "PRAGMA table_info(trades)"
        ).fetchall()
    }

    migrations = {
        "exit_reason": "ALTER TABLE trades ADD COLUMN exit_reason TEXT",
        "breakout_time": "ALTER TABLE trades ADD COLUMN breakout_time INTEGER",
        "retest_time": "ALTER TABLE trades ADD COLUMN retest_time INTEGER",
        "confirmation_time": "ALTER TABLE trades ADD COLUMN confirmation_time INTEGER",
    }

    for column, sql in migrations.items():

        if column not in columns:

            try:
                cur.execute(sql)
            except Exception:
                pass

    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_signal_key
        ON trades(signal_key)
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS scanner_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.commit()
    conn.close()


def get_meta(key):

    conn = get_db()

    row = conn.execute(
        "SELECT value FROM scanner_meta WHERE key = ?",
        (key,)
    ).fetchone()

    conn.close()

    if not row:
        return None

    return row["value"]


def set_meta(key, value):

    conn = get_db()

    conn.execute("""
        INSERT INTO scanner_meta(key, value)
        VALUES (?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value = excluded.value
    """, (key, str(value)))

    conn.commit()
    conn.close()


# ============================================================
# DATA
# ============================================================

def get_candles(symbol, resolution, limit):

    endpoint = (
        "/derivatives/api/v3/klines"
    )

    try:

        response = SESSION.get(
            KRAKEN_BASE + endpoint,
            params={
                "symbol": symbol,
                "interval": resolution,
            },
            timeout=20,
        )

        response.raise_for_status()

        data = response.json()

        rows = data.get("candles", [])

        if not rows:
            rows = data.get("result", [])

        if not rows:
            return pd.DataFrame()

        parsed = []

        for row in rows[-limit:]:

            if isinstance(row, dict):

                ts = (
                    row.get("time")
                    or row.get("timestamp")
                )

                op = row.get("open")
                hi = row.get("high")
                lo = row.get("low")
                cl = row.get("close")
                vol = row.get("volume", 0)

            else:

                if len(row) < 5:
                    continue

                ts = row[0]
                op = row[1]
                hi = row[2]
                lo = row[3]
                cl = row[4]

                vol = row[5] if len(row) > 5 else 0

            try:

                ts = float(ts)

                if ts > 10_000_000_000:
                    ts /= 1000

                parsed.append({
                    "timestamp": int(ts),
                    "open": float(op),
                    "high": float(hi),
                    "low": float(lo),
                    "close": float(cl),
                    "volume": float(vol or 0),
                })

            except Exception:
                continue

        df = pd.DataFrame(parsed)

        if df.empty:
            return df

        df = (
            df
            .drop_duplicates("timestamp")
            .sort_values("timestamp")
            .reset_index(drop=True)
        )

        # ----------------------------------------------------
        # Closed candles only
        # ----------------------------------------------------

        current = now_ts()

        if resolution in ("60", "1h", "60m"):

            candle_seconds = 3600

        elif resolution in ("15", "15m"):

            candle_seconds = 900

        else:

            candle_seconds = 60

        df = df[
            df["timestamp"] + candle_seconds <= current
        ].copy()

        return df.tail(limit).reset_index(drop=True)

    except Exception as exc:

        STATS["errors"] += 1

        print(
            f"[DATA ERROR] {symbol} {resolution}: {exc}"
        )

        return pd.DataFrame()


# ============================================================
# HELPERS
# ============================================================

def candle_body_bullish(row):

    return row["close"] > row["open"]


def candle_body_bearish(row):

    return row["close"] < row["open"]


def pct_distance(a, b):

    if not b:
        return 0

    return abs(a - b) / abs(b)


# ============================================================
# PATTERN DETECTION
# ============================================================

def detect_double_top(df):

    if len(df) < 20:
        return []

    results = []

    highs = df["high"].values
    lows = df["low"].values

    for i in range(5, len(df) - 5):

        left = highs[i - 2]
        right = highs[i + 2]

        if left <= 0 or right <= 0:
            continue

        if pct_distance(left, right) > DOUBLE_LEVEL_TOLERANCE:
            continue

        valley = min(
            lows[i - 1],
            lows[i],
            lows[i + 1]
        )

        peak = max(left, right)

        depth = (peak - valley) / peak

        if depth < MIN_PATTERN_DEPTH_PCT:
            continue

        neckline = valley

        height = peak - neckline

        if height <= 0:
            continue

        pattern_time = int(
            df.iloc[i + 2]["timestamp"]
        )

        quality = (
            depth * 100
            + (1 - pct_distance(left, right)) * 5
        )

        results.append({
            "pattern": "Double Top",
            "direction": "SHORT",
            "pattern_time": pattern_time,
            "left": i - 2,
            "valley": i,
            "right": i + 2,
            "neckline": float(neckline),
            "target": float(neckline - height),
            "stop": float(peak * (1 + INVALIDATION_BUFFER)),
            "height": float(height),
            "quality": float(quality),
        })

    return results


def detect_double_bottom(df):

    if len(df) < 20:
        return []

    results = []

    highs = df["high"].values
    lows = df["low"].values

    for i in range(5, len(df) - 5):

        left = lows[i - 2]
        right = lows[i + 2]

        if left <= 0 or right <= 0:
            continue

        if pct_distance(left, right) > DOUBLE_LEVEL_TOLERANCE:
            continue

        peak = max(
            highs[i - 1],
            highs[i],
            highs[i + 1]
        )

        bottom = min(left, right)

        depth = (peak - bottom) / bottom

        if depth < MIN_PATTERN_DEPTH_PCT:
            continue

        neckline = peak

        height = neckline - bottom

        if height <= 0:
            continue

        pattern_time = int(
            df.iloc[i + 2]["timestamp"]
        )

        quality = (
            depth * 100
            + (1 - pct_distance(left, right)) * 5
        )

        results.append({
            "pattern": "Double Bottom",
            "direction": "LONG",
            "pattern_time": pattern_time,
            "left": i - 2,
            "valley": i,
            "right": i + 2,
            "neckline": float(neckline),
            "target": float(neckline + height),
            "stop": float(bottom * (1 - INVALIDATION_BUFFER)),
            "height": float(height),
            "quality": float(quality),
        })

    return results


def detect_head_shoulders(df):

    if len(df) < 30:
        return []

    results = []

    highs = df["high"].values
    lows = df["low"].values

    for i in range(8, len(df) - 8):

        left_idx = i - 5
        head_idx = i
        right_idx = i + 5

        left = highs[left_idx]
        head = highs[head_idx]
        right = highs[right_idx]

        if head <= left or head <= right:
            continue

        shoulder_avg = (left + right) / 2

        if pct_distance(left, right) > HS_SHOULDER_TOLERANCE:
            continue

        if (head - shoulder_avg) / shoulder_avg < HS_MIN_HEAD_ADVANTAGE_PCT:
            continue

        neckline_left = lows[i - 3]
        neckline_right = lows[i + 3]

        neckline = (
            neckline_left + neckline_right
        ) / 2

        depth = (
            head - neckline
        ) / head

        if depth < MIN_HS_DEPTH_PCT:
            continue

        height = head - neckline

        if height <= 0:
            continue

        pattern_time = int(
            df.iloc[right_idx]["timestamp"]
        )

        quality = (
            depth * 100
            + (1 - pct_distance(left, right)) * 5
        )

        results.append({
            "pattern": "Head & Shoulders",
            "direction": "SHORT",
            "pattern_time": pattern_time,
            "left_shoulder": left_idx,
            "head": head_idx,
            "right_shoulder": right_idx,
            "neckline_left": i - 3,
            "neckline_right": i + 3,
            "neckline": float(neckline),
            "target": float(neckline - height),
            "stop": float(head * (1 + INVALIDATION_BUFFER)),
            "height": float(height),
            "quality": float(quality),
        })

    return results


def detect_inverse_head_shoulders(df):

    if len(df) < 30:
        return []

    results = []

    highs = df["high"].values
    lows = df["low"].values

    for i in range(8, len(df) - 8):

        left_idx = i - 5
        head_idx = i
        right_idx = i + 5

        left = lows[left_idx]
        head = lows[head_idx]
        right = lows[right_idx]

        if head >= left or head >= right:
            continue

        shoulder_avg = (left + right) / 2

        if pct_distance(left, right) > HS_SHOULDER_TOLERANCE:
            continue

        if (shoulder_avg - head) / abs(shoulder_avg) < HS_MIN_HEAD_ADVANTAGE_PCT:
            continue

        neckline_left = highs[i - 3]
        neckline_right = highs[i + 3]

        neckline = (
            neckline_left + neckline_right
        ) / 2

        depth = (
            neckline - head
        ) / abs(head)

        if depth < MIN_HS_DEPTH_PCT:
            continue

        height = neckline - head

        if height <= 0:
            continue

        pattern_time = int(
            df.iloc[right_idx]["timestamp"]
        )

        quality = (
            depth * 100
            + (1 - pct_distance(left, right)) * 5
        )

        results.append({
            "pattern": "Inverse Head & Shoulders",
            "direction": "LONG",
            "pattern_time": pattern_time,
            "left_shoulder": left_idx,
            "head": head_idx,
            "right_shoulder": right_idx,
            "neckline_left": i - 3,
            "neckline_right": i + 3,
            "neckline": float(neckline),
            "target": float(neckline + height),
            "stop": float(head * (1 - INVALIDATION_BUFFER)),
            "height": float(height),
            "quality": float(quality),
        })

    return results


def detect_patterns(df):

    if df is None or df.empty:
        return []

    patterns = []

    patterns.extend(
        detect_double_top(df)
    )

    patterns.extend(
        detect_double_bottom(df)
    )

    patterns.extend(
        detect_head_shoulders(df)
    )

    patterns.extend(
        detect_inverse_head_shoulders(df)
    )

    patterns.sort(
        key=lambda x: (
            x["pattern_time"],
            x["quality"]
        ),
        reverse=True
    )

    return patterns


# ============================================================
# RECENT PATTERNS
# ============================================================

def recent_patterns(patterns, hours):

    cutoff = now_ts() - int(hours * 3600)

    return [
        p for p in patterns
        if p["pattern_time"] >= cutoff
    ]


# ============================================================
# 1H BREAKOUT
# ============================================================

def find_1h_breakout(df, pattern):

    start_time = pattern["pattern_time"]

    end_time = (
        start_time
        + BREAKOUT_LOOKAHEAD_1H * 3600
    )

    future = df[
        (df["timestamp"] > start_time)
        & (df["timestamp"] <= end_time)
    ]

    for idx, row in future.iterrows():

        if pattern["direction"] == "LONG":

            if row["close"] > pattern["neckline"]:

                return {
                    "index": idx,
                    "timestamp": int(row["timestamp"]),
                }

        else:

            if row["close"] < pattern["neckline"]:

                return {
                    "index": idx,
                    "timestamp": int(row["timestamp"]),
                }

    return None


# ============================================================
# 15M SAME PATTERN
# ============================================================

def find_15m_same_pattern(
    df15,
    pattern_1h,
    breakout_1h
):

    patterns = detect_patterns(df15)

    cutoff = (
        breakout_1h["timestamp"]
        + RECENT_PATTERN_HOURS_15M * 3600
    )

    candidates = []

    for p in patterns:

        if p["pattern_time"] <= breakout_1h["timestamp"]:
            continue

        if p["pattern_time"] > cutoff:
            continue

        if p["pattern"] != pattern_1h["pattern"]:
            continue

        if p["direction"] != pattern_1h["direction"]:
            continue

        candidates.append(p)

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (
            x["quality"],
            x["pattern_time"]
        ),
        reverse=True
    )

    return candidates[0]


# ============================================================
# 15M BREAKOUT
# ============================================================

def find_15m_breakout(df15, pattern15):

    start_time = pattern15["pattern_time"]

    end_time = (
        start_time
        + BREAKOUT_LOOKAHEAD_15M * 900
    )

    future = df15[
        (df15["timestamp"] > start_time)
        & (df15["timestamp"] <= end_time)
    ]

    for idx, row in future.iterrows():

        if pattern15["direction"] == "LONG":

            if row["close"] > pattern15["neckline"]:

                return {
                    "index": idx,
                    "timestamp": int(row["timestamp"]),
                }

        else:

            if row["direction"] if False else False:
                pass

            if row["close"] < pattern15["neckline"]:

                return {
                    "index": idx,
                    "timestamp": int(row["timestamp"]),
                }

    return None


# ============================================================
# 15M RETEST
# ============================================================

def find_15m_retest(
    df15,
    pattern15,
    breakout15
):

    start_time = breakout15["timestamp"]

    end_time = (
        start_time
        + RETEST_LOOKAHEAD_15M * 900
    )

    future = df15[
        (df15["timestamp"] > start_time)
        & (df15["timestamp"] <= end_time)
    ]

    for idx, row in future.iterrows():

        neckline = pattern15["neckline"]

        touched = (
            row["low"] <= neckline <= row["high"]
        )

        if not touched:
            continue

        if pattern15["direction"] == "LONG":

            if row["close"] >= neckline:

                return {
                    "index": idx,
                    "timestamp": int(row["timestamp"]),
                }

        else:

            if row["close"] <= neckline:

                return {
                    "index": idx,
                    "timestamp": int(row["timestamp"]),
                }

    return None


# ============================================================
# 15M CONFIRMATION
# ============================================================

def find_15m_confirmation(
    df15,
    pattern15,
    retest15
):

    retest_idx = retest15["index"]

    future = df15.iloc[
        retest_idx + 1:
        retest_idx + 1 + CONFIRMATION_LOOKAHEAD_15M
    ]

    for idx, row in future.iterrows():

        neckline = pattern15["neckline"]

        if pattern15["direction"] == "LONG":

            valid = (
                row["close"] > neckline
                and candle_body_bullish(row)
            )

        else:

            valid = (
                row["close"] < neckline
                and candle_body_bearish(row)
            )

        if valid:

            return {
                "index": idx,
                "timestamp": int(row["timestamp"]),
                "entry_price": float(row["close"]),
            }

    return None


# ============================================================
# DIRECTION CHECK
# ============================================================

def direction_sanity(
    direction,
    entry,
    tp,
    sl
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
# SIGNAL KEY
# ============================================================

def make_signal_key(
    asset,
    direction,
    pattern15,
    confirmation
):

    return (
        f"{asset}|"
        f"{direction}|"
        f"{pattern15['pattern']}|"
        f"{pattern15['pattern_time']}|"
        f"{confirmation['timestamp']}"
    )


def signal_exists(signal_key):

    conn = get_db()

    row = conn.execute("""
        SELECT 1
        FROM trades
        WHERE signal_key = ?
        LIMIT 1
    """, (signal_key,)).fetchone()

    conn.close()

    return row is not None


def open_trade_exists(asset, direction):

    conn = get_db()

    row = conn.execute("""
        SELECT 1
        FROM trades
        WHERE asset = ?
          AND direction = ?
          AND status = 'OPEN'
        LIMIT 1
    """, (
        asset,
        direction
    )).fetchone()

    conn.close()

    return row is not None


# ============================================================
# INSERT TRADE
# ============================================================

def insert_trade(candidate):

    conn = get_db()

    try:

        cur = conn.execute("""
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
        """, (
            candidate["symbol"],
            candidate["asset"],
            candidate["direction"],
            candidate["pattern"],
            candidate["signal_key"],
            candidate["pattern_time"],
            candidate["breakout_time"],
            candidate["retest_time"],
            candidate["confirmation_time"],
            candidate["entry_time"],
            candidate["entry_price"],
            candidate["sl_price"],
            candidate["tp_price"],
            candidate["entry_price"],
            "OPEN",
            now_ts(),
            now_ts(),
        ))

        conn.commit()

        inserted = cur.rowcount == 1

        return inserted

    finally:

        conn.close()


# ============================================================
# ANALYZE ONE ASSET
# ============================================================

def analyze_asset(asset):

    symbol = CONTRACTS[asset]

    df1h = get_candles(
        symbol,
        "60",
        PATTERN_LOOKBACK_1H
    )

    if len(df1h) < 40:

        print(
            f"[{asset}] Not enough closed 1H candles."
        )

        return None

    df15 = get_candles(
        symbol,
        "15",
        PATTERN_LOOKBACK_15M
    )

    if len(df15) < 40:

        print(
            f"[{asset}] Not enough closed 15M candles."
        )

        return None

    STATS["assets_scanned"] += 1

    patterns1h_all = detect_patterns(df1h)

    patterns1h = recent_patterns(
        patterns1h_all,
        RECENT_PATTERN_HOURS_1H
    )

    if not patterns1h:
        return None

    STATS["patterns_1h"] += len(patterns1h)

    valid_candidates = []

    for pattern1h in patterns1h:

        score1h = float(
            pattern1h.get("quality", 0)
        )

        breakout1h = find_1h_breakout(
            df1h,
            pattern1h
        )

        if not breakout1h:

            consider_candidate(
                asset,
                pattern1h["direction"],
                pattern1h["pattern"],
                score1h,
                "1H Pattern",
                "1H Breakout",
                "No closed candle broke the 1H neckline within the breakout window.",
            )

            continue

        STATS["breakouts_1h"] += 1

        consider_candidate(
            asset,
            pattern1h["direction"],
            pattern1h["pattern"],
            score1h,
            "1H Breakout",
            "15M Pattern",
            "1H breakout occurred, but the next required stage is a matching 15M pattern.",
        )

        pattern15 = find_15m_same_pattern(
            df15,
            pattern1h,
            breakout1h
        )

        if not pattern15:

            consider_candidate(
                asset,
                pattern1h["direction"],
                pattern1h["pattern"],
                score1h,
                "1H Breakout",
                "15M Pattern",
                "No matching 15M pattern formed after the 1H breakout within the allowed window.",
            )

            continue

        STATS["patterns_15m"] += 1

        score = (
            score1h
            + float(pattern15.get("quality", 0))
        )

        consider_candidate(
            asset,
            pattern15["direction"],
            pattern15["pattern"],
            score,
            "15M Pattern",
            "15M Breakout",
            "No closed 15M candle broke the 15M neckline within the breakout window.",
        )

        breakout15 = find_15m_breakout(
            df15,
            pattern15
        )

        if not breakout15:
            continue

        STATS["breakouts_15m"] += 1

        consider_candidate(
            asset,
            pattern15["direction"],
            pattern15["pattern"],
            score,
            "15M Breakout",
            "15M Retest",
            "Price did not retest the 15M neckline within the retest window.",
        )

        retest15 = find_15m_retest(
            df15,
            pattern15,
            breakout15
        )

        if not retest15:
            continue

        STATS["retests_15m"] += 1

        consider_candidate(
            asset,
            pattern15["direction"],
            pattern15["pattern"],
            score,
            "15M Retest",
            "15M Confirmation",
            "No valid confirmation candle appeared within the confirmation window.",
        )

        confirmation = find_15m_confirmation(
            df15,
            pattern15,
            retest15
        )

        if not confirmation:
            continue

        STATS["confirmations_15m"] += 1

        entry = confirmation["entry_price"]

        tp = float(pattern15["target"])
        sl = float(pattern15["stop"])

        if not direction_sanity(
            pattern15["direction"],
            entry,
            tp,
            sl
        ):

            consider_candidate(
                asset,
                pattern15["direction"],
                pattern15["pattern"],
                score,
                "15M Confirmation",
                "Direction Check",
                "Natural 15M TP/SL is not valid relative to the confirmation entry.",
            )

            continue

        STATS["candidates"] += 1

        signal_key = make_signal_key(
            asset,
            pattern15["direction"],
            pattern15,
            confirmation
        )

        if signal_exists(signal_key):

            consider_candidate(
                asset,
                pattern15["direction"],
                pattern15["pattern"],
                score,
                "15M Confirmation",
                "DB Check",
                "This exact signal already exists in the database.",
            )

            continue

        if open_trade_exists(
            asset,
            pattern15["direction"]
        ):

            consider_candidate(
                asset,
                pattern15["direction"],
                pattern15["pattern"],
                score,
                "15M Confirmation",
                "DB Check",
                "A same-direction trade is already open for this asset.",
            )

            continue

        candidate = {
            "symbol": symbol,
            "asset": asset,
            "direction": pattern15["direction"],
            "pattern": pattern15["pattern"],

            "signal_key": signal_key,

            "pattern_time": pattern15["pattern_time"],
            "breakout_time": breakout15["timestamp"],
            "retest_time": retest15["timestamp"],
            "confirmation_time": confirmation["timestamp"],
            "entry_time": confirmation["timestamp"],

            "entry_price": entry,
            "tp_price": tp,
            "sl_price": sl,

            "pattern_1h": pattern1h,
            "pattern_15m": pattern15,

            "score": score,

            "_df15": df15.copy(),
        }

        valid_candidates.append(candidate)

        consider_candidate(
            asset,
            pattern15["direction"],
            pattern15["pattern"],
            score,
            "Signal Ready",
            "-",
            "Valid new signal candidate.",
        )

    if not valid_candidates:
        return None

    valid_candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return valid_candidates[0]


# ============================================================
# CHART
# ============================================================

def create_15m_chart(candidate):

    df = candidate.get("_df15")

    if df is None or df.empty:
        return None

    pattern = candidate["pattern_15m"]

    pattern_time = pattern["pattern_time"]

    idx_list = df.index[
        df["timestamp"] >= pattern_time
    ].tolist()

    if not idx_list:
        start_idx = max(
            0,
            len(df) - CHART_CANDLES
        )
    else:
        start_idx = max(
            0,
            idx_list[0] - 30
        )

    chart_df = df.iloc[
        start_idx:
        start_idx + CHART_CANDLES
    ].copy()

    if chart_df.empty:
        return None

    x = np.arange(len(chart_df))

    fig, ax = plt.subplots(
        figsize=(13, 7)
    )

    # --------------------------------------------------------
    # Candles
    # --------------------------------------------------------

    for i, row in chart_df.iterrows():

        pos = chart_df.index.get_loc(i)

        open_price = row["open"]
        close_price = row["close"]
        high = row["high"]
        low = row["low"]

        ax.plot(
            [pos, pos],
            [low, high],
            linewidth=1
        )

        ax.plot(
            [pos, pos],
            [open_price, close_price],
            linewidth=5
        )

    # --------------------------------------------------------
    # Pattern points
    # --------------------------------------------------------

    def mark_point(index, label):

        if index not in chart_df.index:
            return

        pos = chart_df.index.get_loc(index)

        row = chart_df.loc[index]

        if pattern["pattern"] in (
            "Double Top",
            "Head & Shoulders"
        ):

            price = row["high"]

        else:

            price = row["low"]

        ax.scatter(
            [pos],
            [price],
            s=70,
            zorder=10
        )

        ax.annotate(
            label,
            (pos, price),
            xytext=(0, 10),
            textcoords="offset points",
            ha="center",
            fontsize=9
        )

    if pattern["pattern"] == "Double Top":

        mark_point(
            pattern["pattern_time"],
            "R"
        )

        for key, label in [
            ("left", "L"),
            ("valley", "N"),
            ("right", "R")
        ]:

            idx = pattern.get(key)

            if idx is not None:

                absolute_idx = (
                    chart_df.index[0] + idx
                )

                if absolute_idx in chart_df.index:
                    mark_point(
                        absolute_idx,
                        label
                    )

    elif pattern["pattern"] == "Double Bottom":

        for key, label in [
            ("left", "L"),
            ("valley", "N"),
            ("right", "R")
        ]:

            idx = pattern.get(key)

            if idx is not None:

                absolute_idx = (
                    chart_df.index[0] + idx
                )

                if absolute_idx in chart_df.index:
                    mark_point(
                        absolute_idx,
                        label
                    )

    elif pattern["pattern"] in (
        "Head & Shoulders",
        "Inverse Head & Shoulders"
    ):

        for key, label in [
            ("left_shoulder", "LS"),
            ("head", "H"),
            ("right_shoulder", "RS"),
        ]:

            idx = pattern.get(key)

            if idx is not None:

                absolute_idx = (
                    chart_df.index[0] + idx
                )

                if absolute_idx in chart_df.index:

                    mark_point(
                        absolute_idx,
                        label
                    )

    # --------------------------------------------------------
    # Neckline
    # --------------------------------------------------------

    ax.axhline(
        pattern["neckline"],
        linestyle="--",
        linewidth=1.5,
        label="Neckline"
    )

    # --------------------------------------------------------
    # Breakout
    # --------------------------------------------------------

    breakout_ts = candidate["breakout_time"]

    breakout_rows = chart_df.index[
        chart_df["timestamp"] == breakout_ts
    ]

    if len(breakout_rows):

        idx = breakout_rows[0]

        pos = chart_df.index.get_loc(idx)

        price = chart_df.loc[idx]["close"]

        ax.scatter(
            [pos],
            [price],
            s=80,
            marker="D",
            zorder=12
        )

        ax.annotate(
            "Breakout",
            (pos, price),
            xytext=(0, 15),
            textcoords="offset points",
            ha="center"
        )

    # --------------------------------------------------------
    # Retest
    # --------------------------------------------------------

    retest_ts = candidate["retest_time"]

    retest_rows = chart_df.index[
        chart_df["timestamp"] == retest_ts
    ]

    if len(retest_rows):

        idx = retest_rows[0]

        pos = chart_df.index.get_loc(idx)

        price = chart_df.loc[idx]["close"]

        ax.scatter(
            [pos],
            [price],
            s=80,
            marker="s",
            zorder=12
        )

        ax.annotate(
            "Retest",
            (pos, price),
            xytext=(0, 15),
            textcoords="offset points",
            ha="center"
        )

    # --------------------------------------------------------
    # Entry
    # --------------------------------------------------------

    entry_ts = candidate["entry_time"]

    entry_rows = chart_df.index[
        chart_df["timestamp"] == entry_ts
    ]

    if len(entry_rows):

        idx = entry_rows[0]

        pos = chart_df.index.get_loc(idx)

        ax.scatter(
            [pos],
            [candidate["entry_price"]],
            s=100,
            marker="*",
            zorder=15
        )

        ax.annotate(
            "ENTRY",
            (pos, candidate["entry_price"]),
            xytext=(0, 18),
            textcoords="offset points",
            ha="center",
            fontweight="bold"
        )

    # --------------------------------------------------------
    # TP / SL
    # --------------------------------------------------------

    ax.axhline(
        candidate["tp_price"],
        linestyle=":",
        linewidth=1.5,
        label="TP"
    )

    ax.axhline(
        candidate["sl_price"],
        linestyle=":",
        linewidth=1.5,
        label="SL"
    )

    ax.set_title(
        f"{candidate['asset']} {candidate['direction']} | "
        f"{candidate['pattern']} | 15M"
    )

    ax.set_xlabel("15M Closed Candles")
    ax.set_ylabel("Price")

    ax.grid(
        alpha=0.25
    )

    ax.legend()

    path = (
        f"/tmp/"
        f"{candidate['asset']}_"
        f"{candidate['direction']}_"
        f"15m.png"
    )

    plt.tight_layout()

    plt.savefig(
        path,
        dpi=140
    )

    plt.close(fig)

    return path


# ============================================================
# NEW SIGNAL MESSAGE
# ============================================================

def send_new_signal(candidate):

    direction = candidate["direction"]

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    tp = candidate["tp_price"]
    sl = candidate["sl_price"]
    entry = candidate["entry_price"]

    message = f"""
<b>🚨 NEW SIGNAL</b>

<b>{emoji} {candidate['asset']} {direction}</b>
📌 Pattern: {html.escape(candidate['pattern'])}

💰 Entry: {entry:.8g}
🎯 TP: {tp:.8g}
🛑 SL: {sl:.8g}

⭐ Score: {candidate['score']:.2f}

<b>🕐 Tehran Time</b>

📌 Pattern: {format_time(candidate['pattern_time'])}
📍 1H Breakout: {format_time(candidate['breakout_time'])}
🔄 15M Retest: {format_time(candidate['retest_time'])}
✅ 15M Confirmation / Entry: {format_time(candidate['entry_time'])}

<b>⚙️ PAPER ONLY</b>
"""

    chart = create_15m_chart(
        candidate
    )

    sent = telegram_send(
        message.strip(),
        chart
    )

    if sent:
        STATS["signals_sent"] += 1

    return sent


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY entry_time ASC
    """).fetchall()

    conn.close()

    return rows


# ============================================================
# PERFORMANCE
# ============================================================

def get_performance():

    conn = get_db()

    total = conn.execute(
        "SELECT COUNT(*) FROM trades"
    ).fetchone()[0]

    closed = conn.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
    """).fetchone()[0]

    open_count = conn.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'OPEN'
    """).fetchone()[0]

    wins = conn.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
          AND exit_reason = 'TP'
    """).fetchone()[0]

    losses = conn.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
          AND exit_reason = 'SL'
    """).fetchone()[0]

    conn.close()

    win_rate = (
        wins / closed * 100
        if closed
        else 0
    )

    # --------------------------------------------------------
    # Net PnL calculated from actual entry/exit prices
    # --------------------------------------------------------

    conn = get_db()

    rows = conn.execute("""
        SELECT direction,
               entry_price,
               exit_price
        FROM trades
        WHERE status = 'CLOSED'
          AND exit_price IS NOT NULL
    """).fetchall()

    conn.close()

    pnl = 0.0

    for row in rows:

        entry = row["entry_price"]
        exit_price = row["exit_price"]

        if not entry:
            continue

        if row["direction"] == "LONG":

            pnl += (
                (exit_price - entry)
                / entry
                * 100
            )

        else:

            pnl += (
                (entry - exit_price)
                / entry
                * 100
            )

    return {
        "total": total,
        "closed": closed,
        "open": open_count,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "pnl": pnl,
    }


# ============================================================
# OPEN TRADES TEXT
# ============================================================

def open_trades_text():

    trades = get_open_trades()

    if not trades:

        return (
            "<b>📂 OPEN TRADES: 0</b>\n"
            "No open trades."
        )

    lines = [
        f"<b>📂 OPEN TRADES: {len(trades)}</b>"
    ]

    for trade in trades:

        direction = trade["direction"]

        emoji = (
            "🟢"
            if direction == "LONG"
            else "🔴"
        )

        entry = trade["entry_price"]
        current = trade["current_price"]

        if not current:
            current = entry

        if direction == "LONG":

            change = (
                (current - entry)
                / entry
                * 100
            )

        else:

            change = (
                (entry - current)
                / entry
                * 100
            )

        sign = "+" if change >= 0 else ""

        lines.extend([
            "",
            f"{emoji} <b>{trade['asset']} {direction}</b>",
            f"📌 {html.escape(trade['pattern'])}",
            f"💰 Entry: {entry:.8g}",
            f"💵 Current: {current:.8g} ({sign}{change:.2f}%)",
            f"🎯 TP: {trade['tp_price']:.8g}",
            f"🛑 SL: {trade['sl_price']:.8g}",
            f"⏱ Duration: {duration_text(trade['entry_time'])}",
        ])

    return "\n".join(lines)


# ============================================================
# PERFORMANCE TEXT
# ============================================================

def performance_text():

    p = get_performance()

    return f"""
<b>📈 PERFORMANCE</b>

Total Trades: {p['total']}
Closed: {p['closed']}
Open: {p['open']}
🟢 Wins: {p['wins']}
🔴 Losses: {p['losses']}
Win Rate: {p['win_rate']:.2f}%
Net PnL: {'+' if p['pnl'] >= 0 else ''}{p['pnl']:.2f}%
""".strip()


# ============================================================
# SCAN SUMMARY TEXT
# ============================================================

def scan_summary_text():

    return f"""
<b>🔎 SCAN SUMMARY</b>

Assets Scanned: {STATS['assets_scanned']}/{len(ASSETS)}
1H Patterns: {STATS['patterns_1h']}
1H Breakouts: {STATS['breakouts_1h']}
15M Patterns: {STATS['patterns_15m']}
15M Breakouts: {STATS['breakouts_15m']}
15M Retests: {STATS['retests_15m']}
15M Confirmations: {STATS['confirmations_15m']}
New Signals: {STATS['unique_entries']}
Errors: {STATS['errors']}
""".strip()


# ============================================================
# BEST CANDIDATE TEXT
# ============================================================

def best_candidate_text():

    # --------------------------------------------------------
    # THIS IS THE IMPORTANT FIX.
    #
    # If no actual candidate exists, do NOT use XBT or any
    # other asset as a fake placeholder.
    # --------------------------------------------------------

    if BEST_CANDIDATE is None:

        return """
<b>🏆 BEST CANDIDATE</b>

⚪ <b>No viable candidate</b>
📍 Stage Reached: None
❌ Rejected At: 1H Pattern
📝 Reason: No valid recent 1H pattern was found.
""".strip()

    d = BEST_CANDIDATE

    emoji = (
        "🟢"
        if d["direction"] == "LONG"
        else "🔴"
    )

    return f"""
<b>🏆 BEST CANDIDATE</b>

{emoji} <b>{html.escape(d['asset'])} {html.escape(d['direction'])}</b>
📌 Pattern: {html.escape(d['pattern'])}
⭐ Score: {d['score']:.2f}
📍 Stage Reached: {html.escape(d['stage_reached'])}
❌ Rejected At: {html.escape(d['rejected_at'])}
📝 Reason: {html.escape(d['reason'])}
""".strip()


# ============================================================
# NO SIGNAL REPORT
# ============================================================

def send_no_signal_report():

    message = f"""
<b>📊 KRAKEN PATTERN SCANNER</b>

Version: {VERSION}
Mode: PAPER ONLY
Time: Iran
🕐 {format_time(now_ts())}

{scan_summary_text()}

{best_candidate_text()}

{open_trades_text()}

{performance_text()}
""".strip()

    return telegram_send(
        message
    )


# ============================================================
# PERIODIC REPORT
# ============================================================

def periodic_report():

    last = get_meta(
        "last_periodic_report"
    )

    current = now_ts()

    if last:

        try:

            if (
                current - int(last)
                < PERIODIC_REPORT_SECONDS
            ):
                return False

        except Exception:
            pass

    message = f"""
<b>📊 KRAKEN PATTERN SCANNER</b>

Version: {VERSION}
Mode: PAPER ONLY
Time: Iran
🕐 {format_time(current)}

{open_trades_text()}

{performance_text()}
""".strip()

    sent = telegram_send(
        message
    )

    if sent:

        set_meta(
            "last_periodic_report",
            current
        )

    return sent


# ============================================================
# UPDATE CURRENT PRICES
# ============================================================

def get_current_price(symbol):

    try:

        endpoint = (
            "/derivatives/api/v3/tickers"
        )

        response = SESSION.get(
            KRAKEN_BASE + endpoint,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        tickers = data.get(
            "tickers",
            []
        )

        for ticker in tickers:

            if ticker.get("symbol") == symbol:

                price = (
                    ticker.get("last")
                    or ticker.get("lastPrice")
                )

                if price is not None:
                    return float(price)

    except Exception as exc:

        print(
            f"[PRICE ERROR] {symbol}: {exc}"
        )

    return None


# ============================================================
# RECONCILE OPEN TRADES
# ============================================================

def reconcile_open_trades():

    trades = get_open_trades()

    for trade in trades:

        symbol = trade["symbol"]

        current = get_current_price(
            symbol
        )

        if current is None:
            continue

        direction = trade["direction"]

        tp = trade["tp_price"]
        sl = trade["sl_price"]

        exit_reason = None
        exit_price = None

        if direction == "LONG":

            if current >= tp:

                exit_reason = "TP"
                exit_price = tp

            elif current <= sl:

                exit_reason = "SL"
                exit_price = sl

        else:

            if current <= tp:

                exit_reason = "TP"
                exit_price = tp

            elif current >= sl:

                exit_reason = "SL"
                exit_price = sl

        conn = get_db()

        conn.execute("""
            UPDATE trades
            SET current_price = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            current,
            now_ts(),
            trade["id"]
        ))

        conn.commit()
        conn.close()

        if not exit_reason:
            continue

        conn = get_db()

        conn.execute("""
            UPDATE trades
            SET status = 'CLOSED',
                exit_time = ?,
                exit_price = ?,
                exit_reason = ?,
                current_price = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            now_ts(),
            exit_price,
            exit_reason,
            current,
            now_ts(),
            trade["id"]
        ))

        conn.commit()
        conn.close()

        STATS["closed_trades"] += 1

        emoji = (
            "🟢"
            if exit_reason == "TP"
            else "🔴"
        )

        direction = trade["direction"]

        if direction == "LONG":

            pnl = (
                (exit_price - trade["entry_price"])
                / trade["entry_price"]
                * 100
            )

        else:

            pnl = (
                (trade["entry_price"] - exit_price)
                / trade["entry_price"]
                * 100
            )

        sign = "+" if pnl >= 0 else ""

        message = f"""
<b>📕 TRADE CLOSED</b>

{emoji} <b>{trade['asset']} {direction}</b>
📌 {html.escape(trade['pattern'])}

💰 Entry: {trade['entry_price']:.8g}
💵 Exit: {exit_price:.8g}

{'🎯 TP HIT' if exit_reason == 'TP' else '🛑 SL HIT'}

📊 PnL: {sign}{pnl:.2f}%
⏱ Duration: {duration_text(trade['entry_time'])}

🕐 Tehran:
{format_time(now_ts())}
""".strip()

        telegram_send(
            message
        )


# ============================================================
# RESET STATS
# ============================================================

def reset_stats():

    for key in STATS:
        STATS[key] = 0


# ============================================================
# CONSOLE SUMMARY
# ============================================================

def print_scan_summary():

    print()
    print("=" * 60)
    print("SCAN SUMMARY")
    print("=" * 60)

    print(
        f"Assets Scanned: "
        f"{STATS['assets_scanned']}/{len(ASSETS)}"
    )

    print(
        f"1H Patterns: "
        f"{STATS['patterns_1h']}"
    )

    print(
        f"1H Breakouts: "
        f"{STATS['breakouts_1h']}"
    )

    print(
        f"15M Patterns: "
        f"{STATS['patterns_15m']}"
    )

    print(
        f"15M Breakouts: "
        f"{STATS['breakouts_15m']}"
    )

    print(
        f"15M Retests: "
        f"{STATS['retests_15m']}"
    )

    print(
        f"15M Confirmations: "
        f"{STATS['confirmations_15m']}"
    )

    print(
        f"New Signals: "
        f"{STATS['unique_entries']}"
    )

    print(
        f"Errors: "
        f"{STATS['errors']}"
    )

    print("=" * 60)
    print("BEST CANDIDATE")
    print("=" * 60)

    if BEST_CANDIDATE is None:

        print("No viable candidate.")
        print("Stage Reached: None")
        print("Rejected At: 1H Pattern")
        print(
            "Reason: No valid recent 1H pattern was found."
        )

    else:

        d = BEST_CANDIDATE

        print(
            f"{d['asset']} {d['direction']}"
        )

        print(
            f"Pattern: {d['pattern']}"
        )

        print(
            f"Score: {d['score']:.2f}"
        )

        print(
            f"Stage Reached: {d['stage_reached']}"
        )

        print(
            f"Rejected At: {d['rejected_at']}"
        )

        print(
            f"Reason: {d['reason']}"
        )

    print("=" * 60)
    print()


# ============================================================
# SCAN
# ============================================================

def scan():

    reset_stats()
    reset_best_candidate()

    STATS["scans"] = 1

    all_candidates = []

    for asset in ASSETS:

        print(
            f"[SCAN] {asset}"
        )

        try:

            candidate = analyze_asset(
                asset
            )

            if candidate is not None:

                all_candidates.append(
                    candidate
                )

        except Exception as exc:

            STATS["errors"] += 1

            print(
                f"[ERROR] {asset}: {exc}"
            )

            traceback.print_exc()

    # --------------------------------------------------------
    # Highest quality valid candidate first
    # --------------------------------------------------------

    all_candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    inserted = 0

    # --------------------------------------------------------
    # MAX_SIGNALS_PER_SCAN
    # --------------------------------------------------------

    for candidate in all_candidates:

        if inserted >= MAX_SIGNALS_PER_SCAN:
            break

        try:

            success = insert_trade(
                candidate
            )

            if not success:

                continue

            inserted += 1

            STATS["unique_entries"] += 1

            print(
                f"[NEW SIGNAL] "
                f"{candidate['asset']} "
                f"{candidate['direction']} "
                f"{candidate['pattern']}"
            )

            # ------------------------------------------------
            # Send immediately
            # ------------------------------------------------

            send_new_signal(
                candidate
            )

        except Exception as exc:

            STATS["errors"] += 1

            print(
                f"[INSERT ERROR] "
                f"{candidate['asset']}: {exc}"
            )

    # --------------------------------------------------------
    # If a real signal was inserted, BEST_CANDIDATE may
    # already be Signal Ready.
    #
    # If there was no insertion, diagnostics above are used.
    # --------------------------------------------------------

    return inserted


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 60)
    print(
        f"KRAKEN PATTERN LIVE SCANNER "
        f"VERSION {VERSION}"
    )
    print("=" * 60)

    print(
        "MODE: PAPER ONLY"
    )

    print(
        f"Assets: {len(ASSETS)}"
    )

    print(
        f"DB: {DB_FILE}"
    )

    print("=" * 60)

    if REAL_TRADING:

        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    init_db()

    # --------------------------------------------------------
    # First reconcile existing trades
    # --------------------------------------------------------

    reconcile_open_trades()

    # --------------------------------------------------------
    # Run complete scan
    # --------------------------------------------------------

    inserted = scan()

    # --------------------------------------------------------
    # GitHub Actions ALWAYS gets scan summary
    # --------------------------------------------------------

    print_scan_summary()

    # --------------------------------------------------------
    # TELEGRAM LOGIC
    #
    # New signal:
    #   -> normal NEW SIGNAL only
    #
    # No new signal:
    #   -> scan summary
    #   -> best candidate
    #   -> open trades
    #   -> performance
    #
    # This prevents fake XBT candidate reports.
    # --------------------------------------------------------

    if inserted == 0:

        sent = send_no_signal_report()

        # ----------------------------------------------------
        # The no-signal report already contains the current
        # scan status, so don't immediately send another
        # periodic report with duplicated information.
        # ----------------------------------------------------

        if sent:

            set_meta(
                "last_periodic_report",
                now_ts()
            )

    else:

        # ----------------------------------------------------
        # Periodic report remains separate and contains only
        # Open Trades + Performance.
        # It does NOT repeat scan summary.
        # ----------------------------------------------------

        periodic_report()

    print()
    print(
        "SCAN COMPLETE."
    )

    print(
        f"New signals inserted: {inserted}"
    )

    print()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
