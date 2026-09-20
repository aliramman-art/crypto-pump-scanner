# ============================================================
# KRAKEN FUTURES PATTERN LIVE SIGNAL SCANNER
# VERSION 5.9.3
# ============================================================
#
# FIXES IN 5.9.3:
#
# 1) Kraken Charts API:
#       /api/charts/v1/trade/{symbol}/{resolution}
#
#    Correct resolutions:
#       1h
#       5m
#
#    Uses:
#       count=
#
# 2) CONTRACT MAPPING:
#       EXACT symbol matching only.
#
#    Example:
#       XBT -> PF_XBTUSD
#       TRX -> PF_TRXUSD
#
#    No substring matching.
#    This prevents:
#       XBT -> PF_AIXBTUSD
#       TRX -> PF_MSTRXUSD
#
# 3) Existing SQLite DB is preserved.
#
# 4) updated_at is always supplied on INSERT.
#
# 5) Existing DB columns are migrated safely.
#
# 6) REAL_TRADING remains disabled.
#
# STRATEGY:
#
#       1H Pattern
#            ↓
#       1H Closed Candle Breakout
#            ↓
#       First 5M Retest
#            ↓
#       5M Confirmation
#            ↓
#       Entry
#
# ============================================================

import os
import time
import sqlite3
import traceback
import requests
import pandas as pd

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


# ============================================================
# VERSION
# ============================================================

VERSION = "5.9.3"


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com"

INTERVAL_1H = "1h"
INTERVAL_5M = "5m"

SL_PCT = 0.01
TP_PCT = 0.01
RR = 1.0

REAL_TRADING = False

MAX_HOLD_HOURS = 48

MAIN_LOOKBACK = 500
ENTRY_LOOKBACK = 1500

REQUEST_TIMEOUT = 30
REQUEST_SLEEP = 0.10

PERIODIC_REPORT_SECONDS = 15 * 60

DB_FILE = "kraken_pattern_live_v52.db"


# ============================================================
# SIGNAL FRESHNESS
# ============================================================

MAX_ENTRY_AGE_CANDLES = 2
MAX_ENTRY_AGE_MINUTES = 10


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


# ============================================================
# PATTERN PARAMETERS
# ============================================================

PIVOT_LEFT = 3
PIVOT_RIGHT = 3

DOUBLE_TOLERANCE = 0.015

DOUBLE_MIN_SEPARATION = 5
DOUBLE_MAX_SEPARATION = 60

HS_MIN_SEPARATION = 5
HS_MAX_SEPARATION = 80

PATTERN_LOOKBACK = 250

BREAKOUT_LOOKAHEAD = 24
RETEST_LOOKAHEAD = 12
CONFIRMATION_LOOKAHEAD = 6

MIN_PATTERN_DISTANCE = 5
MAX_PATTERN_DISTANCE = 80

PATTERN_TOLERANCE = 0.015


# ============================================================
# TIME
# ============================================================

TEHRAN_TZ = ZoneInfo("Asia/Tehran")


def now_ts():
    return int(time.time())


def utc_now():
    return datetime.now(timezone.utc)


def fmt_tehran(ts):
    if ts is None:
        return "-"

    dt = datetime.fromtimestamp(
        int(ts),
        tz=timezone.utc
    )

    return dt.astimezone(
        TEHRAN_TZ
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def duration_text(
    start_ts,
    end_ts=None
):
    if start_ts is None:
        return "-"

    if end_ts is None:
        end_ts = now_ts()

    seconds = max(
        0,
        int(end_ts) - int(start_ts)
    )

    minutes = seconds // 60
    hours = minutes // 60
    minutes = minutes % 60

    if hours > 0:
        return f"{hours}h {minutes}m"

    return f"{minutes}m"


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID"
)


def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN:
        print("Telegram token missing")
        return False

    if not TELEGRAM_CHAT_ID:
        print("Telegram chat id missing")
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

        r = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if not r.ok:
            print(
                "[TELEGRAM ERROR]",
                r.status_code,
                r.text
            )
            return False

        return True

    except Exception as e:

        print(
            "[TELEGRAM EXCEPTION]",
            e
        )

        return False


# ============================================================
# KRAKEN REQUEST
# ============================================================

def kraken_get(
    path,
    params=None
):

    url = BASE_URL + path

    r = requests.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT,
        headers={
            "Accept": "application/json"
        }
    )

    r.raise_for_status()

    return r.json()


# ============================================================
# CONTRACTS
# ============================================================

def get_contracts():

    data = kraken_get(
        "/derivatives/api/v3/instruments"
    )

    return data.get(
        "instruments",
        []
    )


def build_contract_map():

    contracts = get_contracts()

    result = {}

    for c in contracts:

        symbol = str(
            c.get("symbol", "")
        ).upper()

        if not symbol:
            continue

        # ----------------------------------------------------
        # EXACT MATCH ONLY
        # ----------------------------------------------------

        for asset in ASSETS:

            expected = (
                f"PF_{asset}USD"
            )

            if symbol == expected:

                result[asset] = symbol

                break

    print(
        f"[CONTRACT MAP] "
        f"{len(result)}/{len(ASSETS)} assets mapped"
    )

    for asset in ASSETS:

        if asset in result:

            print(
                f"[MAP] {asset} -> "
                f"{result[asset]}"
            )

        else:

            print(
                f"[MAP MISSING] {asset}"
            )

    return result


# ============================================================
# CANDLES
# ============================================================

