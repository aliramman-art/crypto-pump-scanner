# ============================================================
# KRAKEN FUTURES PATTERN LIVE SIGNAL SCANNER
# VERSION 6.3.0
# ============================================================
#
# PAPER ONLY
#
# STRATEGY
# ------------------------------------------------------------
# 1H REAL SWING PATTERN
#       ↓
# 1H CLOSED-CANDLE BREAKOUT
#       ↓
# 15M REAL SWING PATTERN
#       ↓
# 15M CLOSED-CANDLE BREAKOUT
#       ↓
# FIRST 15M RETEST
#       ↓
# FIRST VALID 15M CONFIRMATION
#       ↓
# ENTRY
#
# IMPORTANT
# ------------------------------------------------------------
# - 1H and 15M patterns DO NOT need to be the same type
# - Direction must remain the same
# - 15M pattern determines TP / SL
# - Closed candles only
# - No lookahead
# - First retest only
# - First valid confirmation only
# - Existing DB is preserved
# - PAPER ONLY
# - REAL_TRADING = False
#
# DATA
# ------------------------------------------------------------
# Kraken Futures Charts API:
# /api/charts/v1/trade/{symbol}/{resolution}
#
# Example:
# /api/charts/v1/trade/PF_XBTUSD/1h
# /api/charts/v1/trade/PF_XBTUSD/15m
#
# ============================================================

import os
import time
import sqlite3
import traceback
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "6.3.0"

REAL_TRADING = False

DB_FILE = "kraken_pattern_live_v52.db"

KRAKEN_BASE = "https://futures.kraken.com"

TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))

MAX_HOLD_HOURS = 48

# Data
MIN_CANDLES_REQUIRED = 60

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

CHART_CANDLES = 80

# ------------------------------------------------------------
# REALISTIC PATTERN PARAMETERS
# ------------------------------------------------------------

# Swing detection
SWING_LEFT_RIGHT = 3

# Minimum bars between important swing points
MIN_PATTERN_SEPARATION_BARS = 3

# Maximum bars allowed between first and second peak/trough
MAX_DOUBLE_PATTERN_BARS = 48

# Maximum bars allowed for complete H&S structure
MAX_HS_PATTERN_BARS = 72

# Double Top / Bottom
DOUBLE_LEVEL_TOLERANCE = 0.012
MIN_PATTERN_DEPTH_PCT = 0.004
MIN_DOUBLE_VALLEY_DEPTH_PCT = 0.004

# Head & Shoulders
HS_SHOULDER_TOLERANCE = 0.025
HS_MIN_HEAD_ADVANTAGE_PCT = 0.003
MIN_HS_DEPTH_PCT = 0.004

# Pattern invalidation
INVALIDATION_BUFFER = 0.001

# Minimum score
MIN_PATTERN_SCORE = 2.0


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
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "Kraken-Pattern-Scanner/"
            + VERSION
        )
    }
)


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


def telegram_enabled():
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def telegram_url(method="sendMessage"):
    return (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/{method}"
    )


def send_telegram(text):
    if not telegram_enabled():
        return False

    try:
        response = SESSION.post(
            telegram_url("sendMessage"),
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )

        response.raise_for_status()
        return True

    except Exception as exc:
        print(f"[TELEGRAM ERROR] {exc}")
        return False


def send_telegram_photo(photo_path, caption):
    if not telegram_enabled():
        return False

    if not photo_path or not os.path.exists(photo_path):
        return False

    try:
        with open(photo_path, "rb") as photo:
            response = SESSION.post(
                telegram_url("sendPhoto"),
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption,
                    "parse_mode": "HTML",
                },
                files={
                    "photo": photo,
                },
                timeout=30,
            )

        response.raise_for_status()
        return True

    except Exception as exc:
        print(f"[TELEGRAM PHOTO ERROR] {exc}")
        return False


# ============================================================
# TIME
# ============================================================

def now_ts():
    return int(time.time())


def format_time(ts):
    if ts is None:
        return "-"

    try:
        dt = datetime.fromtimestamp(
            float(ts),
            tz=TEHRAN_TZ,
        )

        return dt.strftime("%Y-%m-%d %H:%M")

    except Exception:
        return "-"


def duration_text(start_ts):
    if not start_ts:
        return "-"

    seconds = max(0, now_ts() - int(start_ts))

    minutes = seconds // 60
    hours = minutes // 60
    minutes %= 60

    if hours:
        return f"{hours}h {minutes}m"

    return f"{minutes}m"


# ============================================================
# STATS
# ============================================================

STATS = {}


def reset_stats():
    global STATS

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


reset_stats()


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


def consider_candidate(asset, stage, score, reason=""):
    global BEST_CANDIDATE

    if not asset:
        return

    candidate = {
        "asset": asset,
        "stage": stage,
        "score": float(score or 0),
        "reason": reason,
    }

    if BEST_CANDIDATE is None:
        BEST_CANDIDATE = candidate
        return

    old_rank = STAGE_RANK.get(
        BEST_CANDIDATE.get("stage"),
        0,
    )

    new_rank = STAGE_RANK.get(stage, 0)

    if new_rank > old_rank:
        BEST_CANDIDATE = candidate

    elif new_rank == old_rank:
        if candidate["score"] > BEST_CANDIDATE["score"]:
            BEST_CANDIDATE = candidate


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


def init_db():

    conn = db_connect()

    try:

        conn.execute(
            """
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

        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(trades)"
            ).fetchall()
        }

        migrations = {
            "exit_reason": "TEXT",
            "breakout_time": "INTEGER",
            "retest_time": "INTEGER",
            "confirmation_time": "INTEGER",
        }

        for name, dtype in migrations.items():

            if name not in columns:

                conn.execute(
                    f"""
                    ALTER TABLE trades
                    ADD COLUMN {name} {dtype}
                    """
                )

        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_signal_key
            ON trades(signal_key)
            """
        )

        conn.commit()

    finally:
        conn.close()


