# ============================================================
# KRAKEN FUTURES PATTERN LIVE SIGNAL SCANNER
# VERSION 6.2.0
# ============================================================
#
# PAPER ONLY
# REAL_TRADING = False
#
# STRATEGY
#
# 1H:
#   Pattern Detection
#       ↓
#   1H Pattern Breakout
#       ↓
#
# 15M:
#   Same Pattern Detection
#       ↓
#   15M Pattern Breakout
#       ↓
#   15M Retest
#       ↓
#   15M Confirmation
#       ↓
#   ENTRY
#
# TP / SL:
#   Natural target / invalidation of 15M pattern
#
# NO FIXED 1% TP
# NO FIXED 1% SL
# NO RR FILTER
#
# Existing DB preserved:
#   kraken_pattern_live_v52.db
#
# DIAGNOSTICS:
#   - Full scan summary
#   - Best candidate
#   - Exact rejection stage
#   - Exact rejection reason
#   - GitHub Actions diagnostics
#   - Telegram no-signal diagnostics
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

VERSION = "6.2.0"

REAL_TRADING = False

DB_FILE = "kraken_pattern_live_v52.db"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

KRAKEN_INSTRUMENTS_URL = (
    "https://futures.kraken.com/derivatives/api/v3/instruments"
)

KRAKEN_TICKERS_URL = (
    "https://futures.kraken.com/derivatives/api/v3/tickers"
)

KRAKEN_CHART_URL = (
    "https://futures.kraken.com/api/charts/v1/trade"
)

HTTP_RETRIES = 3
HTTP_TIMEOUT = 20

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