def fetch_candles(
    contract,
    interval,
    lookback
):

    if not contract:
        return pd.DataFrame()

    interval_seconds = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
        "12h": 43200,
        "1d": 86400,
        "1w": 604800,
    }

    if interval not in interval_seconds:

        raise ValueError(
            f"Unsupported interval: {interval}"
        )

    path = (
        "/api/charts/v1/trade/"
        f"{contract}/"
        f"{interval}"
    )

    params = {
        "count": int(lookback)
    }

    try:

        data = kraken_get(
            path,
            params=params
        )

    except Exception as e:

        print(
            f"[KRAKEN CANDLE ERROR] "
            f"{contract} {interval}: {e}"
        )

        return pd.DataFrame()

    candles = data.get(
        "candles",
        []
    )

    if not candles:

        print(
            f"[KRAKEN NO CANDLES] "
            f"{contract} {interval}"
        )

        return pd.DataFrame()

    rows = []

    for candle in candles:

        try:

            if isinstance(
                candle,
                dict
            ):

                ts = (
                    candle.get("time")
                    or candle.get("timestamp")
                )

                o = candle.get("open")
                h = candle.get("high")
                l = candle.get("low")
                c = candle.get("close")

                volume = candle.get(
                    "volume",
                    0
                )

            else:

                ts = candle[0]
                o = candle[1]
                h = candle[2]
                l = candle[3]
                c = candle[4]

                volume = (
                    candle[5]
                    if len(candle) > 5
                    else 0
                )

            ts = int(
                float(ts)
            )

            # Kraken Charts API:
            # milliseconds
            if ts > 10_000_000_000:
                ts //= 1000

            rows.append([
                ts,
                float(o),
                float(h),
                float(l),
                float(c),
                float(volume)
            ])

        except Exception:

            continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        rows,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
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

    # --------------------------------------------------------
    # CLOSED CANDLES ONLY
    # --------------------------------------------------------

    current = now_ts()

    df = df[
        (
            df["timestamp"]
            + interval_seconds[interval]
        ) <= current
    ]

    return df.tail(
        lookback
    ).reset_index(
        drop=True
    )


# ============================================================
# PIVOTS
# ============================================================

def pivot_highs(df):

    highs = df["high"].values

    result = []

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT
    ):

        left = highs[
            i - PIVOT_LEFT:i
        ]

        right = highs[
            i + 1:
            i + 1 + PIVOT_RIGHT
        ]

        if (
            highs[i] > left.max()
            and highs[i] >= right.max()
        ):

            result.append(i)

    return result


def pivot_lows(df):

    lows = df["low"].values

    result = []

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT
    ):

        left = lows[
            i - PIVOT_LEFT:i
        ]

        right = lows[
            i + 1:
            i + 1 + PIVOT_RIGHT
        ]

        if (
            lows[i] < left.min()
            and lows[i] <= right.min()
        ):

            result.append(i)

    return result


# ============================================================
# DOUBLE TOP
# ============================================================

def detect_double_top(df):

    patterns = []

    highs = pivot_highs(df)

    for a in range(
        len(highs)
    ):

        for b in range(
            a + 1,
            len(highs)
        ):

            i = highs[a]
            j = highs[b]

            separation = j - i

            if (
                separation
                < DOUBLE_MIN_SEPARATION
            ):
                continue

            if (
                separation
                > DOUBLE_MAX_SEPARATION
            ):
                break

            p1 = float(
                df.iloc[i]["high"]
            )

            p2 = float(
                df.iloc[j]["high"]
            )

            diff = (
                abs(p1 - p2)
                / max(p1, p2)
            )

            if diff > DOUBLE_TOLERANCE:
                continue

            between = df.iloc[
                i:j + 1
            ]

            neckline = float(
                between["low"].min()
            )

            patterns.append({
                "pattern": "DOUBLE_TOP",
                "direction": "SHORT",
                "pattern_time": int(
                    df.iloc[j]["timestamp"]
                ),
                "level": neckline,
                "left_index": i,
                "right_index": j
            })

    return patterns


# ============================================================
# DOUBLE BOTTOM
# ============================================================

def detect_double_bottom(df):

    patterns = []

    lows = pivot_lows(df)

    for a in range(
        len(lows)
    ):

        for b in range(
            a + 1,
            len(lows)
        ):

            i = lows[a]
            j = lows[b]

            separation = j - i

            if (
                separation
                < DOUBLE_MIN_SEPARATION
            ):
                continue

            if (
                separation
                > DOUBLE_MAX_SEPARATION
            ):
                break

            p1 = float(
                df.iloc[i]["low"]
            )

            p2 = float(
                df.iloc[j]["low"]
            )

            diff = (
                abs(p1 - p2)
                / max(
                    min(p1, p2),
                    1e-12
                )
            )

            if diff > DOUBLE_TOLERANCE:
                continue

            between = df.iloc[
                i:j + 1
            ]

            neckline = float(
                between["high"].max()
            )

            patterns.append({
                "pattern": "DOUBLE_BOTTOM",
                "direction": "LONG",
                "pattern_time": int(
                    df.iloc[j]["timestamp"]
                ),
                "level": neckline,
                "left_index": i,
                "right_index": j
            })

    return patterns


# ============================================================
# HEAD & SHOULDERS
# ============================================================

def detect_head_shoulders(df):

    patterns = []

    highs = pivot_highs(df)

    if len(highs) >= 3:

        for k in range(
            len(highs) - 2
        ):

            left = highs[k]
            head = highs[k + 1]
            right = highs[k + 2]

            sep1 = head - left
            sep2 = right - head

            if (
                sep1 < HS_MIN_SEPARATION
                or sep2 < HS_MIN_SEPARATION
            ):
                continue

            if (
                sep1 > HS_MAX_SEPARATION
                or sep2 > HS_MAX_SEPARATION
            ):
                continue

            lh = float(
                df.iloc[left]["high"]
            )

            hh = float(
                df.iloc[head]["high"]
            )

            rh = float(
                df.iloc[right]["high"]
            )

            if not (
                hh > lh
                and hh > rh
            ):
                continue

            shoulder_diff = (
                abs(lh - rh)
                / max(lh, rh)
            )

            if (
                shoulder_diff
                > DOUBLE_TOLERANCE
            ):
                continue

            middle = df.iloc[
                left:right + 1
            ]

            neckline = float(
                middle["low"].min()
            )

            patterns.append({
                "pattern":
                    "HEAD_AND_SHOULDERS",
                "direction":
                    "SHORT",
                "pattern_time":
                    int(
                        df.iloc[right]
                        ["timestamp"]
                    ),
                "level":
                    neckline,
                "left_index":
                    left,
                "right_index":
                    right
            })

    return patterns