# ============================================================
# DATABASE HELPERS
# ============================================================

def has_signal(signal_key):
    conn = db_connect()

    try:

        row = conn.execute(
            """
            SELECT id
            FROM trades
            WHERE signal_key = ?
            LIMIT 1
            """,
            (signal_key,),
        ).fetchone()

        return row is not None

    finally:
        conn.close()


def has_open_same_direction(asset, direction):

    conn = db_connect()

    try:

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

        return row is not None

    finally:
        conn.close()


def insert_trade(trade):

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
                exit_time,
                exit_price,
                exit_reason,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade["symbol"],
                trade["asset"],
                trade["direction"],
                trade["pattern"],
                trade["signal_key"],
                trade["pattern_time"],
                trade["breakout_time"],
                trade["retest_time"],
                trade["confirmation_time"],
                trade["entry_time"],
                trade["entry_price"],
                trade["sl_price"],
                trade["tp_price"],
                trade["current_price"],
                "OPEN",
                None,
                None,
                None,
                now_ts(),
                now_ts(),
            ),
        )

        conn.commit()

        return cur.rowcount == 1

    finally:
        conn.close()


def get_open_trades():

    conn = db_connect()

    try:

        return conn.execute(
            """
            SELECT *
            FROM trades
            WHERE status = 'OPEN'
            ORDER BY entry_time ASC
            """
        ).fetchall()

    finally:
        conn.close()


# ============================================================
# KRAKEN CANDLES
# ============================================================

def get_candles(symbol, resolution, limit):

    """
    Correct Kraken Futures Charts endpoint:

    /api/charts/v1/trade/{symbol}/{resolution}

    Example:
    /api/charts/v1/trade/PF_XBTUSD/1h
    /api/charts/v1/trade/PF_XBTUSD/15m
    """

    endpoint = (
        f"/api/charts/v1/trade/"
        f"{symbol}/{resolution}"
    )

    url = KRAKEN_BASE + endpoint

    try:

        response = SESSION.get(
            url,
            timeout=20,
        )

        response.raise_for_status()

        data = response.json()

        rows = data.get("candles", [])

        if not isinstance(rows, list):
            return pd.DataFrame()

        if not rows:
            return pd.DataFrame()

        parsed = []

        for row in rows:

            try:

                if isinstance(row, dict):

                    ts = row.get("time")

                    if ts is None:
                        continue

                    parsed.append(
                        {
                            "timestamp": int(ts) / 1000,
                            "open": float(row["open"]),
                            "high": float(row["high"]),
                            "low": float(row["low"]),
                            "close": float(row["close"]),
                            "volume": float(
                                row.get(
                                    "volume",
                                    0,
                                )
                            ),
                        }
                    )

                elif isinstance(row, list):

                    if len(row) < 6:
                        continue

                    parsed.append(
                        {
                            "timestamp": int(row[0]) / 1000,
                            "open": float(row[1]),
                            "high": float(row[2]),
                            "low": float(row[3]),
                            "close": float(row[4]),
                            "volume": float(row[5]),
                        }
                    )

            except Exception:
                continue

        if not parsed:
            return pd.DataFrame()

        df = pd.DataFrame(parsed)

        df = df.drop_duplicates(
            subset=["timestamp"]
        )

        df = df.sort_values(
            "timestamp"
        ).reset_index(drop=True)

        candle_seconds = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "4h": 14400,
            "12h": 43200,
            "1d": 86400,
            "1w": 604800,
        }.get(
            resolution,
            900,
        )

        current = time.time()

        # Closed candles only.
        df = df[
            (
                df["timestamp"]
                + candle_seconds
                <= current
            )
        ].copy()

        if limit:
            df = df.tail(limit)

        return df.reset_index(drop=True)

    except Exception as exc:

        STATS["errors"] += 1

        print(
            f"[DATA ERROR] "
            f"{symbol} {resolution}: {exc}"
        )

        return pd.DataFrame()


# ============================================================
# CURRENT PRICE
# ============================================================

_TICKER_CACHE = {
    "time": 0,
    "prices": {},
}


def get_all_current_prices():

    global _TICKER_CACHE

    if (
        time.time()
        - _TICKER_CACHE["time"]
        < 10
        and _TICKER_CACHE["prices"]
    ):
        return _TICKER_CACHE["prices"]

    try:

        response = SESSION.get(
            KRAKEN_BASE
            + "/derivatives/api/v3/tickers",
            timeout=20,
        )

        response.raise_for_status()

        data = response.json()

        tickers = data.get(
            "tickers",
            [],
        )

        prices = {}

        for ticker in tickers:

            symbol = str(
                ticker.get(
                    "symbol",
                    ""
                )
            ).upper()

            value = (
                ticker.get("markPrice")
                or ticker.get("last")
                or ticker.get("lastPrice")
            )

            if not symbol or value is None:
                continue

            try:
                prices[symbol] = float(value)
            except Exception:
                continue

        _TICKER_CACHE = {
            "time": time.time(),
            "prices": prices,
        }

        return prices

    except Exception as exc:

        print(
            f"[TICKER ERROR] {exc}"
        )

        return _TICKER_CACHE.get(
            "prices",
            {},
        )


def get_current_price(symbol):

    prices = get_all_current_prices()

    return prices.get(
        symbol.upper()
    )


# ============================================================
# SWING DETECTION
# ============================================================

def find_swing_highs(
    df,
    left=SWING_LEFT_RIGHT,
    right=SWING_LEFT_RIGHT,
):

    highs = df["high"].tolist()

    result = []

    for i in range(
        left,
        len(df) - right,
    ):

        value = highs[i]

        left_values = highs[
            i - left:i
        ]

        right_values = highs[
            i + 1:i + right + 1
        ]

        if (
            value >= max(left_values)
            and value >= max(right_values)
            and (
                value > max(left_values)
                or value > max(right_values)
            )
        ):
            result.append(i)

    return result