CONTRACT_MAP = {
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
# GLOBALS
# ============================================================

LIVE_PRICES = {}

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
# BEST CANDIDATE DIAGNOSTIC
# ============================================================

STAGE_RANK = {
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


BEST_CANDIDATE = {
    "rank": -1,
    "asset": None,
    "direction": None,
    "pattern": None,
    "score": 0.0,
    "stage": None,
    "rejected_at": None,
    "reason": None,
}


def reset_best_candidate():
    BEST_CANDIDATE.update(
        {
            "rank": -1,
            "asset": None,
            "direction": None,
            "pattern": None,
            "score": 0.0,
            "stage": None,
            "rejected_at": None,
            "reason": None,
        }
    )


def update_best_candidate(
    asset,
    direction,
    pattern,
    score,
    stage,
    rejected_at,
    reason,
):
    rank = STAGE_RANK.get(
        stage,
        0,
    )

    try:
        score_value = float(score or 0)
    except Exception:
        score_value = 0.0

    current_rank = BEST_CANDIDATE["rank"]
    current_score = BEST_CANDIDATE["score"]

    should_replace = (
        rank > current_rank
        or (
            rank == current_rank
            and score_value > current_score
        )
    )

    if not should_replace:
        return

    BEST_CANDIDATE.update(
        {
            "rank": rank,
            "asset": asset,
            "direction": direction,
            "pattern": pattern,
            "score": score_value,
            "stage": stage,
            "rejected_at": rejected_at,
            "reason": reason,
        }
    )


# ============================================================
# TIME
# ============================================================

TEHRAN_TZ = timezone(
    timedelta(hours=3, minutes=30)
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

            if isinstance(
                value,
                str,
            ):
                return datetime.fromisoformat(
                    value.replace(
                        "Z",
                        "+00:00",
                    )
                ).timestamp()

            return float(value)

        start_ts = to_timestamp(
            start_value
        )

        if end_value is None:
            end_ts = now_ts()
        else:
            end_ts = to_timestamp(
                end_value
            )

        seconds = max(
            0,
            int(
                end_ts - start_ts
            ),
        )

        minutes = seconds // 60
        hours = minutes // 60
        minutes = minutes % 60

        if hours > 0:
            return (
                f"{hours}h "
                f"{minutes}m"
            )

        return f"{minutes}m"

    except Exception:
        return "-"


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

            if (
                attempt
                < HTTP_RETRIES - 1
            ):
                time.sleep(
                    1.5
                    * (attempt + 1)
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

            files = {
                "photo": photo,
            }

            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
                "parse_mode": "HTML",
            }

            response = SESSION.post(
                url,
                data=data,
                files=files,
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

    for (
        column_name,
        column_type,
    ) in required_columns.items():

        if column_name not in existing_columns:

            try:

                cur.execute(
                    "ALTER TABLE trades ADD COLUMN "
                    f"{column_name} {column_type}"
                )

                print(
                    "Added DB column:",
                    column_name,
                )

            except Exception as exc:

                print(
                    "DB migration error:",
                    column_name,
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

    except Exception as exc:

        print(
            "Unique index warning:",
            exc,
        )

    conn.commit()
    conn.close()


# ============================================================
# META
# ============================================================

def get_meta(key):

    conn = db_connect()

    row = conn.execute(
        """
        SELECT value
        FROM scanner_meta
        WHERE key=?
        """,
        (key,),
    ).fetchone()

    conn.close()

    if row:
        return row["value"]

    return None


def set_meta(
    key,
    value,
):

    conn = db_connect()

    conn.execute(
        """
        INSERT INTO scanner_meta(key, value)
        VALUES (?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value=excluded.value
        """,
        (
            key,
            str(value),
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# KRAKEN
# ============================================================

def get_contract(asset):

    return CONTRACT_MAP.get(
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

        price = (
            ticker.get("last")
            or ticker.get("lastPrice")
            or ticker.get("markPrice")
        )

        if price is None:
            continue

        try:

            prices[symbol] = float(
                price
            )

        except Exception:
            continue

    LIVE_PRICES = prices


def get_live_price(asset):

    contract = get_contract(
        asset
    )

    price = LIVE_PRICES.get(
        contract
    )

    if price is None:
        return None

    return float(price)


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
                    or candle.get("timestamp")
                    or candle.get("t")
                )

                open_price = (
                    candle.get("open")
                    or candle.get("o")
                )

                high_price = (
                    candle.get("high")
                    or candle.get("h")
                )

                low_price = (
                    candle.get("low")
                    or candle.get("l")
                )

                close_price = (
                    candle.get("close")
                    or candle.get("c")
                )

            else:

                if len(candle) < 5:
                    continue

                timestamp = candle[0]
                open_price = candle[1]
                high_price = candle[2]
                low_price = candle[3]
                close_price = candle[4]

            rows.append(
                {
                    "time": int(
                        float(timestamp)
                    ),
                    "open": float(
                        open_price
                    ),
                    "high": float(
                        high_price
                    ),
                    "low": float(
                        low_price
                    ),
                    "close": float(
                        close_price
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

    df = df.drop_duplicates(
        subset=["time"]
    )

    df = df.sort_values(
        "time"
    )

    interval_seconds = {
        "1h": 3600,
        "15m": 900,
        "5m": 300,
    }.get(
        interval,
        900,
    )

    current_time = now_ts()

    df = df[
        (
            df["time"]
            + interval_seconds
        )
        <= current_time
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
    left=2,
    right=2,
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
        index + 1:index + right + 1
    ]["high"]

    return (
        value >= left_values.max()
        and value >= right_values.max()
    )


def is_local_low(
    df,
    index,
    left=2,
    right=2,
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
        index + 1:index + right + 1
    ]["low"]

    return (
        value <= left_values.min()
        and value <= right_values.min()
    )


def pivot_highs(df):

    result = []

    for i in range(
        len(df)
    ):

        if is_local_high(
            df,
            i,
        ):
            result.append(i)

    return result


def pivot_lows(df):

    result = []

    for i in range(
        len(df)
    ):

        if is_local_low(
            df,
            i,
        ):
            result.append(i)

    return result


# ============================================================
# HELPERS
# ============================================================

def pct_diff(
    a,
    b,
):

    if a == 0:
        return 999.0

    return abs(
        a - b
    ) / abs(a)


def make_pattern(
    name,
    direction,
    points,
    neckline,
    target,
    stop,
    pattern_time,
    quality,
):

    return {
        "pattern": name,
        "direction": direction,
        "points": points,
        "neckline": float(
            neckline
        ),
        "target": float(
            target
        ),
        "stop": float(
            stop
        ),
        "pattern_time": int(
            pattern_time
        ),
        "quality": float(
            quality
        ),
    }


# ============================================================
# DOUBLE TOP
# ============================================================

def detect_double_top(df):

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

            if (
                right - left
                < MIN_PATTERN_SEPARATION_BARS
            ):
                continue

            left_high = float(
                df.iloc[left]["high"]
            )

            right_high = float(
                df.iloc[right]["high"]
            )

            if (
                pct_diff(
                    left_high,
                    right_high,
                )
                > DOUBLE_LEVEL_TOLERANCE
            ):
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
                df.iloc[x]["low"],
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

            if (
                depth
                < MIN_PATTERN_DEPTH_PCT
            ):
                continue

            neckline = valley_price

            height = (
                peak - neckline
            )

            target = (
                neckline - height
            )

            stop = (
                peak
                * (1 + INVALIDATION_BUFFER)
            )

            quality = (
                depth * 100
                + (
                    1
                    - pct_diff(
                        left_high,
                        right_high,
                    )
                ) * 2
            )

            patterns.append(
                make_pattern(
                    "Double Top",
                    "SHORT",
                    {
                        "left": left,
                        "valley": valley,
                        "right": right,
                    },
                    neckline,
                    target,
                    stop,
                    int(
                        df.iloc[right]["time"]
                    ),
                    quality,
                )
            )

    return patterns


# ============================================================
# DOUBLE BOTTOM
# ============================================================

def detect_double_bottom(df):

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

            if (
                right - left
                < MIN_PATTERN_SEPARATION_BARS
            ):
                continue

            left_low = float(
                df.iloc[left]["low"]
            )

            right_low = float(
                df.iloc[right]["low"]
            )

            if (
                pct_diff(
                    left_low,
                    right_low,
                )
                > DOUBLE_LEVEL_TOLERANCE
            ):
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
                df.iloc[x]["high"],
            )

            peak_price = float(
                df.iloc[peak]["high"]
            )

            bottom = min(
                left_low,
                right_low,
            )

            depth = (
                peak_price - bottom
            ) / peak_price

            if (
                depth
                < MIN_PATTERN_DEPTH_PCT
            ):
                continue

            neckline = peak_price

            height = (
                neckline - bottom
            )

            target = (
                neckline + height
            )

            stop = (
                bottom
                * (1 - INVALIDATION_BUFFER)
            )

            quality = (
                depth * 100
                + (
                    1
                    - pct_diff(
                        left_low,
                        right_low,
                    )
                ) * 2
            )

            patterns.append(
                make_pattern(
                    "Double Bottom",
                    "LONG",
                    {
                        "left": left,
                        "valley": peak,
                        "right": right,
                    },
                    neckline,
                    target,
                    stop,
                    int(
                        df.iloc[right]["time"]
                    ),
                    quality,
                )
            )

    return patterns


# ============================================================
# HEAD AND SHOULDERS
# ============================================================

def detect_head_shoulders(df):

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

            if (
                head - left
                < MIN_PATTERN_SEPARATION_BARS
            ):
                continue

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

                left_price = float(
                    df.iloc[left]["high"]
                )

                head_price = float(
                    df.iloc[head]["high"]
                )

                right_price = float(
                    df.iloc[right]["high"]
                )

                shoulder_difference = pct_diff(
                    left_price,
                    right_price,
                )

                if (
                    shoulder_difference
                    > HS_SHOULDER_TOLERANCE
                ):
                    continue

                head_advantage = (
                    head_price
                    - max(
                        left_price,
                        right_price,
                    )
                ) / max(
                    left_price,
                    right_price,
                )

                if (
                    head_advantage
                    < HS_MIN_HEAD_ADVANTAGE_PCT
                ):
                    continue

                first_lows = [
                    x
                    for x in lows
                    if left < x < head
                ]

                second_lows = [
                    x
                    for x in lows
                    if head < x < right
                ]

                if (
                    not first_lows
                    or not second_lows
                ):
                    continue

                neckline_left = min(
                    first_lows,
                    key=lambda x:
                    df.iloc[x]["low"],
                )

                neckline_right = min(
                    second_lows,
                    key=lambda x:
                    df.iloc[x]["low"],
                )

                nl1 = float(
                    df.iloc[
                        neckline_left
                    ]["low"]
                )

                nl2 = float(
                    df.iloc[
                        neckline_right
                    ]["low"]
                )

                neckline = (
                    nl1 + nl2
                ) / 2

                depth = (
                    head_price
                    - neckline
                ) / head_price

                if (
                    depth
                    < MIN_HS_DEPTH_PCT
                ):
                    continue

                height = (
                    head_price
                    - neckline
                )

                target = (
                    neckline - height
                )

                stop = (
                    head_price
                    * (1 + INVALIDATION_BUFFER)
                )

                quality = (
                    depth * 100
                    + (
                        1
                        - shoulder_difference
                    ) * 2
                )

                patterns.append(
                    make_pattern(
                        "Head & Shoulders",
                        "SHORT",
                        {
                            "left_shoulder": left,
                            "head": head,
                            "right_shoulder": right,
                            "neckline_left":
                                neckline_left,
                            "neckline_right":
                                neckline_right,
                        },
                        neckline,
                        target,
                        stop,
                        int(
                            df.iloc[right]["time"]
                        ),
                        quality,
                    )
                )

    return patterns


# ============================================================
# INVERSE HEAD AND SHOULDERS
# ============================================================

def detect_inverse_head_shoulders(df):

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

            if (
                head - left
                < MIN_PATTERN_SEPARATION_BARS
            ):
                continue

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

                left_price = float(
                    df.iloc[left]["low"]
                )

                head_price = float(
                    df.iloc[head]["low"]
                )

                right_price = float(
                    df.iloc[right]["low"]
                )

                shoulder_difference = pct_diff(
                    left_price,
                    right_price,
                )

                if (
                    shoulder_difference
                    > HS_SHOULDER_TOLERANCE
                ):
                    continue

                head_advantage = (
                    min(
                        left_price,
                        right_price,
                    )
                    - head_price
                ) / min(
                    left_price,
                    right_price,
                )

                if (
                    head_advantage
                    < HS_MIN_HEAD_ADVANTAGE_PCT
                ):
                    continue

                first_highs = [
                    x
                    for x in highs
                    if left < x < head
                ]

                second_highs = [
                    x
                    for x in highs
                    if head < x < right
                ]

                if (
                    not first_highs
                    or not second_highs
                ):
                    continue

                neckline_left = max(
                    first_highs,
                    key=lambda x:
                    df.iloc[x]["high"],
                )

                neckline_right = max(
                    second_highs,
                    key=lambda x:
                    df.iloc[x]["high"],
                )

                nl1 = float(
                    df.iloc[
                        neckline_left
                    ]["high"]
                )

                nl2 = float(
                    df.iloc[
                        neckline_right
                    ]["high"]
                )

                neckline = (
                    nl1 + nl2
                ) / 2

                depth = (
                    neckline
                    - head_price
                ) / neckline

                if (
                    depth
                    < MIN_HS_DEPTH_PCT
                ):
                    continue

                height = (
                    neckline
                    - head_price
                )

                target = (
                    neckline + height
                )

                stop = (
                    head_price
                    * (1 - INVALIDATION_BUFFER)
                )

                quality = (
                    depth * 100
                    + (
                        1
                        - shoulder_difference
                    ) * 2
                )

                patterns.append(
                    make_pattern(
                        "Inverse Head & Shoulders",
                        "LONG",
                        {
                            "left_shoulder": left,
                            "head": head,
                            "right_shoulder": right,
                            "neckline_left":
                                neckline_left,
                            "neckline_right":
                                neckline_right,
                        },
                        neckline,
                        target,
                        stop,
                        int(
                            df.iloc[right]["time"]
                        ),
                        quality,
                    )
                )

    return patterns


# ============================================================
# ALL PATTERNS
# ============================================================

def detect_patterns(df):

    if df is None:
        return []

    if len(df) < 30:
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
        key=lambda pattern: (
            pattern["pattern_time"],
            pattern["quality"],
        ),
        reverse=True,
    )

    return patterns


# ============================================================
# 1H BREAKOUT
# ============================================================

def find_1h_breakout(
    df,
    pattern,
):

    eligible = df[
        df["time"]
        > pattern["pattern_time"]
    ].head(
        BREAKOUT_LOOKAHEAD_1H
    )

    if eligible.empty:
        return None

    neckline = pattern["neckline"]

    if pattern["direction"] == "LONG":

        for _, row in eligible.iterrows():

            if row["close"] > neckline:

                return {
                    "time": int(
                        row["time"]
                    ),
                    "price": float(
                        row["close"]
                    ),
                    "index": int(
                        row.name
                    ),
                }

    else:

        for _, row in eligible.iterrows():

            if row["close"] < neckline:

                return {
                    "time": int(
                        row["time"]
                    ),
                    "price": float(
                        row["close"]
                    ),
                    "index": int(
                        row.name
                    ),
                }

    return None


# ============================================================
# FIND SAME 15M PATTERN
# ============================================================

def find_15m_same_pattern(
    df,
    pattern_name,
    direction,
    after_time,
):

    patterns = detect_patterns(
        df
    )

    candidates = []

    for pattern in patterns:

        if (
            pattern["pattern"]
            != pattern_name
        ):
            continue

        if (
            pattern["direction"]
            != direction
        ):
            continue

        if (
            pattern["pattern_time"]
            <= after_time
        ):
            continue

        age = (
            now_ts()
            - pattern["pattern_time"]
        )

        if (
            age
            > RECENT_PATTERN_HOURS_15M
            * 3600
        ):
            continue

        candidates.append(
            pattern
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda pattern: (
            pattern["pattern_time"],
            pattern["quality"],
        ),
        reverse=True,
    )

    return candidates[0]


# ============================================================
# 15M BREAKOUT
# ============================================================

def find_15m_breakout(
    df,
    pattern,
):

    eligible = df[
        df["time"]
        > pattern["pattern_time"]
    ].head(
        BREAKOUT_LOOKAHEAD_15M
    )

    if eligible.empty:
        return None

    neckline = pattern["neckline"]

    if pattern["direction"] == "LONG":

        for _, row in eligible.iterrows():

            if row["close"] > neckline:

                return {
                    "time": int(
                        row["time"]
                    ),
                    "price": float(
                        row["close"]
                    ),
                    "index": int(
                        row.name
                    ),
                }

    else:

        for _, row in eligible.iterrows():

            if row["close"] < neckline:

                return {
                    "time": int(
                        row["time"]
                    ),
                    "price": float(
                        row["close"]
                    ),
                    "index": int(
                        row.name
                    ),
                }

    return None


# ============================================================
# 15M RETEST
# ============================================================

def find_15m_retest(
    df,
    pattern,
    breakout,
):

    eligible = df[
        df["time"]
        > breakout["time"]
    ].head(
        RETEST_LOOKAHEAD_15M
    )

    if eligible.empty:
        return None

    neckline = pattern["neckline"]

    for _, row in eligible.iterrows():

        touched = (
            row["low"]
            <= neckline
            <= row["high"]
        )

        if pattern["direction"] == "LONG":

            closed_on_side = (
                row["close"]
                >= neckline
            )

        else:

            closed_on_side = (
                row["close"]
                <= neckline
            )

        if (
            touched
            and closed_on_side
        ):

            return {
                "time": int(
                    row["time"]
                ),
                "price": float(
                    row["close"]
                ),
                "index": int(
                    row.name
                ),
            }

    return None


# ============================================================
# 15M CONFIRMATION
# ============================================================

def find_15m_confirmation(
    df,
    pattern,
    retest,
):

    eligible = df[
        df["time"]
        > retest["time"]
    ].head(
        CONFIRMATION_LOOKAHEAD_15M
    )

    if eligible.empty:
        return None

    neckline = pattern["neckline"]

    if pattern["direction"] == "LONG":

        for _, row in eligible.iterrows():

            if (
                row["close"] > neckline
                and row["close"] > row["open"]
            ):

                return {
                    "time": int(
                        row["time"]
                    ),
                    "price": float(
                        row["close"]
                    ),
                    "index": int(
                        row.name
                    ),
                }

    else:

        for _, row in eligible.iterrows():

            if (
                row["close"] < neckline
                and row["close"] < row["open"]
            ):

                return {
                    "time": int(
                        row["time"]
                    ),
                    "price": float(
                        row["close"]
                    ),
                    "index": int(
                        row.name
                    ),
                }

    return None


# ============================================================
# DB CHECKS
# ============================================================

def signal_exists(
    signal_key
):

    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
        FROM trades
        WHERE signal_key=?
        LIMIT 1
        """,
        (signal_key,),
    ).fetchone()

    conn.close()

    return row is not None


def open_trade_exists(
    asset,
    direction,
):

    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
        FROM trades
        WHERE asset=?
          AND direction=?
          AND status='OPEN'
        LIMIT 1
        """,
        (
            asset,
            direction,
        ),
    ).fetchone()

    conn.close()

    return row is not None


# ============================================================
# SIGNAL KEY
# ============================================================

def build_signal_key(
    asset,
    pattern_1h,
    breakout_1h,
    pattern_15m,
    breakout_15m,
    retest_15m,
    confirmation_15m,
):

    return (
        f"{asset}|"
        f"{pattern_1h['pattern']}|"
        f"{pattern_1h['pattern_time']}|"
        f"{breakout_1h['time']}|"
        f"{pattern_15m['pattern_time']}|"
        f"{breakout_15m['time']}|"
        f"{retest_15m['time']}|"
        f"{confirmation_15m['time']}"
    )


# ============================================================
# BUILD CANDIDATE
# ============================================================

def build_candidate(
    asset
):

    try:

        df1h = get_candles(
            asset,
            "1h",
            PATTERN_LOOKBACK_1H,
        )

        if len(df1h) < 40:
            update_best_candidate(
                asset,
                None,
                None,
                0,
                "1H Pattern",
                "1H Data",
                "Not enough closed 1H candles.",
            )
            return None

        df15 = get_candles(
            asset,
            "15m",
            PATTERN_LOOKBACK_15M,
        )

        if len(df15) < 50:
            update_best_candidate(
                asset,
                None,
                None,
                0,
                "1H Pattern",
                "15M Data",
                "Not enough closed 15M candles.",
            )
            return None

        patterns_1h = detect_patterns(
            df1h
        )

        current_time = now_ts()

        patterns_1h = [
            pattern
            for pattern in patterns_1h
            if (
                current_time
                - pattern["pattern_time"]
                <= RECENT_PATTERN_HOURS_1H
                * 3600
            )
        ]

        if not patterns_1h:

            update_best_candidate(
                asset,
                None,
                None,
                0,
                "1H Pattern",
                "1H Pattern",
                "No valid recent 1H pattern.",
            )

            return None

        STATS["patterns_1h"] += len(
            patterns_1h
        )

        patterns_1h.sort(
            key=lambda pattern: (
                pattern["pattern_time"],
                pattern["quality"],
            ),
            reverse=True,
        )

        best_local_failure = None

        for pattern_1h in patterns_1h:

            direction = pattern_1h[
                "direction"
            ]

            pattern_name = pattern_1h[
                "pattern"
            ]

            quality_1h = pattern_1h[
                "quality"
            ]

            update_best_candidate(
                asset,
                direction,
                pattern_name,
                quality_1h,
                "1H Pattern",
                "1H Breakout",
                "1H pattern detected, but breakout has not been confirmed.",
            )

            breakout_1h = find_1h_breakout(
                df1h,
                pattern_1h,
            )

            if breakout_1h is None:
                continue

            STATS[
                "breakouts_1h"
            ] += 1

            update_best_candidate(
                asset,
                direction,
                pattern_name,
                quality_1h,
                "1H Breakout",
                "15M Pattern",
                "1H breakout confirmed, but no matching 15M pattern was found.",
            )

            pattern_15m = find_15m_same_pattern(
                df15,
                pattern_name,
                direction,
                breakout_1h["time"],
            )

            if pattern_15m is None:
                continue

            STATS[
                "patterns_15m"
            ] += 1

            combined_score = (
                quality_1h
                + pattern_15m["quality"]
            )

            update_best_candidate(
                asset,
                direction,
                pattern_name,
                combined_score,
                "15M Pattern",
                "15M Breakout",
                "Matching 15M pattern found, but no closed 15M breakout was confirmed.",
            )

            breakout_15m = find_15m_breakout(
                df15,
                pattern_15m,
            )

            if breakout_15m is None:
                continue

            STATS[
                "breakouts_15m"
            ] += 1

            update_best_candidate(
                asset,
                direction,
                pattern_name,
                combined_score,
                "15M Breakout",
                "15M Retest",
                "15M breakout confirmed, but price did not complete a valid retest.",
            )

            retest_15m = find_15m_retest(
                df15,
                pattern_15m,
                breakout_15m,
            )

            if retest_15m is None:
                continue

            STATS[
                "retests_15m"
            ] += 1

            update_best_candidate(
                asset,
                direction,
                pattern_name,
                combined_score,
                "15M Retest",
                "15M Confirmation",
                "15M retest confirmed, but no valid confirmation candle appeared.",
            )

            confirmation_15m = find_15m_confirmation(
                df15,
                pattern_15m,
                retest_15m,
            )

            if confirmation_15m is None:
                continue

            STATS[
                "confirmations_15m"
            ] += 1

            update_best_candidate(
                asset,
                direction,
                pattern_name,
                combined_score,
                "15M Confirmation",
                "Direction Check",
                "15M confirmation exists, but natural TP/SL is invalid relative to entry.",
            )

            entry_price = float(
                confirmation_15m["price"]
            )

            tp_price = float(
                pattern_15m["target"]
            )

            sl_price = float(
                pattern_15m["stop"]
            )

            # ------------------------------------------------
            # Direction sanity check only.
            # NO RR FILTER.
            # ------------------------------------------------

            if direction == "LONG":

                valid_levels = (
                    sl_price < entry_price
                    and tp_price > entry_price
                )

            else:

                valid_levels = (
                    sl_price > entry_price
                    and tp_price < entry_price
                )

            if not valid_levels:
                continue

            signal_key = build_signal_key(
                asset,
                pattern_1h,
                breakout_1h,
                pattern_15m,
                breakout_15m,
                retest_15m,
                confirmation_15m,
            )

            if signal_exists(
                signal_key
            ):

                update_best_candidate(
                    asset,
                    direction,
                    pattern_name,
                    combined_score,
                    "DB Check",
                    "DB Check",
                    "Signal already exists in the database.",
                )

                continue

            if open_trade_exists(
                asset,
                direction,
            ):

                update_best_candidate(
                    asset,
                    direction,
                    pattern_name,
                    combined_score,
                    "DB Check",
                    "DB Check",
                    "A trade with the same asset and direction is already OPEN.",
                )

                continue

            update_best_candidate(
                asset,
                direction,
                pattern_name,
                combined_score,
                "Signal Ready",
                None,
                None,
            )

            return {
                "asset": asset,
                "symbol": get_contract(
                    asset
                ),
                "direction": direction,
                "pattern": pattern_name,
                "signal_key": signal_key,

                "pattern_time":
                    pattern_15m[
                        "pattern_time"
                    ],

                "breakout_time":
                    breakout_15m[
                        "time"
                    ],

                "retest_time":
                    retest_15m[
                        "time"
                    ],

                "confirmation_time":
                    confirmation_15m[
                        "time"
                    ],

                "entry_time":
                    confirmation_15m[
                        "time"
                    ],

                "entry_price":
                    entry_price,

                "sl_price":
                    sl_price,

                "tp_price":
                    tp_price,

                "pattern_1h":
                    pattern_1h,

                "pattern_15m":
                    pattern_15m,

                "breakout_1h":
                    breakout_1h,

                "breakout_15m":
                    breakout_15m,

                "retest_15m":
                    retest_15m,

                "confirmation_15m":
                    confirmation_15m,

                "score":
                    combined_score,
            }

        return None

    except Exception as exc:

        print(
            f"Candidate error {asset}: {exc}"
        )

        traceback.print_exc()

        STATS["errors"] += 1

        update_best_candidate(
            asset,
            None,
            None,
            0,
            "1H Pattern",
            "Scanner Error",
            str(exc),
        )

        return None


# ============================================================
# INSERT TRADE
# ============================================================

def insert_trade(
    signal
):

    conn = db_connect()

    timestamp = now_ts()

    sql = """
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
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?
        )
    """

    values = (
        signal["symbol"],
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
        signal["entry_price"],
        "OPEN",
        timestamp,
        timestamp,
    )

    cursor = conn.execute(
        sql,
        values,
    )

    inserted = (
        cursor.rowcount == 1
    )

    conn.commit()
    conn.close()

    return inserted


# ============================================================
# CHART
# ============================================================

def create_15m_chart(
    asset,
    df15,
    signal,
):

    pattern = signal[
        "pattern_15m"
    ]

    start_time = min(
        pattern["pattern_time"],
        signal[
            "breakout_15m"
        ]["time"],
    )

    chart_df = df15[
        df15["time"]
        >= start_time - 30 * 900
    ].tail(
        CHART_CANDLES
    ).copy()

    if chart_df.empty:
        return None

    fig, ax = plt.subplots(
        figsize=(13, 7)
    )

    x_values = np.arange(
        len(chart_df)
    )

    ax.plot(
        x_values,
        chart_df[
            "close"
        ].values,
        linewidth=1.4,
        label="15M Close",
    )

    points = pattern[
        "points"
    ]

    point_labels = {
        "left": "L",
        "valley": "V",
        "right": "R",
        "left_shoulder": "LS",
        "head": "H",
        "right_shoulder": "RS",
        "neckline_left": "NL",
        "neckline_right": "NR",
    }

    # --------------------------------------------------------
    # Correct point side depending on pattern type.
    # --------------------------------------------------------

    high_points = set()

    if pattern["pattern"] == "Double Top":

        high_points = {
            "left",
            "right",
        }

    elif pattern["pattern"] == "Double Bottom":

        high_points = {
            "valley",
        }

    elif pattern["pattern"] == "Head & Shoulders":

        high_points = {
            "left_shoulder",
            "head",
            "right_shoulder",
        }

    elif pattern["pattern"] == "Inverse Head & Shoulders":

        high_points = {
            "neckline_left",
            "neckline_right",
        }

    for (
        point_name,
        point_index,
    ) in points.items():

        if point_index < 0:
            continue

        if point_index >= len(
            df15
        ):
            continue

        timestamp = int(
            df15.iloc[
                point_index
            ]["time"]
        )

        matches = np.where(
            chart_df[
                "time"
            ].values
            == timestamp
        )[0]

        if len(matches) == 0:
            continue

        chart_index = int(
            matches[0]
        )

        if point_name in high_points:

            price = float(
                chart_df.iloc[
                    chart_index
                ]["high"]
            )

        else:

            price = float(
                chart_df.iloc[
                    chart_index
                ]["low"]
            )

        label = point_labels.get(
            point_name,
            point_name,
        )

        ax.scatter(
            chart_index,
            price,
            s=65,
            zorder=5,
        )

        ax.annotate(
            label,
            (
                chart_index,
                price,
            ),
            xytext=(0, 10),
            textcoords="offset points",
            ha="center",
            fontsize=10,
            fontweight="bold",
        )

    neckline = pattern[
        "neckline"
    ]

    ax.axhline(
        neckline,
        linestyle="--",
        linewidth=1.2,
        label=(
            f"Neckline "
            f"{neckline:.8g}"
        ),
    )

    def mark_event(
        event,
        label,
    ):

        timestamp = event[
            "time"
        ]

        matches = np.where(
            chart_df[
                "time"
            ].values
            == timestamp
        )[0]

        if len(matches) == 0:
            return

        chart_index = int(
            matches[0]
        )

        price = float(
            event["price"]
        )

        ax.scatter(
            chart_index,
            price,
            s=75,
            zorder=6,
        )

        ax.annotate(
            label,
            (
                chart_index,
                price,
            ),
            xytext=(0, -18),
            textcoords="offset points",
            ha="center",
            fontsize=9,
            fontweight="bold",
        )

    mark_event(
        signal[
            "breakout_15m"
        ],
        "BREAKOUT",
    )

    mark_event(
        signal[
            "retest_15m"
        ],
        "RETEST",
    )

    mark_event(
        signal[
            "confirmation_15m"
        ],
        "ENTRY",
    )

    ax.axhline(
        signal["tp_price"],
        linestyle=":",
        linewidth=1.5,
        label=(
            f"TP "
            f"{signal['tp_price']:.8g}"
        ),
    )

    ax.axhline(
        signal["sl_price"],
        linestyle=":",
        linewidth=1.5,
        label=(
            f"SL "
            f"{signal['sl_price']:.8g}"
        ),
    )

    ax.set_title(
        f"{asset} | 15M "
        f"{signal['pattern']} | "
        f"{signal['direction']}"
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
        f"chart_{asset}_"
        f"{signal['confirmation_time']}.png"
    )

    path = os.path.join(
        "/tmp",
        filename,
    )

    fig.savefig(
        path,
        dpi=140,
        bbox_inches="tight",
    )

    plt.close(fig)

    return path


# ============================================================
# NEW SIGNAL MESSAGE
# ============================================================

def signal_caption(
    signal
):

    if signal["direction"] == "LONG":
        emoji = "🟢"
    else:
        emoji = "🔴"

    entry = float(
        signal["entry_price"]
    )

    tp = float(
        signal["tp_price"]
    )

    sl = float(
        signal["sl_price"]
    )

    if entry != 0:

        if signal[
            "direction"
        ] == "LONG":

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

    else:

        tp_pct = 0
        sl_pct = 0

    return (
        f"<b>{emoji} NEW SIGNAL</b>\n\n"

        f"<b>{signal['asset']} "
        f"{signal['direction']}</b>\n"

        f"📌 Pattern: "
        f"{signal['pattern']}\n\n"

        f"💰 Entry: "
        f"{entry:.10g}\n"

        f"🎯 TP: "
        f"{tp:.10g} "
        f"({tp_pct:+.2f}%)\n"

        f"🛑 SL: "
        f"{sl:.10g} "
        f"({sl_pct:+.2f}%)\n\n"

        f"🕐 1H Pattern: "
        f"{format_time(signal['pattern_1h']['pattern_time'])}\n"

        f"💥 1H Breakout: "
        f"{format_time(signal['breakout_1h']['time'])}\n"

        f"🔹 15M Pattern: "
        f"{format_time(signal['pattern_15m']['pattern_time'])}\n"

        f"💥 15M Breakout: "
        f"{format_time(signal['breakout_15m']['time'])}\n"

        f"🔄 15M Retest: "
        f"{format_time(signal['retest_15m']['time'])}\n"

        f"✅ 15M Confirmation: "
        f"{format_time(signal['confirmation_15m']['time'])}\n\n"

        f"🎯 TP/SL: Natural Pattern Levels\n"
        f"📊 Chart: 15M"
    )


# ============================================================
# SEND NEW SIGNAL
# ============================================================

def send_new_signal(
    signal,
    df15,
):

    caption = signal_caption(
        signal
    )

    chart_path = create_15m_chart(
        signal["asset"],
        df15,
        signal,
    )

    sent = False

    if (
        chart_path
        and os.path.exists(
            chart_path
        )
    ):

        sent = telegram_send_photo(
            chart_path,
            caption,
        )

        try:
            os.remove(
                chart_path
            )
        except Exception:
            pass

    else:

        sent = telegram_send_text(
            caption
        )

    if sent:
        STATS[
            "signals_sent"
        ] += 1

    return sent


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade_id,
    exit_price,
    reason,
):

    conn = db_connect()

    exit_time = now_ts()

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
        """,
        (
            exit_time,
            exit_price,
            reason,
            exit_price,
            exit_time,
            trade_id,
        ),
    )

    conn.commit()

    row = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE id=?
        """,
        (trade_id,),
    ).fetchone()

    conn.close()

    return row


# ============================================================
# CHECK EXIT
# ============================================================

def check_trade_exit(
    trade
):

    price = get_live_price(
        trade["asset"]
    )

    if price is None:
        return None

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

    reason = None

    if direction == "LONG":

        if price >= tp:
            reason = "TP"

        elif price <= sl:
            reason = "SL"

    else:

        if price <= tp:
            reason = "TP"

        elif price >= sl:
            reason = "SL"

    if reason is None:

        try:

            if isinstance(
                trade["entry_time"],
                str,
            ):

                entry_dt = datetime.fromisoformat(
                    trade[
                        "entry_time"
                    ].replace(
                        "Z",
                        "+00:00",
                    )
                )

                entry_timestamp = (
                    entry_dt.timestamp()
                )

            else:

                entry_timestamp = float(
                    trade[
                        "entry_time"
                    ]
                )

            if (
                now_ts()
                - entry_timestamp
                >= MAX_HOLD_HOURS
                * 3600
            ):

                reason = "TIME"

        except Exception:
            pass

    return {
        "price": price,
        "reason": reason,
    }


# ============================================================
# CLOSE MESSAGE
# ============================================================

def send_close_message(
    trade
):

    entry = float(
        trade["entry_price"]
    )

    exit_price = float(
        trade["exit_price"]
    )

    direction = trade[
        "direction"
    ]

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

    reason = trade[
        "exit_reason"
    ]

    if reason == "TP":
        icon = "🎯"

    elif reason == "SL":
        icon = "🛑"

    else:
        icon = "⏱️"

    duration = duration_text(
        trade["created_at"],
        trade["exit_time"],
    )

    text = (
        f"<b>{icon} CLOSED TRADE</b>\n\n"

        f"<b>{trade['asset']} "
        f"{direction}</b>\n"

        f"📌 Pattern: "
        f"{trade['pattern']}\n"

        f"💰 Entry: "
        f"{entry:.10g}\n"

        f"💵 Exit: "
        f"{exit_price:.10g}\n"

        f"📊 Result: "
        f"{pnl_pct:+.2f}%\n"

        f"📌 Reason: "
        f"{reason}\n"

        f"🕐 Entry: "
        f"{format_time(trade['entry_time'])}\n"

        f"🕐 Exit: "
        f"{format_time(trade['exit_time'])}\n"

        f"⏱ Duration: "
        f"{duration}"
    )

    if telegram_send_text(
        text
    ):

        STATS[
            "signals_sent"
        ] += 1


# ============================================================
# RECONCILE OPEN TRADES
# ============================================================

def reconcile_open_trades():

    conn = db_connect()

    trades = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status='OPEN'
        ORDER BY id ASC
        """
    ).fetchall()

    conn.close()

    for trade in trades:

        try:

            result = check_trade_exit(
                trade
            )

            if result is None:
                continue

            price = result[
                "price"
            ]

            reason = result[
                "reason"
            ]

            conn2 = db_connect()

            conn2.execute(
                """
                UPDATE trades
                SET
                    current_price=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    price,
                    now_ts(),
                    trade["id"],
                ),
            )

            conn2.commit()
            conn2.close()

            if reason is None:
                continue

            closed_trade = close_trade(
                trade["id"],
                price,
                reason,
            )

            if closed_trade:

                send_close_message(
                    closed_trade
                )

                STATS[
                    "closed_trades"
                ] += 1

        except Exception as exc:

            print(
                "Reconciliation error:",
                exc,
            )

            STATS[
                "errors"
            ] += 1


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():

    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status='OPEN'
        ORDER BY entry_time DESC
        """
    ).fetchall()

    conn.close()

    return rows


def open_trades_text():

    trades = get_open_trades()

    if not trades:

        return (
            "<b>📂 OPEN TRADES</b>\n"
            "No open trades."
        )

    lines = [
        (
            f"<b>📂 OPEN TRADES: "
            f"{len(trades)}</b>\n"
        )
    ]

    for trade in trades:

        price = get_live_price(
            trade["asset"]
        )

        if price is None:
            price = trade[
                "current_price"
            ]

        if price is None:
            price = trade[
                "entry_price"
            ]

        price = float(price)

        entry = float(
            trade["entry_price"]
        )

        if trade[
            "direction"
        ] == "LONG":

            pnl = (
                (price - entry)
                / entry
                * 100
            )

            emoji = "🟢"

        else:

            pnl = (
                (entry - price)
                / entry
                * 100
            )

            emoji = "🔴"

        lines.append(
            f"{emoji} "
            f"<b>{trade['asset']} "
            f"{trade['direction']}</b>\n"

            f"📌 {trade['pattern']}\n"

            f"💰 Entry: "
            f"{entry:.10g}\n"

            f"💵 Current: "
            f"{price:.10g} "
            f"({pnl:+.2f}%)\n"

            f"🎯 TP: "
            f"{float(trade['tp_price']):.10g}\n"

            f"🛑 SL: "
            f"{float(trade['sl_price']):.10g}\n"

            f"⏱ Duration: "
            f"{duration_text(trade['created_at'])}\n"
        )

    return "\n".join(
        lines
    )


# ============================================================
# PERFORMANCE
# ============================================================

def performance_text():

    conn = db_connect()

    total = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        """
    ).fetchone()[0]

    open_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status='OPEN'
        """
    ).fetchone()[0]

    closed_trades = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status='CLOSED'
        """
    ).fetchall()

    conn.close()

    wins = 0
    losses = 0
    total_pnl = 0.0

    for trade in closed_trades:

        try:

            entry = float(
                trade["entry_price"]
            )

            exit_price = float(
                trade["exit_price"]
            )

            if trade[
                "direction"
            ] == "LONG":

                pnl = (
                    exit_price - entry
                ) / entry * 100

            else:

                pnl = (
                    entry - exit_price
                ) / entry * 100

            total_pnl += pnl

            if pnl > 0:
                wins += 1

            elif pnl < 0:
                losses += 1

        except Exception:
            continue

    closed_count = len(
        closed_trades
    )

    if closed_count > 0:

        win_rate = (
            wins
            / closed_count
            * 100
        )

    else:

        win_rate = 0.0

    return (
        "<b>📈 PERFORMANCE</b>\n\n"

        f"Total Trades: "
        f"{total}\n"

        f"Closed: "
        f"{closed_count}\n"

        f"Open: "
        f"{open_count}\n"

        f"🟢 Wins: "
        f"{wins}\n"

        f"🔴 Losses: "
        f"{losses}\n"

        f"Win Rate: "
        f"{win_rate:.2f}%\n"

        f"Net PnL: "
        f"{total_pnl:+.2f}%"
    )


# ============================================================
# SCAN SUMMARY
# ============================================================

def scan_summary_text():

    return (
        "<b>🔎 SCAN SUMMARY</b>\n\n"

        f"Assets Scanned: "
        f"{STATS['assets_scanned']}/"
        f"{len(ASSETS)}\n"

        f"1H Patterns: "
        f"{STATS['patterns_1h']}\n"

        f"1H Breakouts: "
        f"{STATS['breakouts_1h']}\n"

        f"15M Patterns: "
        f"{STATS['patterns_15m']}\n"

        f"15M Breakouts: "
        f"{STATS['breakouts_15m']}\n"

        f"15M Retests: "
        f"{STATS['retests_15m']}\n"

        f"15M Confirmations: "
        f"{STATS['confirmations_15m']}\n"

        f"New Signals: "
        f"{STATS['unique_entries']}\n"

        f"Errors: "
        f"{STATS['errors']}"
    )


# ============================================================
# BEST CANDIDATE TEXT
# ============================================================

def best_candidate_text():

    candidate = BEST_CANDIDATE

    if not candidate[
        "asset"
    ]:

        return (
            "<b>🏆 BEST CANDIDATE</b>\n"
            "No valid candidate was found.\n\n"
            "Reason: No recent valid 1H pattern."
        )

    asset = candidate[
        "asset"
    ]

    direction = (
        candidate["direction"]
        or "-"
    )

    pattern = (
        candidate["pattern"]
        or "-"
    )

    stage = (
        candidate["stage"]
        or "-"
    )

    rejected_at = (
        candidate["rejected_at"]
        or "-"
    )

    reason = (
        candidate["reason"]
        or "No rejection. Candidate reached the final stage."
    )

    score = candidate[
        "score"
    ]

    if direction == "LONG":
        emoji = "🟢"
    elif direction == "SHORT":
        emoji = "🔴"
    else:
        emoji = "⚪"

    return (
        "<b>🏆 BEST CANDIDATE</b>\n\n"

        f"{emoji} <b>{asset} "
        f"{direction}</b>\n"

        f"📌 Pattern: "
        f"{pattern}\n"

        f"⭐ Score: "
        f"{score:.2f}\n"

        f"📍 Stage Reached: "
        f"{stage}\n"

        f"❌ Rejected At: "
        f"{rejected_at}\n"

        f"📝 Reason: "
        f"{reason}"
    )


# ============================================================
# NO SIGNAL REPORT
# ============================================================

def send_no_signal_report():

    report = (
        "<b>📊 KRAKEN PATTERN SCANNER</b>\n\n"

        f"Version: {VERSION}\n"
        "Mode: PAPER ONLY\n"
        "Time: Iran\n\n"

        f"{scan_summary_text()}\n\n"

        f"{best_candidate_text()}\n\n"

        f"{open_trades_text()}\n\n"

        f"{performance_text()}"
    )

    return telegram_send_text(
        report
    )


# ============================================================
# PERIODIC REPORT
# ============================================================

def periodic_report():

    current = now_ts()

    last_report = get_meta(
        "last_periodic_report"
    )

    if last_report:

        try:

            elapsed = (
                current
                - int(
                    float(
                        last_report
                    )
                )
            )

            if (
                elapsed
                < PERIODIC_REPORT_SECONDS
            ):
                return False

        except Exception:
            pass

    report = (
        "<b>📊 KRAKEN PATTERN SCANNER</b>\n\n"

        f"Version: {VERSION}\n"
        "Mode: PAPER ONLY\n\n"

        f"{open_trades_text()}\n\n"

        f"{performance_text()}\n\n"

        f"{scan_summary_text()}"
    )

    if telegram_send_text(
        report
    ):

        set_meta(
            "last_periodic_report",
            current,
        )

        return True

    return False


# ============================================================
# PRINT ACTION SUMMARY
# ============================================================

def print_scan_summary():

    print()
    print("=" * 70)
    print("SCAN SUMMARY")
    print("=" * 70)

    print(
        f"Assets Scanned       : "
        f"{STATS['assets_scanned']}/{len(ASSETS)}"
    )

    print(
        f"1H Patterns          : "
        f"{STATS['patterns_1h']}"
    )

    print(
        f"1H Breakouts         : "
        f"{STATS['breakouts_1h']}"
    )

    print(
        f"15M Patterns         : "
        f"{STATS['patterns_15m']}"
    )

    print(
        f"15M Breakouts        : "
        f"{STATS['breakouts_15m']}"
    )

    print(
        f"15M Retests          : "
        f"{STATS['retests_15m']}"
    )

    print(
        f"15M Confirmations    : "
        f"{STATS['confirmations_15m']}"
    )

    print(
        f"Candidates           : "
        f"{STATS['candidates']}"
    )

    print(
        f"New Signals          : "
        f"{STATS['unique_entries']}"
    )

    print(
        f"Closed Trades        : "
        f"{STATS['closed_trades']}"
    )

    print(
        f"Errors               : "
        f"{STATS['errors']}"
    )

    print()
    print("-" * 70)
    print("BEST CANDIDATE")
    print("-" * 70)

    if BEST_CANDIDATE[
        "asset"
    ]:

        print(
            "Asset       :",
            BEST_CANDIDATE[
                "asset"
            ],
        )

        print(
            "Direction   :",
            BEST_CANDIDATE[
                "direction"
            ],
        )

        print(
            "Pattern     :",
            BEST_CANDIDATE[
                "pattern"
            ],
        )

        print(
            "Score       :",
            f"{BEST_CANDIDATE['score']:.2f}",
        )

        print(
            "Stage       :",
            BEST_CANDIDATE[
                "stage"
            ],
        )

        print(
            "Rejected At :",
            BEST_CANDIDATE[
                "rejected_at"
            ],
        )

        print(
            "Reason      :",
            BEST_CANDIDATE[
                "reason"
            ],
        )

    else:

        print(
            "No valid recent 1H candidate."
        )

    print("=" * 70)
    print()


# ============================================================
# SCAN
# ============================================================

def scan():

    STATS["scans"] += 1

    reset_best_candidate()

    try:

        load_prices()

    except Exception as exc:

        print(
            "Price loading error:",
            exc,
        )

        STATS[
            "errors"
        ] += 1

        return 0

    candidates = []

    for asset in ASSETS:

        try:

            STATS[
                "assets_scanned"
            ] += 1

            candidate = build_candidate(
                asset
            )

            if candidate:

                candidates.append(
                    candidate
                )

                STATS[
                    "candidates"
                ] += 1

        except Exception as exc:

            print(
                f"Scan error {asset}: "
                f"{exc}"
            )

            traceback.print_exc()

            STATS[
                "errors"
            ] += 1

    if not candidates:

        return 0

    candidates.sort(
        key=lambda candidate:
        candidate["score"],
        reverse=True,
    )

    selected = []

    for candidate in candidates:

        if (
            len(selected)
            >= MAX_SIGNALS_PER_SCAN
        ):
            break

        if signal_exists(
            candidate["signal_key"]
        ):
            continue

        if open_trade_exists(
            candidate["asset"],
            candidate["direction"],
        ):
            continue

        selected.append(
            candidate
        )

    inserted_count = 0

    for signal in selected:

        inserted = insert_trade(
            signal
        )

        if not inserted:
            continue

        inserted_count += 1

        STATS[
            "unique_entries"
        ] += 1

        # A real new signal has now been created.
        # The Telegram scan summary will NOT be sent.

        try:

            df15 = get_candles(
                signal["asset"],
                "15m",
                PATTERN_LOOKBACK_15M,
            )

            send_new_signal(
                signal,
                df15,
            )

        except Exception as exc:

            print(
                "Signal notification error:",
                exc,
            )

            STATS[
                "errors"
            ] += 1

    return inserted_count


# ============================================================
# STARTUP MESSAGE
# ============================================================

def startup_message():

    text = (
        "<b>🚀 KRAKEN PATTERN SCANNER</b>\n\n"

        f"Version: {VERSION}\n"
        "Mode: PAPER ONLY\n\n"

        "<b>Strategy</b>\n"

        "1H Pattern → 1H Breakout\n"
        "→ 15M Same Pattern\n"
        "→ 15M Breakout\n"
        "→ 15M Retest\n"
        "→ 15M Confirmation\n"
        "→ Entry\n\n"

        "🎯 TP: Natural 15M Pattern Target\n"
        "🛑 SL: Natural 15M Pattern Invalidation\n\n"

        "Fixed TP/SL: OFF\n"
        "RR Filter: OFF"
    )

    telegram_send_text(
        text
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)

    print(
        "KRAKEN FUTURES PATTERN LIVE SIGNAL SCANNER"
    )

    print(
        f"VERSION {VERSION}"
    )

    print("=" * 70)

    print(
        "REAL_TRADING:",
        REAL_TRADING,
    )

    if REAL_TRADING:

        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    init_db()

    try:

        load_prices()

    except Exception as exc:

        print(
            "Initial price loading warning:",
            exc,
        )

    try:

        reconcile_open_trades()

    except Exception as exc:

        print(
            "Startup reconciliation error:",
            exc,
        )

        STATS[
            "errors"
        ] += 1

    inserted_count = scan()

    # --------------------------------------------------------
    # Always print diagnostics to GitHub Actions.
    # --------------------------------------------------------

    print_scan_summary()

    # --------------------------------------------------------
    # Telegram behavior:
    #
    # NEW SIGNAL:
    #   Only the normal signal notification is sent.
    #
    # NO NEW SIGNAL:
    #   Scan Summary
    #   Best Candidate
    #   Open Trades
    #   Performance
    #
    # This prevents duplicate reports.
    # --------------------------------------------------------

    if inserted_count == 0:

        sent = send_no_signal_report()

        if sent:

            # Prevent periodic_report() from immediately
            # sending another almost identical report.
            set_meta(
                "last_periodic_report",
                now_ts(),
            )

    else:

        # If there is a new signal, do not send the scan
        # diagnostic report. The normal signal message
        # already went to Telegram.
        periodic_report()

    print("=" * 70)

    print(
        "SCAN COMPLETE"
    )

    print(
        f"NEW SIGNALS THIS SCAN: "
        f"{inserted_count}"
    )

    print("=" * 70)


if __name__ == "__main__":
    main()