# ============================================================
# INVERSE HEAD & SHOULDERS
# ============================================================

def detect_inverse_head_shoulders(df):

    patterns = []

    lows = pivot_lows(df)

    if len(lows) >= 3:

        for k in range(
            len(lows) - 2
        ):

            left = lows[k]
            head = lows[k + 1]
            right = lows[k + 2]

            sep1 = head - left
            sep2 = right - head

            if (
                sep1 < HS_MIN_SEPARATION
                or sep2 < HS_MIN_SEPARATION
            ):
                continue

            if (
                sep1 > HS_MAX_SEPARATION
                or sep2 > HS_MAX_SEPARATION
            ):
                continue

            ll = float(
                df.iloc[left]["low"]
            )

            hh = float(
                df.iloc[head]["low"]
            )

            rl = float(
                df.iloc[right]["low"]
            )

            if not (
                hh < ll
                and hh < rl
            ):
                continue

            shoulder_diff = (
                abs(ll - rl)
                / max(
                    min(ll, rl),
                    1e-12
                )
            )

            if (
                shoulder_diff
                > DOUBLE_TOLERANCE
            ):
                continue

            middle = df.iloc[
                left:right + 1
            ]

            neckline = float(
                middle["high"].max()
            )

            patterns.append({
                "pattern":
                    "INVERSE_HEAD_AND_SHOULDERS",
                "direction":
                    "LONG",
                "pattern_time":
                    int(
                        df.iloc[right]
                        ["timestamp"]
                    ),
                "level":
                    neckline,
                "left_index":
                    left,
                "right_index":
                    right
            })

    return patterns


# ============================================================
# FLAGS
# ============================================================

def detect_flags(df):

    patterns = []

    if len(df) < 30:
        return patterns

    closes = df["close"]

    for i in range(
        20,
        len(df) - 1
    ):

        base = float(
            closes.iloc[i - 20]
        )

        current = float(
            closes.iloc[i]
        )

        if base == 0:
            continue

        move = (
            current - base
        ) / base

        recent = df.iloc[
            i - 5:i + 1
        ]

        recent_range = (
            float(recent["high"].max())
            -
            float(recent["low"].min())
        )

        if current == 0:
            continue

        if move >= 0.025:

            if (
                recent_range
                / current
                < 0.03
            ):

                patterns.append({
                    "pattern":
                        "BULL_FLAG",
                    "direction":
                        "LONG",
                    "pattern_time":
                        int(
                            df.iloc[i]
                            ["timestamp"]
                        ),
                    "level":
                        float(
                            recent["high"].max()
                        ),
                    "left_index":
                        i - 20,
                    "right_index":
                        i
                })

        elif move <= -0.025:

            if (
                recent_range
                / abs(current)
                < 0.03
            ):

                patterns.append({
                    "pattern":
                        "BEAR_FLAG",
                    "direction":
                        "SHORT",
                    "pattern_time":
                        int(
                            df.iloc[i]
                            ["timestamp"]
                        ),
                    "level":
                        float(
                            recent["low"].min()
                        ),
                    "left_index":
                        i - 20,
                    "right_index":
                        i
                })

    return patterns


# ============================================================
# PATTERN DETECTION
# ============================================================

def detect_patterns(df):

    if df.empty:
        return []

    df = (
        df.tail(PATTERN_LOOKBACK)
        .reset_index(drop=True)
    )

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

    patterns.extend(
        detect_flags(df)
    )

    return patterns


# ============================================================
# BREAKOUT
# ============================================================

def breakout_signal(
    df,
    pattern
):

    if df.empty:
        return None

    level = pattern.get(
        "level"
    )

    if level is None:
        return None

    direction = pattern[
        "direction"
    ]

    pattern_time = int(
        pattern["pattern_time"]
    )

    candidates = df[
        df["timestamp"]
        > pattern_time
    ].copy()

    if candidates.empty:
        return None

    candidates = candidates.head(
        BREAKOUT_LOOKAHEAD
    )

    for _, row in candidates.iterrows():

        close = float(
            row["close"]
        )

        if direction == "LONG":

            if close > level:

                return {
                    "breakout_time":
                        int(
                            row["timestamp"]
                        ),
                    "breakout_price":
                        close,
                    "level":
                        float(level)
                }

        else:

            if close < level:

                return {
                    "breakout_time":
                        int(
                            row["timestamp"]
                        ),
                    "breakout_price":
                        close,
                    "level":
                        float(level)
                }

    return None


# ============================================================
# FIRST 5M RETEST
# ============================================================

def find_first_retest(
    df5,
    breakout,
    direction
):

    if df5.empty:
        return None

    level = float(
        breakout["level"]
    )

    breakout_time = int(
        breakout["breakout_time"]
    )

    candidates = df5[
        df5["timestamp"]
        > breakout_time
    ].copy()

    if candidates.empty:
        return None

    candidates = candidates.head(
        RETEST_LOOKAHEAD
    )

    for _, row in candidates.iterrows():

        low = float(
            row["low"]
        )

        high = float(
            row["high"]
        )

        touched = (
            low <= level <= high
        )

        if touched:

            return {
                "retest_time":
                    int(
                        row["timestamp"]
                    ),
                "retest_price":
                    level
            }

    return None


# ============================================================
# 5M CONFIRMATION
# ============================================================