def find_swing_lows(
    df,
    left=SWING_LEFT_RIGHT,
    right=SWING_LEFT_RIGHT,
):

    lows = df["low"].tolist()

    result = []

    for i in range(
        left,
        len(df) - right,
    ):

        value = lows[i]

        left_values = lows[
            i - left:i
        ]

        right_values = lows[
            i + 1:i + right + 1
        ]

        if (
            value <= min(left_values)
            and value <= min(right_values)
            and (
                value < min(left_values)
                or value < min(right_values)
            )
        ):
            result.append(i)

    return result


# ============================================================
# PATTERN SCORE
# ============================================================

def pattern_quality_base(depth_pct):
    return max(
        0.0,
        min(
            10.0,
            depth_pct * 1000,
        ),
    )


# ============================================================
# DOUBLE TOP
# ============================================================

def detect_double_tops(df):

    swings = find_swing_highs(df)

    lows = find_swing_lows(df)

    patterns = []

    for a in range(len(swings)):

        i = swings[a]

        for b in range(
            a + 1,
            len(swings),
        ):

            j = swings[b]

            separation = j - i

            if separation < MIN_PATTERN_SEPARATION_BARS:
                continue

            if separation > MAX_DOUBLE_PATTERN_BARS:
                break

            p1 = float(df.iloc[i]["high"])
            p2 = float(df.iloc[j]["high"])

            avg_peak = (p1 + p2) / 2

            level_difference = (
                abs(p1 - p2)
                / avg_peak
            )

            if (
                level_difference
                > DOUBLE_LEVEL_TOLERANCE
            ):
                continue

            between_lows = [
                x
                for x in lows
                if i < x < j
            ]

            if not between_lows:
                continue

            valley_idx = min(
                between_lows,
                key=lambda x: float(
                    df.iloc[x]["low"]
                ),
            )

            valley = float(
                df.iloc[valley_idx]["low"]
            )

            depth_pct = (
                avg_peak - valley
            ) / avg_peak

            if (
                depth_pct
                < MIN_DOUBLE_VALLEY_DEPTH_PCT
            ):
                continue

            # The second peak should not be
            # simply a tiny continuation move.
            if p2 <= valley:
                continue

            score = (
                pattern_quality_base(
                    depth_pct
                )
                + max(
                    0,
                    5
                    * (
                        1
                        - (
                            level_difference
                            / DOUBLE_LEVEL_TOLERANCE
                        )
                    ),
                )
            )

            pattern_time = int(
                df.iloc[j]["timestamp"]
            )

            target = (
                valley
                - (
                    avg_peak
                    - valley
                )
            )

            stop = (
                max(p1, p2)
                * (
                    1
                    + INVALIDATION_BUFFER
                )
            )

            patterns.append(
                {
                    "pattern": "Double Top",
                    "direction": "SHORT",
                    "left_idx": i,
                    "right_idx": j,
                    "neckline_idx": valley_idx,
                    "pattern_time": pattern_time,
                    "neckline": valley,
                    "target": target,
                    "stop": stop,
                    "depth_pct": depth_pct,
                    "score": score,
                }
            )

    return patterns


# ============================================================
# DOUBLE BOTTOM
# ============================================================

def detect_double_bottoms(df):

    swings = find_swing_lows(df)

    highs = find_swing_highs(df)

    patterns = []

    for a in range(len(swings)):

        i = swings[a]

        for b in range(
            a + 1,
            len(swings),
        ):

            j = swings[b]

            separation = j - i

            if separation < MIN_PATTERN_SEPARATION_BARS:
                continue

            if separation > MAX_DOUBLE_PATTERN_BARS:
                break

            p1 = float(df.iloc[i]["low"])
            p2 = float(df.iloc[j]["low"])

            avg_bottom = (
                p1 + p2
            ) / 2

            level_difference = (
                abs(p1 - p2)
                / avg_bottom
            )

            if (
                level_difference
                > DOUBLE_LEVEL_TOLERANCE
            ):
                continue

            between_highs = [
                x
                for x in highs
                if i < x < j
            ]

            if not between_highs:
                continue

            peak_idx = max(
                between_highs,
                key=lambda x: float(
                    df.iloc[x]["high"]
                ),
            )

            peak = float(
                df.iloc[peak_idx]["high"]
            )

            depth_pct = (
                peak - avg_bottom
            ) / avg_bottom

            if (
                depth_pct
                < MIN_DOUBLE_VALLEY_DEPTH_PCT
            ):
                continue

            if peak <= avg_bottom:
                continue

            score = (
                pattern_quality_base(
                    depth_pct
                )
                + max(
                    0,
                    5
                    * (
                        1
                        - (
                            level_difference
                            / DOUBLE_LEVEL_TOLERANCE
                        )
                    ),
                )
            )

            pattern_time = int(
                df.iloc[j]["timestamp"]
            )

            target = (
                peak
                + (
                    peak
                    - avg_bottom
                )
            )

            stop = (
                min(p1, p2)
                * (
                    1
                    - INVALIDATION_BUFFER
                )
            )

            patterns.append(
                {
                    "pattern": "Double Bottom",
                    "direction": "LONG",
                    "left_idx": i,
                    "right_idx": j,
                    "neckline_idx": peak_idx,
                    "pattern_time": pattern_time,
                    "neckline": peak,
                    "target": target,
                    "stop": stop,
                    "depth_pct": depth_pct,
                    "score": score,
                }
            )

    return patterns


# ============================================================
# HEAD & SHOULDERS
# ============================================================