def find_confirmation(
    df5,
    retest,
    direction
):

    if df5.empty:
        return None

    retest_time = int(
        retest["retest_time"]
    )

    candidates = df5[
        df5["timestamp"]
        > retest_time
    ].copy()

    if candidates.empty:
        return None

    candidates = candidates.head(
        CONFIRMATION_LOOKAHEAD
    )

    for _, row in candidates.iterrows():

        op = float(
            row["open"]
        )

        cl = float(
            row["close"]
        )

        if direction == "LONG":

            if cl > op:

                return {
                    "confirm_time":
                        int(
                            row["timestamp"]
                        ),
                    "confirm_price":
                        cl
                }

        else:

            if cl < op:

                return {
                    "confirm_time":
                        int(
                            row["timestamp"]
                        ),
                    "confirm_price":
                        cl
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

    if df5.empty:
        return None

    confirm_time = int(
        confirmation["confirm_time"]
    )

    candidates = df5[
        df5["timestamp"]
        > confirm_time
    ].copy()

    if candidates.empty:
        return None

    row = candidates.iloc[0]

    entry_time = int(
        row["timestamp"]
    )

    entry_price = float(
        row["close"]
    )

    age_minutes = (
        now_ts() - entry_time
    ) / 60

    # --------------------------------------------------------
    # EXACT FRESHNESS LIMIT
    # --------------------------------------------------------

    max_age = min(
        MAX_ENTRY_AGE_CANDLES * 5,
        MAX_ENTRY_AGE_MINUTES
    )

    if age_minutes > max_age:
        return None

    if direction == "LONG":

        sl = (
            entry_price
            * (1 - SL_PCT)
        )

        tp = (
            entry_price
            * (1 + TP_PCT)
        )

    else:

        sl = (
            entry_price
            * (1 + SL_PCT)
        )

        tp = (
            entry_price
            * (1 - TP_PCT)
        )

    return {
        "entry_time":
            entry_time,
        "entry_price":
            entry_price,
        "sl_price":
            sl,
        "tp_price":
            tp
    }


# ============================================================
# GENERATE LIVE ENTRIES
# ============================================================

def generate_live_entries(
    asset,
    contract
):

    stats = {
        "patterns": 0,
        "recent": 0,
        "breakouts": 0,
        "old_breakouts": 0,
        "retests": 0,
        "confirmations": 0,
        "valid": 0,
        "stale": 0
    }

    df1h = fetch_candles(
        contract,
        INTERVAL_1H,
        MAIN_LOOKBACK
    )

    if df1h.empty:
        return [], stats

    patterns = detect_patterns(
        df1h
    )

    stats["patterns"] = len(
        patterns
    )

    recent_patterns = []

    cutoff = (
        now_ts()
        - 48 * 3600
    )

    for pattern in patterns:

        if (
            pattern["pattern_time"]
            < cutoff
        ):
            continue

        recent_patterns.append(
            pattern
        )

    stats["recent"] = len(
        recent_patterns
    )

    if not recent_patterns:
        return [], stats

    df5 = fetch_candles(
        contract,
        INTERVAL_5M,
        ENTRY_LOOKBACK
    )

    if df5.empty:
        return [], stats

    entries = []

    for pattern in recent_patterns:

        breakout = breakout_signal(
            df1h,
            pattern
        )

        if not breakout:
            continue

        stats["breakouts"] += 1

        breakout_age = (
            now_ts()
            - breakout["breakout_time"]
        ) / 3600

        if breakout_age > 48:

            stats[
                "old_breakouts"
            ] += 1

            continue

        retest = find_first_retest(
            df5,
            breakout,
            pattern["direction"]
        )

        if not retest:
            continue

        stats["retests"] += 1

        confirmation = find_confirmation(
            df5,
            retest,
            pattern["direction"]
        )

        if not confirmation:
            continue

        stats[
            "confirmations"
        ] += 1

        entry = find_entry(
            df5,
            confirmation,
            pattern["direction"]
        )

        if not entry:

            stats["stale"] += 1

            continue

        stats["valid"] += 1

        entries.append({
            "asset":
                asset,

            "symbol":
                contract,

            "contract":
                contract,

            "pattern":
                pattern["pattern"],

            "direction":
                pattern["direction"],

            "breakout_time":
                breakout["breakout_time"],

            "retest_time":
                retest["retest_time"],

            "confirm_time":
                confirmation[
                    "confirm_time"
                ],

            "entry_time":
                entry["entry_time"],

            "entry_price":
                entry["entry_price"],

            "sl_price":
                entry["sl_price"],

            "tp_price":
                entry["tp_price"]
        })

    return entries, stats


# ============================================================
# DATABASE
# ============================================================

def get_db():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_key TEXT UNIQUE,
            asset TEXT,
            symbol TEXT,
            contract TEXT,
            pattern TEXT,
            direction TEXT,
            breakout_time INTEGER,
            retest_time INTEGER,
            confirm_time INTEGER,
            entry_time INTEGER,
            entry_price REAL,
            sl_price REAL,
            tp_price REAL,
            exit_time INTEGER,
            exit_price REAL,
            exit_reason TEXT,
            status TEXT,
            detected_at INTEGER,
            created_at INTEGER,
            updated_at INTEGER,
            notified_new INTEGER DEFAULT 0,
            notified_close INTEGER DEFAULT 0
        )
    """)

    conn.commit()

    cur.execute(
        "PRAGMA table_info(trades)"
    )

    existing = {
        row[1]
        for row in cur.fetchall()
    }

    migrations = {

        "contract": """
            ALTER TABLE trades
            ADD COLUMN contract TEXT
        """,

        "exit_reason": """
            ALTER TABLE trades
            ADD COLUMN exit_reason TEXT
        """,

        "created_at": """
            ALTER TABLE trades
            ADD COLUMN created_at INTEGER
        """,

        "updated_at": """
            ALTER TABLE trades
            ADD COLUMN updated_at INTEGER
        """,

        "notified_new": """
            ALTER TABLE trades
            ADD COLUMN notified_new INTEGER
            DEFAULT 0
        """,

        "notified_close": """
            ALTER TABLE trades
            ADD COLUMN notified_close INTEGER
            DEFAULT 0
        """
    }

    for column, sql in migrations.items():

        if column not in existing:

            print(
                f"[DB MIGRATION] "
                f"Adding {column}"
            )

            try:

                cur.execute(sql)
                conn.commit()

            except sqlite3.OperationalError as e:

                print(
                    f"[DB MIGRATION ERROR] "
                    f"{column}: {e}"
                )

    # --------------------------------------------------------
    # BACKFILL
    # --------------------------------------------------------

    cur.execute("""
        UPDATE trades
        SET created_at =
            COALESCE(
                created_at,
                detected_at,
                CAST(
                    strftime(
                        '%s',
                        'now'
                    ) AS INTEGER
                )
            )
        WHERE created_at IS NULL
    """)

    cur.execute("""
        UPDATE trades
        SET updated_at =
            COALESCE(
                updated_at,
                created_at,
                detected_at,
                CAST(
                    strftime(
                        '%s',
                        'now'
                    ) AS INTEGER
                )
            )
        WHERE updated_at IS NULL
    """)

    cur.execute("""
        UPDATE trades
        SET contract = symbol
        WHERE contract IS NULL
    """)

    cur.execute(
        "PRAGMA table_info(trades)"
    )

    final_columns = {
        row[1]
        for row in cur.fetchall()
    }

    required = {
        "signal_key",
        "asset",
        "symbol",
        "contract",
        "pattern",
        "direction",
        "breakout_time",
        "retest_time",
        "confirm_time",
        "entry_time",
        "entry_price",
        "sl_price",
        "tp_price",
        "exit_time",
        "exit_price",
        "exit_reason",
        "status",
        "detected_at",
        "created_at",
        "updated_at",
        "notified_new",
        "notified_close"
    }

    missing = (
        required
        - final_columns
    )

    if missing:

        conn.close()

        raise RuntimeError(
            "Database schema missing: "
            f"{sorted(missing)}"
        )

    conn.commit()
    conn.close()

    print(
        "Database schema verified."
    )


# ============================================================
# INSERT TRADE
# ============================================================

def insert_trade(trade):

    conn = get_db()
    cur = conn.cursor()

    now_value = now_ts()

    created_at = trade.get(
        "created_at",
        now_value
    )

    updated_at = trade.get(
        "updated_at",
        created_at
    )

    if created_at is None:
        created_at = now_value

    if updated_at is None:
        updated_at = created_at

    signal_key = (
        f"{trade['asset']}|"
        f"{trade['pattern']}|"
        f"{trade['direction']}|"
        f"{trade['breakout_time']}|"
        f"{trade['entry_time']}"
    )

    cur.execute("""
        SELECT id
        FROM trades
        WHERE signal_key = ?
        LIMIT 1
    """, (
        signal_key,
    ))

    if cur.fetchone():

        print(
            f"[DUPLICATE DB] "
            f"{signal_key}"
        )

        conn.close()

        return False

    try:

        cur.execute("""
            INSERT INTO trades (
                signal_key,
                asset,
                symbol,
                contract,
                pattern,
                direction,
                breakout_time,
                retest_time,
                confirm_time,
                entry_time,
                entry_price,
                sl_price,
                tp_price,
                exit_time,
                exit_price,
                exit_reason,
                status,
                detected_at,
                created_at,
                updated_at,
                notified_new,
                notified_close
            )
            VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                NULL, NULL, NULL,
                'OPEN',
                ?, ?, ?,
                0, 0
            )
        """, (
            signal_key,
            trade["asset"],
            trade["symbol"],
            trade["contract"],
            trade["pattern"],
            trade["direction"],
            trade["breakout_time"],
            trade["retest_time"],
            trade["confirm_time"],
            trade["entry_time"],
            trade["entry_price"],
            trade["sl_price"],
            trade["tp_price"],
            trade.get(
                "detected_at",
                now_value
            ),
            created_at,
            updated_at
        ))

        conn.commit()

        inserted = (
            cur.rowcount == 1
        )

        if inserted:

            print(
                f"[INSERTED] "
                f"{signal_key}"
            )

        return inserted

    except Exception as e:

        conn.rollback()

        print(
            "[INSERT ERROR]",
            e
        )

        return False

    finally:

        conn.close()


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY entry_time ASC
    """)

    rows = cur.fetchall()

    conn.close()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade_id,
    exit_price,
    exit_reason,
    exit_time=None
):

    if exit_time is None:
        exit_time = now_ts()

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE trades
        SET
            exit_time = ?,
            exit_price = ?,
            exit_reason = ?,
            status = 'CLOSED',
            updated_at = ?
        WHERE
            id = ?
            AND status = 'OPEN'
    """, (
        exit_time,
        exit_price,
        exit_reason,
        exit_time,
        trade_id
    ))

    conn.commit()

    changed = (
        cur.rowcount == 1
    )

    conn.close()

    return changed


# ============================================================
# NOTIFICATION FLAGS
# ============================================================

def mark_new_notified(
    trade_id
):

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE trades
        SET notified_new = 1
        WHERE id = ?
    """, (
        trade_id,
    ))

    conn.commit()
    conn.close()


def mark_close_notified(
    trade_id
):

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE trades
        SET notified_close = 1
        WHERE id = ?
    """, (
        trade_id,
    ))

    conn.commit()
    conn.close()


# ============================================================
# LIVE PRICES
# ============================================================

def get_live_prices():

    data = kraken_get(
        "/derivatives/api/v3/tickers"
    )

    prices = {}

    for ticker in data.get(
        "tickers",
        []
    ):

        symbol = str(
            ticker.get(
                "symbol",
                ""
            )
        )

        last = ticker.get(
            "last"
        )

        if last is None:
            continue

        try:

            prices[symbol] = float(
                last
            )

        except Exception:
            pass

    return prices


# ============================================================
# PRICE FORMAT
# ============================================================

def price_text(value):

    if value is None:
        return "-"

    value = float(value)

    if value >= 1000:
        return f"{value:.2f}"

    if value >= 1:
        return f"{value:.4f}"

    if value >= 0.1:
        return f"{value:.5f}"

    if value >= 0.01:
        return f"{value:.6f}"

    return f"{value:.8f}"


# ============================================================
# PERCENT
# ============================================================

def calc_pct(
    direction,
    entry,
    current
):

    if not entry:
        return 0.0

    if direction == "LONG":

        return (
            (
                current - entry
            )
            / entry
        ) * 100

    return (
        (
            entry - current
        )
        / entry
    ) * 100


# ============================================================
# NEW SIGNAL MESSAGE
# ============================================================

def new_signal_message(
    trade,
    current_price=None
):

    direction = trade[
        "direction"
    ]

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
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

    current = (
        current_price
        if current_price is not None
        else entry
    )

    pct = calc_pct(
        direction,
        entry,
        current
    )

    return (
        "🚨 <b>NEW SIGNAL</b>\n\n"
        f"{emoji} <b>"
        f"{trade['asset']} "
        f"{direction}</b>\n"
        f"📌 Pattern: "
        f"{trade['pattern']}\n"
        f"💰 Entry: "
        f"{price_text(entry)}\n"
        f"💵 Current: "
        f"{price_text(current)} "
        f"({pct:+.2f}%)\n"
        f"🛑 SL: "
        f"{price_text(sl)}\n"
        f"🎯 TP: "
        f"{price_text(tp)}\n"
        f"⚙️ RR: {RR:.2f}\n"
        f"🕐 Entry: "
        f"{fmt_tehran(trade['entry_time'])}"
    )


# ============================================================
# CLOSE MESSAGE
# ============================================================

def close_message(
    trade,
    exit_price
):

    direction = trade[
        "direction"
    ]

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    entry = float(
        trade["entry_price"]
    )

    exit_price = float(
        exit_price
    )

    pct = calc_pct(
        direction,
        entry,
        exit_price
    )

    return (
        "🔔 <b>TRADE CLOSED</b>\n\n"
        f"{emoji} <b>"
        f"{trade['asset']} "
        f"{direction}</b>\n"
        f"📌 Pattern: "
        f"{trade['pattern']}\n"
        f"💰 Entry: "
        f"{price_text(entry)}\n"
        f"🏁 Exit: "
        f"{price_text(exit_price)}\n"
        f"📊 P&L: "
        f"{pct:+.2f}%\n"
        f"📍 Reason: "
        f"{trade['exit_reason']}\n"
        f"🕐 Entry: "
        f"{fmt_tehran(trade['entry_time'])}\n"
        f"🕐 Exit: "
        f"{fmt_tehran(trade['exit_time'])}\n"
        f"⏱ Duration: "
        f"{duration_text("
        f"trade['entry_time'],"
        f"trade['exit_time']"
        f")}"
    )


# ============================================================
# PROCESS NEW SIGNALS
# ============================================================

def process_new_signals(
    entries,
    live_prices
):

    inserted_count = 0
    seen_keys = set()

    for entry in entries:

        signal_key = (
            f"{entry['asset']}|"
            f"{entry['pattern']}|"
            f"{entry['direction']}|"
            f"{entry['breakout_time']}|"
            f"{entry['entry_time']}"
        )

        if signal_key in seen_keys:

            print(
                "[DUPLICATE IN SAME SCAN]",
                signal_key
            )

            continue

        seen_keys.add(
            signal_key
        )

        ts = now_ts()

        entry["detected_at"] = ts
        entry["created_at"] = ts
        entry["updated_at"] = ts

        inserted = insert_trade(
            entry
        )

        if not inserted:
            continue

        inserted_count += 1

        current = live_prices.get(
            entry["contract"],
            entry["entry_price"]
        )

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM trades
            WHERE signal_key = ?
            LIMIT 1
        """, (
            signal_key,
        ))

        row = cur.fetchone()

        conn.close()

        if row:

            trade = dict(row)

            message = (
                new_signal_message(
                    trade,
                    current
                )
            )

            if send_telegram(
                message
            ):

                mark_new_notified(
                    trade["id"]
                )

    return inserted_count