def detect_head_shoulders(df):

    highs = find_swing_highs(df)
    lows = find_swing_lows(df)

    patterns = []

    for a in range(len(highs)):

        left_idx = highs[a]

        for b in range(
            a + 1,
            len(highs),
        ):

            head_idx = highs[b]

            if (
                head_idx - left_idx
                < MIN_PATTERN_SEPARATION_BARS
            ):
                continue

            if (
                head_idx - left_idx
                > MAX_HS_PATTERN_BARS
            ):
                break

            left = float(
                df.iloc[left_idx]["high"]
            )

            head = float(
                df.iloc[head_idx]["high"]
            )

            # Head must clearly exceed left shoulder
            if (
                head
                < left
                * (
                    1
                    + HS_MIN_HEAD_ADVANTAGE_PCT
                )
            ):
                continue

            middle_lows = [
                x
                for x in lows
                if left_idx < x < head_idx
            ]

            if not middle_lows:
                continue

            valley1_idx = min(
                middle_lows,
                key=lambda x: float(
                    df.iloc[x]["low"]
                ),
            )

            valley1 = float(
                df.iloc[valley1_idx]["low"]
            )

            for c in range(
                b + 1,
                len(highs),
            ):

                right_idx = highs[c]

                total_bars = (
                    right_idx
                    - left_idx
                )

                if (
                    total_bars
                    < (
                        2
                        * MIN_PATTERN_SEPARATION_BARS
                    )
                ):
                    continue

                if (
                    total_bars
                    > MAX_HS_PATTERN_BARS
                ):
                    break

                right = float(
                    df.iloc[right_idx]["high"]
                )

                shoulder_diff = (
                    abs(left - right)
                    / (
                        (left + right)
                        / 2
                    )
                )

                if (
                    shoulder_diff
                    > HS_SHOULDER_TOLERANCE
                ):
                    continue

                second_lows = [
                    x
                    for x in lows
                    if head_idx < x < right_idx
                ]

                if not second_lows:
                    continue

                valley2_idx = min(
                    second_lows,
                    key=lambda x: float(
                        df.iloc[x]["low"]
                    ),
                )

                valley2 = float(
                    df.iloc[valley2_idx]["low"]
                )

                neckline = (
                    valley1
                    + valley2
                ) / 2

                depth_pct = (
                    head - neckline
                ) / neckline

                if (
                    depth_pct
                    < MIN_HS_DEPTH_PCT
                ):
                    continue

                # Head must exceed right shoulder too.
                if (
                    head
                    < right
                    * (
                        1
                        + HS_MIN_HEAD_ADVANTAGE_PCT
                    )
                ):
                    continue

                symmetry_score = max(
                    0,
                    5
                    * (
                        1
                        - (
                            shoulder_diff
                            / HS_SHOULDER_TOLERANCE
                        )
                    ),
                )

                depth_score = pattern_quality_base(
                    depth_pct
                )

                score = (
                    symmetry_score
                    + depth_score
                )

                target = (
                    neckline
                    - (
                        head
                        - neckline
                    )
                )

                stop = (
                    head
                    * (
                        1
                        + INVALIDATION_BUFFER
                    )
                )

                patterns.append(
                    {
                        "pattern": "Head & Shoulders",
                        "direction": "SHORT",
                        "left_idx": left_idx,
                        "head_idx": head_idx,
                        "right_idx": right_idx,
                        "neckline_idx": valley2_idx,
                        "valley1_idx": valley1_idx,
                        "valley2_idx": valley2_idx,
                        "pattern_time": int(
                            df.iloc[right_idx]["timestamp"]
                        ),
                        "neckline": neckline,
                        "target": target,
                        "stop": stop,
                        "depth_pct": depth_pct,
                        "score": score,
                    }
                )

    return patterns


# ============================================================
# INVERSE HEAD & SHOULDERS
# ============================================================

def detect_inverse_head_shoulders(df):

    lows = find_swing_lows(df)
    highs = find_swing_highs(df)

    patterns = []

    for a in range(len(lows)):

        left_idx = lows[a]

        for b in range(
            a + 1,
            len(lows),
        ):

            head_idx = lows[b]

            if (
                head_idx - left_idx
                < MIN_PATTERN_SEPARATION_BARS
            ):
                continue

            if (
                head_idx - left_idx
                > MAX_HS_PATTERN_BARS
            ):
                break

            left = float(
                df.iloc[left_idx]["low"]
            )

            head = float(
                df.iloc[head_idx]["low"]
            )

            if (
                head
                > left
                * (
                    1
                    - HS_MIN_HEAD_ADVANTAGE_PCT
                )
            ):
                continue

            middle_highs = [
                x
                for x in highs
                if left_idx < x < head_idx
            ]

            if not middle_highs:
                continue

            valley1_idx = max(
                middle_highs,
                key=lambda x: float(
                    df.iloc[x]["high"]
                ),
            )

            valley1 = float(
                df.iloc[valley1_idx]["high"]
            )

            for c in range(
                b + 1,
                len(lows),
            ):

                right_idx = lows[c]

                total_bars = (
                    right_idx
                    - left_idx
                )

                if (
                    total_bars
                    < (
                        2
                        * MIN_PATTERN_SEPARATION_BARS
                    )
                ):
                    continue

                if (
                    total_bars
                    > MAX_HS_PATTERN_BARS
                ):
                    break

                right = float(
                    df.iloc[right_idx]["low"]
                )

                shoulder_diff = (
                    abs(left - right)
                    / (
                        (left + right)
                        / 2
                    )
                )

                if (
                    shoulder_diff
                    > HS_SHOULDER_TOLERANCE
                ):
                    continue

                second_highs = [
                    x
                    for x in highs
                    if head_idx < x < right_idx
                ]

                if not second_highs:
                    continue

                valley2_idx = max(
                    second_highs,
                    key=lambda x: float(
                        df.iloc[x]["high"]
                    ),
                )

                valley2 = float(
                    df.iloc[valley2_idx]["high"]
                )

                neckline = (
                    valley1
                    + valley2
                ) / 2

                depth_pct = (
                    neckline - head
                ) / neckline

                if (
                    depth_pct
                    < MIN_HS_DEPTH_PCT
                ):
                    continue

                if (
                    head
                    > right
                    * (
                        1
                        - HS_MIN_HEAD_ADVANTAGE_PCT
                    )
                ):
                    continue

                symmetry_score = max(
                    0,
                    5
                    * (
                        1
                        - (
                            shoulder_diff
                            / HS_SHOULDER_TOLERANCE
                        )
                    ),
                )

                depth_score = pattern_quality_base(
                    depth_pct
                )

                score = (
                    symmetry_score
                    + depth_score
                )

                target = (
                    neckline
                    + (
                        neckline
                        - head
                    )
                )

                stop = (
                    head
                    * (
                        1
                        - INVALIDATION_BUFFER
                    )
                )

                patterns.append(
                    {
                        "pattern": "Inverse Head & Shoulders",
                        "direction": "LONG",
                        "left_idx": left_idx,
                        "head_idx": head_idx,
                        "right_idx": right_idx,
                        "neckline_idx": valley2_idx,
                        "valley1_idx": valley1_idx,
                        "valley2_idx": valley2_idx,
                        "pattern_time": int(
                            df.iloc[right_idx]["timestamp"]
                        ),
                        "neckline": neckline,
                        "target": target,
                        "stop": stop,
                        "depth_pct": depth_pct,
                        "score": score,
                    }
                )

    return patterns