# ============================================================
# LIVE EXIT CHECK
# ============================================================

def process_open_trades(
    live_prices
):

    open_trades = (
        get_open_trades()
    )

    closed_count = 0

    for trade in open_trades:

        contract = (
            trade.get("contract")
            or trade.get("symbol")
        )

        current = live_prices.get(
            contract
        )

        if current is None:
            continue

        direction = trade[
            "direction"
        ]

        sl = float(
            trade["sl_price"]
        )

        tp = float(
            trade["tp_price"]
        )

        exit_reason = None
        exit_price = None

        # ----------------------------------------------------
        # SAME CANDLE / SAME UPDATE:
        # SL FIRST
        # ----------------------------------------------------

        if direction == "LONG":

            if current <= sl:

                exit_price = sl
                exit_reason = "SL"

            elif current >= tp:

                exit_price = tp
                exit_reason = "TP"

        else:

            if current >= sl:

                exit_price = sl
                exit_reason = "SL"

            elif current <= tp:

                exit_price = tp
                exit_reason = "TP"

        # ----------------------------------------------------
        # TIME EXIT
        # ----------------------------------------------------

        if exit_reason is None:

            age_hours = (
                now_ts()
                - int(
                    trade["entry_time"]
                )
            ) / 3600

            if (
                age_hours
                >= MAX_HOLD_HOURS
            ):

                exit_price = current
                exit_reason = "TIME"

        if exit_reason is None:
            continue

        closed = close_trade(
            trade["id"],
            exit_price,
            exit_reason
        )

        if not closed:
            continue

        closed_count += 1

        trade["exit_time"] = now_ts()
        trade["exit_price"] = exit_price
        trade["exit_reason"] = exit_reason

        message = close_message(
            trade,
            exit_price
        )

        if send_telegram(
            message
        ):

            mark_close_notified(
                trade["id"]
            )

    return closed_count


# ============================================================
# HISTORICAL CLOSE RECONCILIATION
# ============================================================