# ============================================================
# ALL PATTERNS
# ============================================================

def detect_patterns(df):

    patterns = []

    patterns.extend(
        detect_double_tops(df)
    )

    patterns.extend(
        detect_double_bottoms(df)
    )

    patterns.extend(
        detect_head_shoulders(df)
    )

    patterns.extend(
        detect_inverse_head_shoulders(df)
    )

    # Remove duplicate patterns with same
    # type / direction / timestamp.
    unique = {}

    for pattern in patterns:

        key = (
            pattern["pattern"],
            pattern["direction"],
            pattern["pattern_time"],
            round(
                float(pattern["neckline"]),
                12,
            ),
        )

        old = unique.get(key)

        if (
            old is None
            or pattern["score"]
            > old["score"]
        ):
            unique[key] = pattern

    result = list(unique.values())

    result.sort(
        key=lambda x: (
            x["pattern_time"],
            x["score"],
        ),
        reverse=True,
    )

    return result


# ============================================================
# RECENT PATTERNS
# ============================================================

def recent_patterns(
    patterns,
    hours,
):

    cutoff = (
        now_ts()
        - hours * 3600
    )

    return [
        p
        for p in patterns
        if p["pattern_time"] >= cutoff
    ]


# ============================================================
# 1H BREAKOUT
# ============================================================

def find_1h_breakout(
    df,
    pattern,
):

    pattern_time = pattern[
        "pattern_time"
    ]

    end_time = (
        pattern_time
        + BREAKOUT_LOOKAHEAD_1H * 3600
    )

    direction = pattern[
        "direction"
    ]

    for i in range(len(df)):

        row = df.iloc[i]

        ts = int(row["timestamp"])

        if ts <= pattern_time:
            continue

        if ts > end_time:
            break

        if direction == "LONG":

            if (
                float(row["close"])
                > float(pattern["neckline"])
            ):

                return {
                    "index": i,
                    "timestamp": ts,
                    "price": float(row["close"]),
                }

        else:

            if (
                float(row["close"])
                < float(pattern["neckline"])
            ):

                return {
                    "index": i,
                    "timestamp": ts,
                    "price": float(row["close"]),
                }

    return None


# ============================================================
# 15M PATTERN
# ============================================================