def reconcile_open_trades():

    open_trades = (
        get_open_trades()
    )

    if not open_trades:
        return 0

    closed_count = 0

    for trade in open_trades:

        contract = (
            trade.get("contract")
            or trade.get("symbol")
        )

        if not contract:
            continue

        df5 = fetch_candles(
            contract,
            INTERVAL_5M,
            700
        )

        if df5.empty:
            continue

        entry_time = int(
            trade["entry_time"]
        )

        future = df5[
            df5["timestamp"]
            > entry_time
        ]

        if future.empty:
            continue

        direction = trade[
            "direction"
        ]

        sl = float(
            trade["sl_price"]
        )

        tp = float(
            trade["tp_price"]
        )

        exit_time = None
        exit_price = None
        exit_reason = None

        for _, candle in (
            future.iterrows()
        ):

            high = float(
                candle["high"]
            )

            low = float(
                candle["low"]
            )

            ts = int(
                candle["timestamp"]
            )

            if direction == "LONG":

                hit_sl = (
                    low <= sl
                )

                hit_tp = (
                    high >= tp
                )

            else:

                hit_sl = (
                    high >= sl
                )

                hit_tp = (
                    low <= tp
                )

            # ------------------------------------------------
            # SL FIRST
            # ------------------------------------------------

            if hit_sl:

                exit_time = ts
                exit_price = sl
                exit_reason = "SL"

                break

            if hit_tp:

                exit_time = ts
                exit_price = tp
                exit_reason = "TP"

                break

            if (
                ts - entry_time
                >= MAX_HOLD_HOURS * 3600
            ):

                exit_time = ts
                exit_price = float(
                    candle["close"]
                )
                exit_reason = "TIME"

                break

        if exit_reason is None:
            continue

        closed = close_trade(
            trade["id"],
            exit_price,
            exit_reason,
            exit_time
        )

        if not closed:
            continue

        closed_count += 1

        trade["exit_time"] = exit_time
        trade["exit_price"] = exit_price
        trade["exit_reason"] = exit_reason

        if int(
            trade.get(
                "notified_close",
                0
            ) or 0
        ) == 0:

            if send_telegram(
                close_message(
                    trade,
                    exit_price
                )
            ):

                mark_close_notified(
                    trade["id"]
                )

    return closed_count


# ============================================================
# PERFORMANCE
# ============================================================

def performance():

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(
                CASE
                    WHEN status='CLOSED'
                    THEN 1
                    ELSE 0
                END
            ) AS closed
        FROM trades
    """)

    row = cur.fetchone()

    total = int(
        row["total"] or 0
    )

    closed = int(
        row["closed"] or 0
    )

    cur.execute("""
        SELECT
            direction,
            entry_price,
            exit_price
        FROM trades
        WHERE status='CLOSED'
    """)

    rows = cur.fetchall()

    conn.close()

    wins = 0
    losses = 0
    total_pct = 0.0

    for row in rows:

        entry = float(
            row["entry_price"]
        )

        exit_price = float(
            row["exit_price"]
        )

        pct = calc_pct(
            row["direction"],
            entry,
            exit_price
        )

        total_pct += pct

        if pct > 0:
            wins += 1

        elif pct < 0:
            losses += 1

    winrate = (
        wins / closed * 100
        if closed > 0
        else 0
    )

    return {
        "total": total,
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "winrate": winrate,
        "total_pct": total_pct
    }


# ============================================================
# PERIODIC REPORT
# ============================================================

def periodic_report(
    live_prices
):

    open_trades = (
        get_open_trades()
    )

    perf = performance()

    lines = []

    lines.append(
        "📊 <b>KRAKEN PATTERN SCANNER</b>"
    )

    lines.append("")

    lines.append(
        f"🟢 <b>OPEN TRADES: "
        f"{len(open_trades)}</b>"
    )

    for trade in open_trades:

        direction = trade[
            "direction"
        ]

        emoji = (
            "🟢"
            if direction == "LONG"
            else "🔴"
        )

        contract = (
            trade.get("contract")
            or trade.get("symbol")
        )

        current = live_prices.get(
            contract,
            float(
                trade["entry_price"]
            )
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

        pct = calc_pct(
            direction,
            entry,
            current
        )

        lines.append("")

        lines.append(
            f"{emoji} <b>"
            f"{trade['asset']} "
            f"{direction}</b>"
        )

        lines.append(
            f"📌 Pattern: "
            f"{trade['pattern']}"
        )

        lines.append(
            f"💰 Entry: "
            f"{price_text(entry)}"
        )

        lines.append(
            f"💵 Current: "
            f"{price_text(current)} "
            f"({pct:+.2f}%)"
        )

        lines.append(
            f"🛑 SL: "
            f"{price_text(sl)} "
            f"({calc_pct(direction, entry, sl):+.2f}%)"
        )

        lines.append(
            f"🎯 TP: "
            f"{price_text(tp)} "
            f"({calc_pct(direction, entry, tp):+.2f}%)"
        )

        lines.append(
            f"⚙️ RR: {RR:.2f}"
        )

        lines.append(
            f"⏱ Duration: "
            f"{duration_text(trade['entry_time'])}"
        )

    lines.append("")

    lines.append(
        "📈 <b>PERFORMANCE</b>"
    )

    lines.append(
        f"Trades: {perf['total']}"
    )

    lines.append(
        f"Closed: {perf['closed']}"
    )

    lines.append(
        f"Wins: {perf['wins']}"
    )

    lines.append(
        f"Losses: {perf['losses']}"
    )

    lines.append(
        f"Win Rate: "
        f"{perf['winrate']:.2f}%"
    )

    lines.append(
        f"Total P&L: "
        f"{perf['total_pct']:+.2f}%"
    )

    send_telegram(
        "\n".join(lines)
    )


# ============================================================
# REPORT TIMER
# ============================================================

def should_send_periodic_report():

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS scanner_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    cur.execute("""
        SELECT value
        FROM scanner_meta
        WHERE key = 'last_periodic_report'
    """)

    row = cur.fetchone()

    conn.close()

    if not row:
        return True

    try:

        last = int(
            row["value"]
        )

    except Exception:

        return True

    return (
        now_ts() - last
        >= PERIODIC_REPORT_SECONDS
    )


def mark_periodic_report():

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT OR REPLACE INTO
        scanner_meta (
            key,
            value
        )
        VALUES (
            'last_periodic_report',
            ?
        )
    """, (
        str(now_ts()),
    ))

    conn.commit()
    conn.close()