def find_15m_pattern(
    df15,
    breakout_1h,
):

    patterns = detect_patterns(df15)

    cutoff_start = breakout_1h[
        "timestamp"
    ]

    cutoff_end = (
        cutoff_start
        + RECENT_PATTERN_HOURS_15M * 3600
    )

    candidates = []

    for pattern in patterns:

        pt = pattern[
            "pattern_time"
        ]

        if pt <= cutoff_start:
            continue

        if pt > cutoff_end:
            continue

        # IMPORTANT:
        # Pattern type does NOT need to match 1H.
        candidates.append(pattern)

    if not candidates:
        return None

    # Prefer the most recent valid structure,
    # then quality.
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

    pattern_time = pattern15[
        "pattern_time"
    ]

    end_time = (
        pattern_time
        + BREAKOUT_LOOKAHEAD_15M * 900
    )

    direction = pattern15[
        "direction"
    ]

    for i in range(len(df15)):

        row = df15.iloc[i]

        ts = int(row["timestamp"])

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
                    "index": i,
                    "timestamp": ts,
                    "price": close,
                }

        else:

            if close < neckline:

                return {
                    "index": i,
                    "timestamp": ts,
                    "price": close,
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

    direction = pattern15[
        "direction"
    ]

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
            low <= neckline <= high
        )

        if not touched:
            continue

        if direction == "LONG":

            if close >= neckline:

                return {
                    "index": i,
                    "timestamp": int(
                        row["timestamp"]
                    ),
                    "price": close,
                }

        else:

            if close <= neckline:

                return {
                    "index": i,
                    "timestamp": int(
                        row["timestamp"]
                    ),
                    "price": close,
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

    direction = pattern15[
        "direction"
    ]

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

        if direction == "LONG":

            bullish = (
                close > open_price
            )

            if (
                bullish
                and close > neckline
            ):

                return {
                    "index": i,
                    "timestamp": int(
                        row["timestamp"]
                    ),
                    "price": close,
                }

        else:

            bearish = (
                close < open_price
            )

            if (
                bearish
                and close < neckline
            ):

                return {
                    "index": i,
                    "timestamp": int(
                        row["timestamp"]
                    ),
                    "price": close,
                }

    return None


# ============================================================
# DIRECTION / TARGET VALIDATION
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

    # --------------------------------------------------------
    # 1H
    # --------------------------------------------------------

    df1h = get_candles(
        symbol,
        "1h",
        PATTERN_LOOKBACK_1H,
    )

    if len(df1h) < MIN_CANDLES_REQUIRED:

        consider_candidate(
            asset,
            "None",
            0,
            "Insufficient 1H data",
        )

        return None

    # --------------------------------------------------------
    # 15M
    # --------------------------------------------------------

    df15 = get_candles(
        symbol,
        "15m",
        PATTERN_LOOKBACK_15M,
    )

    if len(df15) < MIN_CANDLES_REQUIRED:

        consider_candidate(
            asset,
            "1H Pattern",
            0,
            "Insufficient 15M data",
        )

        return None

    # Only count an asset as scanned
    # after BOTH timeframes are valid.
    STATS["assets_scanned"] += 1

    # --------------------------------------------------------
    # 1H PATTERNS
    # --------------------------------------------------------

    patterns1h = detect_patterns(
        df1h
    )

    patterns1h = recent_patterns(
        patterns1h,
        RECENT_PATTERN_HOURS_1H,
    )

    STATS["patterns_1h"] += len(
        patterns1h
    )

    if not patterns1h:

        consider_candidate(
            asset,
            "None",
            0,
            "No valid recent 1H pattern",
        )

        return None

    # Strongest / newest first
    patterns1h.sort(
        key=lambda p: (
            p["pattern_time"],
            p["score"],
        ),
        reverse=True,
    )

    # --------------------------------------------------------
    # TRY 1H PATTERNS
    # --------------------------------------------------------

    for pattern1h in patterns1h:

        consider_candidate(
            asset,
            "1H Pattern",
            pattern1h["score"],
            pattern1h["pattern"],
        )

        breakout1h = find_1h_breakout(
            df1h,
            pattern1h,
        )

        if not breakout1h:
            continue

        STATS["breakouts_1h"] += 1

        consider_candidate(
            asset,
            "1H Breakout",
            pattern1h["score"],
            pattern1h["pattern"],
        )

        # ----------------------------------------------------
        # 15M PATTERN
        # ----------------------------------------------------

        pattern15 = find_15m_pattern(
            df15,
            breakout1h,
        )

        if not pattern15:
            continue

        STATS["patterns_15m"] += 1

        # IMPORTANT:
        # Only direction must match.
        if (
            pattern15["direction"]
            != pattern1h["direction"]
        ):
            continue

        consider_candidate(
            asset,
            "15M Pattern",
            pattern15["score"],
            (
                f"1H={pattern1h['pattern']} "
                f"15M={pattern15['pattern']}"
            ),
        )

        # ----------------------------------------------------
        # 15M BREAKOUT
        # ----------------------------------------------------

        breakout15 = find_15m_breakout(
            df15,
            pattern15,
        )

        if not breakout15:
            continue

        STATS["breakouts_15m"] += 1

        consider_candidate(
            asset,
            "15M Breakout",
            pattern15["score"],
            pattern15["pattern"],
        )

        # ----------------------------------------------------
        # RETEST
        # ----------------------------------------------------

        retest15 = find_first_retest(
            df15,
            pattern15,
            breakout15,
        )

        if not retest15:
            continue

        STATS["retests_15m"] += 1

        consider_candidate(
            asset,
            "15M Retest",
            pattern15["score"],
            pattern15["pattern"],
        )

        # ----------------------------------------------------
        # CONFIRMATION
        # ----------------------------------------------------

        confirmation15 = find_confirmation(
            df15,
            pattern15,
            retest15,
        )

        if not confirmation15:
            continue

        STATS["confirmations_15m"] += 1

        consider_candidate(
            asset,
            "15M Confirmation",
            pattern15["score"],
            pattern15["pattern"],
        )

        # ----------------------------------------------------
        # ENTRY
        # ----------------------------------------------------

        entry = float(
            confirmation15["price"]
        )

        tp = float(
            pattern15["target"]
        )

        sl = float(
            pattern15["stop"]
        )

        direction = pattern15[
            "direction"
        ]

        if not validate_trade_levels(
            direction,
            entry,
            tp,
            sl,
        ):

            continue

        consider_candidate(
            asset,
            "Direction Check",
            pattern15["score"],
            pattern15["pattern"],
        )

        # ----------------------------------------------------
        # SIGNAL KEY
        # ----------------------------------------------------

        signal_key = (
            f"{asset}|"
            f"{direction}|"
            f"{pattern15['pattern']}|"
            f"{pattern15['pattern_time']}|"
            f"{confirmation15['timestamp']}"
        )

        if has_signal(signal_key):

            continue

        if has_open_same_direction(
            asset,
            direction,
        ):

            continue

        consider_candidate(
            asset,
            "DB Check",
            pattern15["score"],
            pattern15["pattern"],
        )

        trade = {
            "symbol": symbol,
            "asset": asset,
            "direction": direction,
            "pattern": pattern15["pattern"],
            "signal_key": signal_key,
            "pattern_time": pattern15["pattern_time"],
            "breakout_time": breakout15["timestamp"],
            "retest_time": retest15["timestamp"],
            "confirmation_time": confirmation15["timestamp"],
            "entry_time": confirmation15["timestamp"],
            "entry_price": entry,
            "sl_price": sl,
            "tp_price": tp,
            "current_price": entry,
            "pattern1h": pattern1h,
            "pattern15m": pattern15,
            "breakout1h": breakout1h,
            "breakout15m": breakout15,
            "retest15m": retest15,
            "confirmation15m": confirmation15,
            "df15": df15,
        }

        STATS["candidates"] += 1

        consider_candidate(
            asset,
            "Signal Ready",
            pattern15["score"],
            pattern15["pattern"],
        )

        return trade

    return None


# ============================================================
# CHART
# ============================================================

def make_chart(trade):

    df = trade.get("df15")

    if df is None or df.empty:
        return None

    chart_df = df.tail(
        CHART_CANDLES
    ).copy()

    if chart_df.empty:
        return None

    plt.figure(
        figsize=(14, 7)
    )

    for i, row in chart_df.iterrows():

        x = i

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

        plt.vlines(
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
                close * 0.00005,
                1e-12,
            )

        plt.bar(
            x,
            height,
            bottom=bottom,
            width=0.65,
        )

    # --------------------------------------------------------
    # Important levels
    # --------------------------------------------------------

    pattern15 = trade[
        "pattern15m"
    ]

    plt.axhline(
        pattern15["neckline"],
        linestyle="--",
        linewidth=1,
        label="15M Neckline",
    )

    plt.axhline(
        trade["entry_price"],
        linestyle="-",
        linewidth=1,
        label="ENTRY",
    )

    plt.axhline(
        trade["tp_price"],
        linestyle="--",
        linewidth=1,
        label="TP",
    )

    plt.axhline(
        trade["sl_price"],
        linestyle="--",
        linewidth=1,
        label="SL",
    )

    # --------------------------------------------------------
    # Pattern points
    # --------------------------------------------------------

    start_index = chart_df.index[0]

    def mark(idx, label):

        if idx not in chart_df.index:
            return

        local_x = (
            idx - start_index
        )

        row = df.iloc[idx]

        if label in ("P1", "P2", "H", "R"):
            y = float(
                row["high"]
            )
        else:
            y = float(
                row["low"]
            )

        plt.annotate(
            label,
            (
                local_x,
                y,
            ),
            xytext=(
                0,
                10 if label in ("P1", "P2", "H", "R") else -18,
            ),
            textcoords="offset points",
            ha="center",
            fontsize=9,
            fontweight="bold",
        )

    p = pattern15

    if p["pattern"] == "Double Top":

        mark(
            p["left_idx"],
            "P1",
        )

        mark(
            p["right_idx"],
            "P2",
        )

        mark(
            p["neckline_idx"],
            "N",
        )

    elif p["pattern"] == "Double Bottom":

        mark(
            p["left_idx"],
            "P1",
        )

        mark(
            p["right_idx"],
            "P2",
        )

        mark(
            p["neckline_idx"],
            "N",
        )

    elif p["pattern"] == "Head & Shoulders":

        mark(
            p["left_idx"],
            "LS",
        )

        mark(
            p["head_idx"],
            "H",
        )

        mark(
            p["right_idx"],
            "RS",
        )

    elif (
        p["pattern"]
        == "Inverse Head & Shoulders"
    ):

        mark(
            p["left_idx"],
            "LS",
        )

        mark(
            p["head_idx"],
            "H",
        )

        mark(
            p["right_idx"],
            "RS",
        )

    plt.title(
        f"{trade['asset']} "
        f"{trade['direction']} | "
        f"15M {trade['pattern15m']}"
    )

    plt.xlabel(
        "15M Closed Candles"
    )

    plt.ylabel(
        "Price"
    )

    plt.grid(
        alpha=0.2
    )

    plt.legend()

    plt.tight_layout()

    filename = (
        f"signal_"
        f"{trade['asset']}_"
        f"{int(time.time())}.png"
    )

    plt.savefig(
        filename,
        dpi=150,
    )

    plt.close()

    return filename


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def build_signal_message(trade):

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

⏱ <b>Entry Time:</b> {format_time(trade['entry_time'])}

⚙️ <b>Mode:</b> PAPER ONLY
"""


# ============================================================
# OPEN TRADES REPORT
# ============================================================

def build_open_trades_message():

    rows = get_open_trades()

    if not rows:

        return "🟢 <b>OPEN TRADES: 0</b>"

    lines = [
        f"🟢 <b>OPEN TRADES: {len(rows)}</b>"
    ]

    prices = get_all_current_prices()

    for row in rows:

        symbol = str(
            row["symbol"]
        ).upper()

        current = prices.get(
            symbol,
            row["current_price"],
        )

        entry = float(
            row["entry_price"]
        )

        direction = row[
            "direction"
        ]

        if direction == "LONG":

            pnl_pct = (
                (
                    current
                    - entry
                )
                / entry
            ) * 100

            emoji = "🟢"

        else:

            pnl_pct = (
                (
                    entry
                    - current
                )
                / entry
            ) * 100

            emoji = "🔴"

        lines.append(
            f"""
{emoji} <b>{row['asset']} {direction}</b>
📌 Pattern: {row['pattern']}
💰 Entry: {entry:.10g}
💵 Current: {current:.10g} ({pnl_pct:+.2f}%)
🛑 SL: {float(row['sl_price']):.10g}
🎯 TP: {float(row['tp_price']):.10g}
⏱ Duration: {duration_text(row['entry_time'])}
"""
        )

    return "\n".join(lines)


# ============================================================
# PERFORMANCE
# ============================================================

def get_performance():

    conn = db_connect()

    try:

        total = conn.execute(
            """
            SELECT COUNT(*)
            FROM trades
            """
        ).fetchone()[0]

        closed = conn.execute(
            """
            SELECT COUNT(*)
            FROM trades
            WHERE status = 'CLOSED'
            """
        ).fetchone()[0]

        open_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM trades
            WHERE status = 'OPEN'
            """
        ).fetchone()[0]

        wins = conn.execute(
            """
            SELECT COUNT(*)
            FROM trades
            WHERE status = 'CLOSED'
              AND exit_reason = 'TP'
            """
        ).fetchone()[0]

        losses = conn.execute(
            """
            SELECT COUNT(*)
            FROM trades
            WHERE status = 'CLOSED'
              AND exit_reason = 'SL'
            """
        ).fetchone()[0]

        pnl = conn.execute(
            """
            SELECT
                COALESCE(
                    SUM(
                        CASE
                            WHEN direction = 'LONG'
                                THEN
                                    (
                                        exit_price
                                        - entry_price
                                    )
                                    / entry_price
                                    * 100
                            WHEN direction = 'SHORT'
                                THEN
                                    (
                                        entry_price
                                        - exit_price
                                    )
                                    / entry_price
                                    * 100
                            ELSE 0
                        END
                    ),
                    0
                )
            FROM trades
            WHERE status = 'CLOSED'
            """
        ).fetchone()[0]

        win_rate = (
            wins / closed * 100
            if closed
            else 0
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

    finally:
        conn.close()


def build_performance_message():

    p = get_performance()

    return f"""
📈 <b>PERFORMANCE</b>

Total: {p['total']}
Closed: {p['closed']}
Open: {p['open']}

Wins: {p['wins']}
Losses: {p['losses']}

Win Rate: {p['win_rate']:.2f}%
Net PnL: {p['pnl']:+.2f}%
"""


# ============================================================
# SCAN SUMMARY
# ============================================================

def build_scan_summary():

    scanned = STATS[
        "assets_scanned"
    ]

    total_assets = len(ASSETS)

    lines = [
        "📊 <b>KRAKEN PATTERN SCANNER</b>",
        "",
        f"<b>Version:</b> {VERSION}",
        "<b>Mode:</b> PAPER ONLY",
        f"<b>Time:</b> {format_time(now_ts())}",
        "",
        "🔎 <b>SCAN SUMMARY</b>",
        "",
        f"Assets Scanned: {scanned}/{total_assets}",
        f"1H Patterns: {STATS['patterns_1h']}",
        f"1H Breakouts: {STATS['breakouts_1h']}",
        f"15M Patterns: {STATS['patterns_15m']}",
        f"15M Breakouts: {STATS['breakouts_15m']}",
        f"15M Retests: {STATS['retests_15m']}",
        f"15M Confirmations: {STATS['confirmations_15m']}",
        f"New Signals: {STATS['signals_sent']}",
        f"Errors: {STATS['errors']}",
        "",
    ]

    # --------------------------------------------------------
    # BEST CANDIDATE
    # --------------------------------------------------------

    lines.append(
        "🏆 <b>BEST CANDIDATE</b>"
    )

    if BEST_CANDIDATE:

        lines.append(
            f"Asset: {BEST_CANDIDATE['asset']}"
        )

        lines.append(
            f"Stage: {BEST_CANDIDATE['stage']}"
        )

        lines.append(
            f"Score: {BEST_CANDIDATE['score']:.2f}"
        )

        if BEST_CANDIDATE["reason"]:

            lines.append(
                f"Info: {BEST_CANDIDATE['reason']}"
            )

    else:

        if scanned < total_assets:

            lines.append(
                "Status: Scan incomplete"
            )

            lines.append(
                "Reason: Some assets failed during data retrieval."
            )

        else:

            lines.append(
                "Status: No viable candidate"
            )

            lines.append(
                "Reason: No valid pattern reached the required stage."
            )

    lines.append("")

    lines.append(
        build_open_trades_message()
    )

    lines.append(
        build_performance_message()
    )

    return "\n".join(lines)


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
                status = 'CLOSED',
                exit_time = ?,
                exit_price = ?,
                exit_reason = ?,
                current_price = ?,
                updated_at = ?
            WHERE id = ?
              AND status = 'OPEN'
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
        # MAX HOLD
        # ----------------------------------------------------

        if (
            row["entry_time"]
            and (
                now_ts()
                - int(row["entry_time"])
            )
            >= MAX_HOLD_HOURS * 3600
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

        if reason:

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

    STATS["scans"] += 1

    candidates = []

    for asset in ASSETS:

        symbol = CONTRACTS[
            asset
        ]

        try:

            trade = analyze_asset(
                asset,
                symbol,
            )

            if trade:
                candidates.append(
                    trade
                )

        except Exception as exc:

            STATS[
                "errors"
            ] += 1

            print(
                f"[ASSET ERROR] "
                f"{asset}: {exc}"
            )

            traceback.print_exc()

    # --------------------------------------------------------
    # BEST SIGNAL
    # --------------------------------------------------------

    if not candidates:

        message = (
            build_scan_summary()
        )

        send_telegram(
            message
        )

        return None

    # Strongest candidate first
    candidates.sort(
        key=lambda x: (
            x["pattern15m"]["score"],
            x["pattern1h"]["score"],
            x["confirmation15m"]["timestamp"],
        ),
        reverse=True,
    )

    inserted = 0

    for trade in candidates:

        if inserted >= MAX_SIGNALS_PER_SCAN:
            break

        success = insert_trade(
            trade
        )

        if not success:
            continue

        STATS[
            "unique_entries"
        ] += 1

        STATS[
            "signals_sent"
        ] += 1

        inserted += 1

        message = build_signal_message(
            trade
        )

        send_telegram(
            message
        )

        chart_path = make_chart(
            trade
        )

        if chart_path:

            send_telegram_photo(
                chart_path,
                (
                    f"📊 "
                    f"{trade['asset']} "
                    f"{trade['direction']} | "
                    f"15M {trade['pattern15m']['pattern']}"
                ),
            )

            try:
                os.remove(
                    chart_path
                )
            except Exception:
                pass

    # --------------------------------------------------------
    # If DB rejected all candidates
    # --------------------------------------------------------

    if inserted == 0:

        send_telegram(
            build_scan_summary()
        )

    return inserted


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)

    print(
        f"KRAKEN FUTURES PATTERN LIVE SCANNER "
        f"VERSION {VERSION}"
    )

    print(
        "MODE: PAPER ONLY"
    )

    print(
        f"REAL_TRADING: {REAL_TRADING}"
    )

    print("=" * 70)

    init_db()

    reset_stats()

    reset_best_candidate()

    # --------------------------------------------------------
    # Existing open trades first
    # --------------------------------------------------------

    reconcile_open_trades()

    # --------------------------------------------------------
    # New scan
    # --------------------------------------------------------

    scan()

    # --------------------------------------------------------
    # Console summary
    # --------------------------------------------------------

    print()
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
        f"{STATS['signals_sent']}"
    )

    print(
        f"Errors: "
        f"{STATS['errors']}"
    )

    print("=" * 70)


if __name__ == "__main__":
    main()