# ============================================================
# SCAN
# ============================================================

def scan():

    scan_start = time.time()

    print("=" * 70)

    print(
        "KRAKEN FUTURES PATTERN LIVE "
        "SIGNAL SCANNER"
    )

    print(
        f"VERSION {VERSION}"
    )

    print("=" * 70)

    print(
        f"REAL_TRADING = "
        f"{REAL_TRADING}"
    )

    print(
        f"ASSETS = "
        f"{len(ASSETS)}"
    )

    print(
        f"MAX_ENTRY_AGE_CANDLES = "
        f"{MAX_ENTRY_AGE_CANDLES}"
    )

    print(
        f"MAX ENTRY AGE = "
        f"{MAX_ENTRY_AGE_MINUTES} MINUTES"
    )

    print(
        f"PERIODIC_REPORT_SECONDS = "
        f"{PERIODIC_REPORT_SECONDS}"
    )

    if REAL_TRADING:

        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    init_db()

    # --------------------------------------------------------
    # CONTRACT MAP
    # --------------------------------------------------------

    contract_map = (
        build_contract_map()
    )

    all_entries = []

    total_stats = {
        "patterns": 0,
        "recent": 0,
        "breakouts": 0,
        "old_breakouts": 0,
        "retests": 0,
        "confirmations": 0,
        "valid": 0,
        "stale": 0
    }

    # --------------------------------------------------------
    # SCAN ASSETS
    # --------------------------------------------------------

    for asset in ASSETS:

        contract = contract_map.get(
            asset
        )

        if not contract:

            print(
                f"[SKIP] {asset}: "
                f"contract not found"
            )

            continue

        try:

            entries, stats = (
                generate_live_entries(
                    asset,
                    contract
                )
            )

            for key in total_stats:

                total_stats[key] += (
                    stats.get(key, 0)
                )

            print(
                f"{asset:<6} "
                f"patterns="
                f"{stats.get('patterns', 0)} "
                f"recent="
                f"{stats.get('recent', 0)} "
                f"breakouts="
                f"{stats.get('breakouts', 0)} "
                f"old="
                f"{stats.get('old_breakouts', 0)} "
                f"retests="
                f"{stats.get('retests', 0)} "
                f"conf="
                f"{stats.get('confirmations', 0)} "
                f"valid="
                f"{stats.get('valid', 0)}"
            )

            all_entries.extend(
                entries
            )

        except Exception as e:

            print(
                f"[ASSET ERROR] "
                f"{asset}: {e}"
            )

            traceback.print_exc()

        time.sleep(
            REQUEST_SLEEP
        )

    # --------------------------------------------------------
    # UNIQUE ENTRIES
    # --------------------------------------------------------

    unique_entries = []

    seen = set()

    for entry in all_entries:

        key = (
            f"{entry['asset']}|"
            f"{entry['pattern']}|"
            f"{entry['direction']}|"
            f"{entry['breakout_time']}|"
            f"{entry['entry_time']}"
        )

        if key in seen:

            print(
                "[DUPLICATE IN SAME SCAN]",
                key
            )

            continue

        seen.add(key)

        unique_entries.append(
            entry
        )

    print("")

    print(
        f"Patterns: "
        f"{total_stats['patterns']}"
    )

    print(
        f"Recent patterns: "
        f"{total_stats['recent']}"
    )

    print(
        f"Breakouts: "
        f"{total_stats['breakouts']}"
    )

    print(
        f"Old breakouts: "
        f"{total_stats['old_breakouts']}"
    )

    print(
        f"Retests: "
        f"{total_stats['retests']}"
    )

    print(
        f"Confirmations: "
        f"{total_stats['confirmations']}"
    )

    print(
        f"Valid entries: "
        f"{len(all_entries)}"
    )

    print(
        f"Unique entries this scan: "
        f"{len(unique_entries)}"
    )

    # --------------------------------------------------------
    # LIVE PRICES
    # --------------------------------------------------------

    live_prices = (
        get_live_prices()
    )

    # --------------------------------------------------------
    # NEW SIGNALS
    # --------------------------------------------------------

    inserted_count = (
        process_new_signals(
            unique_entries,
            live_prices
        )
    )

    print(
        f"New signals inserted: "
        f"{inserted_count}"
    )

    # --------------------------------------------------------
    # LIVE EXITS
    # --------------------------------------------------------

    closed_count = (
        process_open_trades(
            live_prices
        )
    )

    print(
        f"Trades closed by live price: "
        f"{closed_count}"
    )

    # --------------------------------------------------------
    # HISTORICAL RECONCILIATION
    # --------------------------------------------------------

    reconciled = (
        reconcile_open_trades()
    )

    print(
        f"Historical reconciled closes: "
        f"{reconciled}"
    )

    # --------------------------------------------------------
    # PERIODIC REPORT
    # --------------------------------------------------------

    if should_send_periodic_report():

        periodic_report(
            live_prices
        )

        mark_periodic_report()

    elapsed = (
        time.time()
        - scan_start
    )

    print("")

    print(
        f"Scan completed in "
        f"{elapsed:.1f}s"
    )

    print("=" * 70)


# ============================================================
# MAIN
# ============================================================

def main():

    try:

        scan()

    except Exception as e:

        print(
            "[FATAL ERROR]"
        )

        print(e)

        traceback.print_exc()

        raise


if __name__ == "__main__":

    main()
