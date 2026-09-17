# ============================================================
# KRAKEN FUTURES PATTERN LIVE SIGNAL SCANNER
# VERSION 5.1 - RR 1.0 + CLEAN TELEGRAM REPORT
# ============================================================
#
# STRATEGY:
#
#   1H Pattern
#       ↓
#   1H CLOSED-CANDLE BREAKOUT
#       ↓
#   FIRST 5M RETEST
#       ↓
#   5M CONFIRMATION
#       ↓
#   ENTRY
#
# LIVE MODE:
#
#   RR = 1.0
#   TP = 1.00%
#   SL = 1.00%
#
# IMPORTANT:
# - CLOSED CANDLES ONLY
# - NO LOOKAHEAD
# - FIRST RETEST ONLY
# - CONFIRMATION MUST COME AFTER RETEST
# - ENTRY CANDLE IS NOT USED FOR TP/SL
# - SAME CANDLE TP + SL = SL FIRST
# - PAPER TRADING ONLY
# - REAL TRADING DISABLED
# - TELEGRAM REPORTING
# - SQLITE PERSISTENCE
#
# ============================================================

import os
import time
import sqlite3
import requests
import numpy as np
import pandas as pd

from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com"

# ------------------------------------------------------------
# LIVE SETTINGS
# ------------------------------------------------------------

MAIN_INTERVAL = "1h"
ENTRY_INTERVAL = "5m"

SL_PCT = 0.01
TP_PCT = 0.01
RR = 1.0

# ------------------------------------------------------------
# PAPER TRADING ONLY
# ------------------------------------------------------------

REAL_TRADING = False

# ------------------------------------------------------------
# MAXIMUM TRADE AGE
# ------------------------------------------------------------

MAX_HOLD_HOURS = 48

# ------------------------------------------------------------
# HISTORY
# ------------------------------------------------------------

MAIN_LOOKBACK = 500
ENTRY_LOOKBACK = 1500

# ------------------------------------------------------------
# API
# ------------------------------------------------------------

REQUEST_TIMEOUT = 30
REQUEST_SLEEP = 0.10

CANDLE_CHUNK = 1900

# ------------------------------------------------------------
# DATABASE
# ------------------------------------------------------------

DB_FILE = "kraken_pattern_live.db"

# ------------------------------------------------------------
# TELEGRAM
# ------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)


# ============================================================
# FIXED 20-ASSET UNIVERSE
# ============================================================

FIXED_ASSETS = [
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
    "BCH",
    "UNI",
    "AAVE",
    "ATOM",
    "XLM",
    "ALGO",
    "FIL",
    "ETC",
    "SUI",
    "HBAR",
]

EXPECTED_UNIVERSE_SIZE = len(
    FIXED_ASSETS
)


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

MIN_STRUCTURE_BARS = 12
MAX_STRUCTURE_BARS = 60

FLAG_IMPULSE_LOOKBACK = 35
FLAG_CONSOLIDATION_BARS = 15

FLAG_MIN_IMPULSE = 0.04
FLAG_MAX_RETRACE = 0.55
FLAG_MAX_RANGE_TO_IMPULSE = 0.60
FLAG_MIN_DIRECTIONAL_EFFICIENCY = 0.45

BREAKOUT_BUFFER = 0.0010

RETEST_MAX_BARS = 12
CONFIRM_MAX_BARS = 6

MIN_BODY_RATIO = 0.45
MIN_CLOSE_POSITION = 0.60


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent":
            "Mozilla/5.0 "
            "KrakenPatternLive/5.1"
    }
)


# ============================================================
# GENERAL HELPERS
# ============================================================

def utc_now():

    return datetime.now(
        timezone.utc
    )


def format_time(ts):

    return datetime.fromtimestamp(
        int(ts),
        tz=timezone.utc,
    ).strftime(
        "%Y-%m-%d %H:%M UTC"
    )


def pct_change(
    current,
    reference,
):

    if reference == 0:
        return 0.0

    return (
        current
        / reference
        - 1.0
    ) * 100.0


def pct(a, b):

    if b == 0:
        return 0.0

    return abs(a - b) / abs(b)


def fmt_price(price):

    if price >= 1000:
        return f"{price:.2f}"

    if price >= 100:
        return f"{price:.3f}"

    if price >= 1:
        return f"{price:.4f}"

    if price >= 0.01:
        return f"{price:.6f}"

    return f"{price:.8f}"


# ============================================================
# DIRECTIONAL HELPERS
# ============================================================

def directional_move_pct(
    current,
    entry,
    direction,
):
    """
    Positive = favorable move
    Negative = unfavorable move
    """

    raw = pct_change(
        current,
        entry,
    )

    if direction == "SHORT":
        return -raw

    return raw


def tp_sl_pct_from_entry(
    entry,
    tp,
    sl,
    direction,
):
    """
    Returns TP/SL percentages relative to entry
    with market direction preserved.

    LONG:
        TP +1%
        SL -1%

    SHORT:
        TP -1%
        SL +1%
    """

    if entry == 0:
        return 0.0, 0.0

    tp_pct = (
        (tp - entry)
        / entry
        * 100.0
    )

    sl_pct = (
        (sl - entry)
        / entry
        * 100.0
    )

    return tp_pct, sl_pct


def distance_pct(
    price_a,
    price_b,
):
    """
    Absolute percentage distance.
    """

    if price_b == 0:
        return 0.0

    return (
        abs(
            price_a - price_b
        )
        / price_b
        * 100.0
    )


# ============================================================
# API
# ============================================================

def api_get(
    path,
    params=None,
):

    url = BASE_URL + path

    last_error = None

    for attempt in range(3):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            data = response.json()

            if isinstance(data, dict):

                if data.get(
                    "result"
                ) == "error":

                    raise RuntimeError(
                        str(data)
                    )

                if data.get("error"):

                    raise RuntimeError(
                        str(
                            data["error"]
                        )
                    )

            time.sleep(
                REQUEST_SLEEP
            )

            return data

        except Exception as exc:

            last_error = exc

            if attempt < 2:

                time.sleep(
                    1.0
                    * (attempt + 1)
                )

    raise RuntimeError(
        f"API request failed: "
        f"{path} | {last_error}"
    )


# ============================================================
# INSTRUMENT DISCOVERY
# ============================================================

def discover_instruments():

    data = api_get(
        "/derivatives/api/v3/instruments"
    )

    raw = data.get(
        "instruments",
        []
    )

    if not isinstance(
        raw,
        list,
    ):

        raise RuntimeError(
            "Invalid instruments response"
        )

    return [
        item
        for item in raw
        if isinstance(
            item,
            dict,
        )
    ]


def is_perpetual_instrument(
    item
):

    symbol = str(
        item.get("symbol")
        or item.get("instrument")
        or ""
    ).upper()

    instrument_type = str(
        item.get("type")
        or item.get("instrumentType")
        or ""
    ).upper()

    if "PERPETUAL" in instrument_type:
        return True

    if "PERPETUAL" in symbol:
        return True

    if symbol.startswith("PI_"):
        return True

    if symbol.startswith("PF_"):
        return True

    return False


def choose_contract_for_asset(
    asset,
    instruments,
):

    valid_symbols = {
        f"PF_{asset}USD",
        f"PI_{asset}USD",
    }

    candidates = []

    for item in instruments:

        if not is_perpetual_instrument(
            item
        ):
            continue

        symbol = str(
            item.get("symbol")
            or item.get("instrument")
            or ""
        ).upper().strip()

        if symbol not in valid_symbols:
            continue

        candidates.append(symbol)

    if not candidates:
        return None

    if f"PF_{asset}USD" in candidates:
        return f"PF_{asset}USD"

    if f"PI_{asset}USD" in candidates:
        return f"PI_{asset}USD"

    return candidates[0]


def build_dynamic_universe():

    instruments = discover_instruments()

    mapping = {}

    print()
    print("=" * 70)
    print(
        "KRAKEN FUTURES CONTRACT DISCOVERY"
    )
    print("=" * 70)

    for asset in FIXED_ASSETS:

        contract = choose_contract_for_asset(
            asset,
            instruments,
        )

        mapping[asset] = contract

        print(
            f"{asset:<8} -> "
            f"{contract or 'NO EXACT CONTRACT'}"
        )

    print("=" * 70)

    return mapping


# ============================================================
# CANDLE PARSER
# ============================================================

def parse_candle_response(data):

    rows = None

    if isinstance(data, dict):

        for key in (
            "candles",
            "data",
            "history",
            "result",
        ):

            value = data.get(key)

            if isinstance(
                value,
                list,
            ):

                rows = value
                break

    elif isinstance(
        data,
        list,
    ):

        rows = data

    if not rows:
        return []

    output = []

    for row in rows:

        if isinstance(
            row,
            dict,
        ):

            ts = (
                row.get("time")
                or row.get("timestamp")
                or row.get("ts")
            )

            o = row.get("open")
            h = row.get("high")
            l = row.get("low")
            c = row.get("close")

            v = (
                row.get("volume")
                or row.get("vol")
                or 0
            )

        elif (
            isinstance(
                row,
                (list, tuple),
            )
            and len(row) >= 5
        ):

            ts = row[0]
            o = row[1]
            h = row[2]
            l = row[3]
            c = row[4]

            v = (
                row[5]
                if len(row) > 5
                else 0
            )

        else:

            continue

        try:

            ts = float(ts)

            if ts > 10_000_000_000:
                ts /= 1000.0

            output.append(
                {
                    "timestamp": int(ts),
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": float(v),
                }
            )

        except Exception:

            continue

    return output


# ============================================================
# FETCH RECENT CANDLES
# ============================================================

def fetch_recent_candles(
    symbol,
    interval,
    lookback,
):

    interval_seconds_map = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }

    interval_seconds = (
        interval_seconds_map[
            interval
        ]
    )

    now_ts = int(
        utc_now().timestamp()
    )

    start_ts = (
        now_ts
        - (
            lookback
            + 20
        )
        * interval_seconds
    )

    end_ts = now_ts

    all_rows = []

    cursor = start_ts

    while cursor < end_ts:

        chunk_end = min(
            end_ts,
            cursor
            + CANDLE_CHUNK
            * interval_seconds,
        )

        path = (
            f"/api/charts/v1/trade/"
            f"{symbol}/{interval}"
        )

        try:

            data = api_get(
                path,
                params={
                    "from": cursor,
                    "to": chunk_end,
                },
            )

            rows = parse_candle_response(
                data
            )

            if rows:
                all_rows.extend(rows)

        except Exception as exc:

            print(
                f"  Candle fetch error "
                f"{symbol} {interval}: "
                f"{exc}"
            )

            break

        next_cursor = (
            chunk_end
            + interval_seconds
        )

        if next_cursor <= cursor:
            break

        cursor = next_cursor

    if not all_rows:

        return pd.DataFrame(
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        )

    df = pd.DataFrame(
        all_rows
    )

    df = df.drop_duplicates(
        subset=["timestamp"]
    )

    df = df.sort_values(
        "timestamp"
    ).reset_index(
        drop=True
    )

    now_ts = int(
        utc_now().timestamp()
    )

    df = df[
        df["timestamp"]
        <= now_ts
    ].copy()

    df = df[
        (
            df["timestamp"]
            + interval_seconds
        )
        <= now_ts
    ].copy()

    if len(df) > lookback:

        df = df.tail(
            lookback
        )

    return df.reset_index(
        drop=True
    )


# ============================================================
# CURRENT MARKET PRICE
# ============================================================

def fetch_current_price(
    contract,
):

    try:

        data = api_get(
            "/derivatives/api/v3/tickers"
        )

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

            symbol = str(
                item.get("symbol")
                or item.get("instrument")
                or ""
            ).upper()

            if symbol != contract.upper():
                continue

            for key in (
                "last",
                "lastPrice",
                "price",
            ):

                value = item.get(
                    key
                )

                if value is not None:

                    return float(
                        value
                    )

    except Exception as exc:

        print(
            f"Current price error "
            f"{contract}: {exc}"
        )

    return None


# ============================================================
# DATABASE
# ============================================================

def db_connect():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.row_factory = (
        sqlite3.Row
    )

    return conn


def init_db():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            signal_key TEXT UNIQUE,

            asset TEXT NOT NULL,
            symbol TEXT NOT NULL,
            pattern TEXT NOT NULL,
            direction TEXT NOT NULL,

            pattern_start INTEGER,
            pattern_end INTEGER,
            breakout_time INTEGER,
            retest_time INTEGER,
            entry_time INTEGER,

            entry_price REAL NOT NULL,

            tp_price REAL NOT NULL,
            sl_price REAL NOT NULL,

            current_price REAL,

            status TEXT NOT NULL,
            result TEXT,

            exit_time INTEGER,
            exit_price REAL,

            r_multiple REAL,

            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )

    conn.commit()
    conn.close()


def trade_exists(
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


def insert_trade(
    trade,
):

    conn = db_connect()

    now_ts = int(
        utc_now().timestamp()
    )

    conn.execute(
        """
        INSERT OR IGNORE INTO trades (
            signal_key,
            asset,
            symbol,
            pattern,
            direction,
            pattern_start,
            pattern_end,
            breakout_time,
            retest_time,
            entry_time,
            entry_price,
            tp_price,
            sl_price,
            current_price,
            status,
            result,
            exit_time,
            exit_price,
            r_multiple,
            created_at,
            updated_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            trade["signal_key"],
            trade["asset"],
            trade["symbol"],
            trade["pattern"],
            trade["direction"],
            trade["pattern_start"],
            trade["pattern_end"],
            trade["breakout_time"],
            trade["retest_time"],
            trade["entry_time"],
            trade["entry_price"],
            trade["tp_price"],
            trade["sl_price"],
            trade.get(
                "current_price"
            ),
            "OPEN",
            None,
            None,
            None,
            None,
            now_ts,
            now_ts,
        ),
    )

    conn.commit()
    conn.close()


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

    return [
        dict(row)
        for row in rows
    ]


def get_all_closed_trades():

    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'CLOSED'
        ORDER BY exit_time ASC
        """
    ).fetchall()

    conn.close()

    return [
        dict(row)
        for row in rows
    ]


def update_trade_open_price(
    trade_id,
    current_price,
):

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
            current_price,
            int(
                utc_now().timestamp()
            ),
            trade_id,
        ),
    )

    conn.commit()
    conn.close()


def close_trade(
    trade_id,
    result,
    exit_time,
    exit_price,
    r_multiple,
):

    conn = db_connect()

    conn.execute(
        """
        UPDATE trades
        SET
            status = 'CLOSED',
            result = ?,
            exit_time = ?,
            exit_price = ?,
            r_multiple = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            result,
            exit_time,
            exit_price,
            r_multiple,
            int(
                utc_now().timestamp()
            ),
            trade_id,
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(df):

    highs = []
    lows = []

    h = df["high"].values
    l = df["low"].values

    n = len(df)

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT,
    ):

        left_h = h[
            i - PIVOT_LEFT:i
        ]

        right_h = h[
            i + 1:
            i + 1 + PIVOT_RIGHT
        ]

        left_l = l[
            i - PIVOT_LEFT:i
        ]

        right_l = l[
            i + 1:
            i + 1 + PIVOT_RIGHT
        ]

        if (
            h[i] > left_h.max()
            and h[i] >= right_h.max()
        ):

            highs.append(i)

        if (
            l[i] < left_l.min()
            and l[i] <= right_l.min()
        ):

            lows.append(i)

    return highs, lows


# ============================================================
# PATTERN OBJECT
# ============================================================

def make_pattern(
    pattern_type,
    direction,
    start_i,
    end_i,
    breakout_level,
    upper_level=None,
    lower_level=None,
    anchors=None,
    quality=0.0,
):

    return {
        "pattern": pattern_type,
        "direction": direction,
        "start": int(start_i),
        "end": int(end_i),
        "breakout_level": float(
            breakout_level
        ),
        "upper_level": (
            float(upper_level)
            if upper_level is not None
            else None
        ),
        "lower_level": (
            float(lower_level)
            if lower_level is not None
            else None
        ),
        "anchors": anchors or [],
        "quality": float(
            quality
        ),
        "state": "DETECTED",
    }


# ============================================================
# STRUCTURE OVERLAP
# ============================================================

def structures_overlap(
    a,
    b,
):

    a0 = a["start"]
    a1 = a["end"]

    b0 = b["start"]
    b1 = b["end"]

    overlap_start = max(
        a0,
        b0,
    )

    overlap_end = min(
        a1,
        b1,
    )

    if overlap_end <= overlap_start:
        return False

    overlap = (
        overlap_end
        - overlap_start
    )

    len_a = max(
        1,
        a1 - a0,
    )

    len_b = max(
        1,
        b1 - b0,
    )

    ratio = (
        overlap
        / min(
            len_a,
            len_b,
        )
    )

    return ratio >= 0.70


# ============================================================
# DOUBLE TOP
# ============================================================

def detect_double_top(
    df,
    pivots_high,
):

    patterns = []

    for j in range(
        1,
        len(pivots_high),
    ):

        p1 = pivots_high[
            j - 1
        ]

        p2 = pivots_high[j]

        separation = (
            p2 - p1
        )

        if not (
            DOUBLE_MIN_SEPARATION
            <= separation
            <= DOUBLE_MAX_SEPARATION
        ):
            continue

        h1 = df.iloc[p1]["high"]
        h2 = df.iloc[p2]["high"]

        if pct(
            h1,
            h2,
        ) > DOUBLE_TOLERANCE:
            continue

        between = df.iloc[
            p1:p2 + 1
        ]

        neckline = float(
            between["low"].min()
        )

        if neckline >= min(
            h1,
            h2,
        ):
            continue

        patterns.append(
            make_pattern(
                "Double Top",
                "SHORT",
                p1,
                p2,
                neckline,
                upper_level=max(
                    h1,
                    h2,
                ),
                lower_level=neckline,
                anchors=[
                    p1,
                    p2,
                ],
                quality=(
                    1.0
                    - pct(
                        h1,
                        h2,
                    )
                ),
            )
        )

    return patterns


# ============================================================
# DOUBLE BOTTOM
# ============================================================

def detect_double_bottom(
    df,
    pivots_low,
):

    patterns = []

    for j in range(
        1,
        len(pivots_low),
    ):

        p1 = pivots_low[
            j - 1
        ]

        p2 = pivots_low[j]

        separation = (
            p2 - p1
        )

        if not (
            DOUBLE_MIN_SEPARATION
            <= separation
            <= DOUBLE_MAX_SEPARATION
        ):
            continue

        l1 = df.iloc[p1]["low"]
        l2 = df.iloc[p2]["low"]

        if pct(
            l1,
            l2,
        ) > DOUBLE_TOLERANCE:
            continue

        between = df.iloc[
            p1:p2 + 1
        ]

        neckline = float(
            between["high"].max()
        )

        if neckline <= max(
            l1,
            l2,
        ):
            continue

        patterns.append(
            make_pattern(
                "Double Bottom",
                "LONG",
                p1,
                p2,
                neckline,
                upper_level=neckline,
                lower_level=min(
                    l1,
                    l2,
                ),
                anchors=[
                    p1,
                    p2,
                ],
                quality=(
                    1.0
                    - pct(
                        l1,
                        l2,
                    )
                ),
            )
        )

    return patterns


# ============================================================
# HEAD & SHOULDERS
# ============================================================

def detect_head_shoulders(
    df,
    pivots_high,
    pivots_low,
):

    patterns = []

    for i in range(
        len(pivots_high) - 2
    ):

        ls = pivots_high[i]
        head = pivots_high[
            i + 1
        ]
        rs = pivots_high[
            i + 2
        ]

        if not (
            HS_MIN_SEPARATION
            <= head - ls
            <= HS_MAX_SEPARATION
        ):
            continue

        if not (
            HS_MIN_SEPARATION
            <= rs - head
            <= HS_MAX_SEPARATION
        ):
            continue

        h_ls = df.iloc[
            ls
        ]["high"]

        h_head = df.iloc[
            head
        ]["high"]

        h_rs = df.iloc[
            rs
        ]["high"]

        if (
            h_head <= h_ls
            or h_head <= h_rs
        ):
            continue

        if pct(
            h_ls,
            h_rs,
        ) > 0.05:
            continue

        lows_between_1 = [
            x
            for x in pivots_low
            if ls < x < head
        ]

        lows_between_2 = [
            x
            for x in pivots_low
            if head < x < rs
        ]

        if (
            not lows_between_1
            or not lows_between_2
        ):
            continue

        n1 = min(
            lows_between_1,
            key=lambda x:
            df.iloc[x]["low"],
        )

        n2 = min(
            lows_between_2,
            key=lambda x:
            df.iloc[x]["low"],
        )

        neckline = (
            df.iloc[n1]["low"]
            + df.iloc[n2]["low"]
        ) / 2.0

        patterns.append(
            make_pattern(
                "Head & Shoulders",
                "SHORT",
                ls,
                rs,
                neckline,
                upper_level=h_head,
                lower_level=neckline,
                anchors=[
                    ls,
                    head,
                    rs,
                    n1,
                    n2,
                ],
                quality=(
                    h_head
                    / max(
                        h_ls,
                        h_rs,
                    )
                ),
            )
        )

    return patterns


# ============================================================
# INVERSE HEAD & SHOULDERS
# ============================================================

def detect_inverse_head_shoulders(
    df,
    pivots_high,
    pivots_low,
):

    patterns = []

    for i in range(
        len(pivots_low) - 2
    ):

        ls = pivots_low[i]
        head = pivots_low[
            i + 1
        ]
        rs = pivots_low[
            i + 2
        ]

        if not (
            HS_MIN_SEPARATION
            <= head - ls
            <= HS_MAX_SEPARATION
        ):
            continue

        if not (
            HS_MIN_SEPARATION
            <= rs - head
            <= HS_MAX_SEPARATION
        ):
            continue

        l_ls = df.iloc[
            ls
        ]["low"]

        l_head = df.iloc[
            head
        ]["low"]

        l_rs = df.iloc[
            rs
        ]["low"]

        if (
            l_head >= l_ls
            or l_head >= l_rs
        ):
            continue

        if pct(
            l_ls,
            l_rs,
        ) > 0.05:
            continue

        highs_between_1 = [
            x
            for x in pivots_high
            if ls < x < head
        ]

        highs_between_2 = [
            x
            for x in pivots_high
            if head < x < rs
        ]

        if (
            not highs_between_1
            or not highs_between_2
        ):
            continue

        n1 = max(
            highs_between_1,
            key=lambda x:
            df.iloc[x]["high"],
        )

        n2 = max(
            highs_between_2,
            key=lambda x:
            df.iloc[x]["high"],
        )

        neckline = (
            df.iloc[n1]["high"]
            + df.iloc[n2]["high"]
        ) / 2.0

        patterns.append(
            make_pattern(
                "Inverse H&S",
                "LONG",
                ls,
                rs,
                neckline,
                upper_level=neckline,
                lower_level=l_head,
                anchors=[
                    ls,
                    head,
                    rs,
                    n1,
                    n2,
                ],
                quality=(
                    min(
                        l_ls,
                        l_rs,
                    )
                    / max(
                        l_head,
                        1e-12,
                    )
                ),
            )
        )

    return patterns


# ============================================================
# LINEAR LEVEL
# ============================================================

def linear_level(
    i1,
    p1,
    i2,
    p2,
    x,
):

    if i2 == i1:
        return p2

    slope = (
        p2 - p1
    ) / (
        i2 - i1
    )

    return (
        p1
        + slope
        * (
            x - i1
        )
    )


# ============================================================
# TRIANGLE / WEDGE
# ============================================================

def detect_triangle_wedge(
    df,
    pivots_high,
    pivots_low,
):

    patterns = []

    if len(pivots_high) < 2:
        return patterns

    if len(pivots_low) < 2:
        return patterns

    hi1 = pivots_high[-2]
    hi2 = pivots_high[-1]

    lo1 = pivots_low[-2]
    lo2 = pivots_low[-1]

    start = min(
        hi1,
        lo1,
    )

    end = max(
        hi2,
        lo2,
    )

    bars = end - start

    if not (
        MIN_STRUCTURE_BARS
        <= bars
        <= MAX_STRUCTURE_BARS
    ):
        return patterns

    h1 = df.iloc[
        hi1
    ]["high"]

    h2 = df.iloc[
        hi2
    ]["high"]

    l1 = df.iloc[
        lo1
    ]["low"]

    l2 = df.iloc[
        lo2
    ]["low"]

    upper_now = linear_level(
        hi1,
        h1,
        hi2,
        h2,
        end,
    )

    lower_now = linear_level(
        lo1,
        l1,
        lo2,
        l2,
        end,
    )

    if upper_now <= lower_now:
        return patterns

    upper_slope = (
        h2 - h1
    )

    lower_slope = (
        l2 - l1
    )

    contracting = (
        upper_slope < 0
        and lower_slope > 0
    )

    if contracting:

        patterns.append(
            make_pattern(
                "Triangle",
                "LONG",
                start,
                end,
                upper_now,
                upper_level=upper_now,
                lower_level=lower_now,
                anchors=[
                    hi1,
                    hi2,
                    lo1,
                    lo2,
                ],
                quality=1.0,
            )
        )

        patterns.append(
            make_pattern(
                "Triangle",
                "SHORT",
                start,
                end,
                lower_now,
                upper_level=upper_now,
                lower_level=lower_now,
                anchors=[
                    hi1,
                    hi2,
                    lo1,
                    lo2,
                ],
                quality=1.0,
            )
        )

    rising_wedge = (
        upper_slope > 0
        and lower_slope > 0
        and upper_slope < lower_slope
    )

    if rising_wedge:

        patterns.append(
            make_pattern(
                "Wedge",
                "SHORT",
                start,
                end,
                lower_now,
                upper_level=upper_now,
                lower_level=lower_now,
                anchors=[
                    hi1,
                    hi2,
                    lo1,
                    lo2,
                ],
                quality=1.0,
            )
        )

    falling_wedge = (
        upper_slope < 0
        and lower_slope < 0
        and upper_slope > lower_slope
    )

    if falling_wedge:

        patterns.append(
            make_pattern(
                "Wedge",
                "LONG",
                start,
                end,
                upper_now,
                upper_level=upper_now,
                lower_level=lower_now,
                anchors=[
                    hi1,
                    hi2,
                    lo1,
                    lo2,
                ],
                quality=1.0,
            )
        )

    return patterns


# ============================================================
# FLAG
# ============================================================

def detect_flags(
    df,
    current_i,
):

    patterns = []

    consolidation = (
        FLAG_CONSOLIDATION_BARS
    )

    impulse_lb = (
        FLAG_IMPULSE_LOOKBACK
    )

    if (
        current_i
        < impulse_lb
        + consolidation
    ):
        return patterns

    impulse_start = (
        current_i
        - impulse_lb
    )

    impulse_end = (
        current_i
        - consolidation
    )

    if (
        impulse_end
        <= impulse_start
    ):
        return patterns

    start_price = float(
        df.iloc[
            impulse_start
        ]["close"]
    )

    impulse_end_price = float(
        df.iloc[
            impulse_end
        ]["close"]
    )

    impulse_return = (
        impulse_end_price
        / start_price
    ) - 1.0

    impulse_abs = abs(
        impulse_return
    )

    if (
        impulse_abs
        < FLAG_MIN_IMPULSE
    ):
        return patterns

    impulse_high = float(
        df.iloc[
            impulse_start:
            impulse_end + 1
        ]["high"].max()
    )

    impulse_low = float(
        df.iloc[
            impulse_start:
            impulse_end + 1
        ]["low"].min()
    )

    impulse_range = (
        impulse_high
        - impulse_low
    )

    if impulse_range <= 0:
        return patterns

    consolidation_df = df.iloc[
        impulse_end:
        current_i + 1
    ]

    c_high = float(
        consolidation_df[
            "high"
        ].max()
    )

    c_low = float(
        consolidation_df[
            "low"
        ].min()
    )

    c_range = (
        c_high
        - c_low
    )

    if (
        c_range
        / impulse_range
        > FLAG_MAX_RANGE_TO_IMPULSE
    ):
        return patterns

    closes = (
        consolidation_df[
            "close"
        ].values
    )

    if len(closes) < 2:
        return patterns

    net_move = abs(
        closes[-1]
        - closes[0]
    )

    path = np.abs(
        np.diff(closes)
    ).sum()

    efficiency = (
        net_move / path
        if path > 0
        else 0.0
    )

    if (
        efficiency
        < FLAG_MIN_DIRECTIONAL_EFFICIENCY
    ):
        return patterns

    if impulse_return > 0:

        retrace = (
            impulse_end_price
            - closes[-1]
        ) / impulse_range

        if (
            retrace
            <= FLAG_MAX_RETRACE
        ):

            patterns.append(
                make_pattern(
                    "Flag",
                    "LONG",
                    impulse_start,
                    current_i,
                    c_high,
                    upper_level=c_high,
                    lower_level=c_low,
                    anchors=[
                        impulse_start,
                        impulse_end,
                        current_i,
                    ],
                    quality=efficiency,
                )
            )

    elif impulse_return < 0:

        retrace = (
            closes[-1]
            - impulse_end_price
        ) / impulse_range

        if (
            retrace
            <= FLAG_MAX_RETRACE
        ):

            patterns.append(
                make_pattern(
                    "Flag",
                    "SHORT",
                    impulse_start,
                    current_i,
                    c_low,
                    upper_level=c_high,
                    lower_level=c_low,
                    anchors=[
                        impulse_start,
                        impulse_end,
                        current_i,
                    ],
                    quality=efficiency,
                )
            )

    return patterns


# ============================================================
# BREAKOUT
# ============================================================

def breakout_signal(
    df,
    pattern,
    candle_i,
):

    if candle_i <= pattern["end"]:
        return False

    row = df.iloc[
        candle_i
    ]

    close = float(
        row["close"]
    )

    direction = pattern[
        "direction"
    ]

    if direction == "LONG":

        level = (
            pattern.get(
                "upper_level"
            )
            or pattern[
                "breakout_level"
            ]
        )

        return (
            close
            >
            level
            * (
                1.0
                + BREAKOUT_BUFFER
            )
        )

    level = (
        pattern.get(
            "lower_level"
        )
        or pattern[
            "breakout_level"
        ]
    )

    return (
        close
        <
        level
        * (
            1.0
            - BREAKOUT_BUFFER
        )
    )


# ============================================================
# CONFIRMATION
# ============================================================

def confirmation_ok(
    row,
    direction,
):

    o = float(
        row["open"]
    )

    h = float(
        row["high"]
    )

    l = float(
        row["low"]
    )

    c = float(
        row["close"]
    )

    candle_range = (
        h - l
    )

    if candle_range <= 0:
        return False

    body = abs(
        c - o
    )

    body_ratio = (
        body
        / candle_range
    )

    if (
        body_ratio
        < MIN_BODY_RATIO
    ):
        return False

    if direction == "LONG":

        close_position = (
            c - l
        ) / candle_range

        if (
            close_position
            < MIN_CLOSE_POSITION
        ):
            return False

        return c > o

    close_position = (
        h - c
    ) / candle_range

    if (
        close_position
        < MIN_CLOSE_POSITION
    ):
        return False

    return c < o


# ============================================================
# FIRST RETEST + CONFIRMATION
# ============================================================

def find_entry(
    df_5m,
    breakout_time,
    direction,
    level,
):

    future = df_5m[
        df_5m["timestamp"]
        > breakout_time
    ].copy()

    if future.empty:
        return None

    future = future.head(
        RETEST_MAX_BARS
        + CONFIRM_MAX_BARS
        + 5
    )

    retest_pos = None

    for pos, (
        _,
        row,
    ) in enumerate(
        future.iterrows()
    ):

        h = float(
            row["high"]
        )

        l = float(
            row["low"]
        )

        c = float(
            row["close"]
        )

        touched = (
            l <= level <= h
        )

        if not touched:
            continue

        if direction == "LONG":

            if c >= level:

                retest_pos = pos
                break

        else:

            if c <= level:

                retest_pos = pos
                break

    if retest_pos is None:
        return None

    confirmation_slice = (
        future.iloc[
            retest_pos + 1:
            retest_pos
            + 1
            + CONFIRM_MAX_BARS
        ]
    )

    if confirmation_slice.empty:
        return None

    for _, row in (
        confirmation_slice.iterrows()
    ):

        if not confirmation_ok(
            row,
            direction,
        ):
            continue

        return {
            "entry_time": int(
                row["timestamp"]
            ),
            "entry_price": float(
                row["close"]
            ),
            "retest_time": int(
                future.iloc[
                    retest_pos
                ]["timestamp"]
            ),
            "retest_price": float(
                future.iloc[
                    retest_pos
                ]["close"]
            ),
        }

    return None


# ============================================================
# PATTERN ENGINE
# ============================================================

def detect_patterns(df):

    pivots_high, pivots_low = (
        find_pivots(df)
    )

    patterns = []

    patterns.extend(
        detect_double_top(
            df,
            pivots_high,
        )
    )

    patterns.extend(
        detect_double_bottom(
            df,
            pivots_low,
        )
    )

    patterns.extend(
        detect_head_shoulders(
            df,
            pivots_high,
            pivots_low,
        )
    )

    patterns.extend(
        detect_inverse_head_shoulders(
            df,
            pivots_high,
            pivots_low,
        )
    )

    patterns.extend(
        detect_triangle_wedge(
            df,
            pivots_high,
            pivots_low,
        )
    )

    start_flag = (
        FLAG_IMPULSE_LOOKBACK
        + FLAG_CONSOLIDATION_BARS
    )

    for i in range(
        start_flag,
        len(df) - 1,
    ):

        patterns.extend(
            detect_flags(
                df,
                i,
            )
        )

    unique = {}

    for p in patterns:

        key = (
            p["pattern"],
            p["direction"],
            p["start"],
            p["end"],
            round(
                p[
                    "breakout_level"
                ],
                10,
            ),
        )

        unique[key] = p

    patterns = list(
        unique.values()
    )

    patterns.sort(
        key=lambda x: (
            x["end"],
            x["start"],
        )
    )

    accepted = []

    for p in patterns:

        duplicate = False

        for old in accepted:

            if (
                p["pattern"]
                != old["pattern"]
            ):
                continue

            if (
                p["direction"]
                != old["direction"]
            ):
                continue

            if structures_overlap(
                p,
                old,
            ):

                if (
                    p["quality"]
                    <= old["quality"]
                ):

                    duplicate = True

                else:

                    try:
                        accepted.remove(
                            old
                        )
                    except ValueError:
                        pass

                break

        if not duplicate:

            accepted.append(p)

    return accepted


# ============================================================
# SIGNAL GENERATION
# ============================================================

def generate_live_entries(
    asset,
    contract,
    df_1h,
    df_5m,
):

    patterns = detect_patterns(
        df_1h
    )

    if not patterns:
        return []

    entries = []

    consumed_events = set()

    recent_start_ts = int(
        df_1h.iloc[
            max(
                0,
                len(df_1h) - 180
            )
        ]["timestamp"]
    )

    for p in patterns:

        pattern_end_ts = int(
            df_1h.iloc[
                p["end"]
            ]["timestamp"]
        )

        if (
            pattern_end_ts
            < recent_start_ts
        ):
            continue

        breakout_i = None

        search_end = min(
            len(df_1h),
            p["end"] + 200,
        )

        for i in range(
            p["end"] + 1,
            search_end,
        ):

            if not breakout_signal(
                df_1h,
                p,
                i,
            ):
                continue

            breakout_time = int(
                df_1h.iloc[
                    i
                ]["timestamp"]
            )

            latest_5m_ts = int(
                df_5m.iloc[-1][
                    "timestamp"
                ]
            )

            max_retest_seconds = (
                (
                    RETEST_MAX_BARS
                    + CONFIRM_MAX_BARS
                    + 5
                )
                * 300
            )

            if (
                breakout_time
                <
                latest_5m_ts
                - max_retest_seconds
            ):
                continue

            event_key = (
                asset,
                i,
                p["direction"],
            )

            if event_key in (
                consumed_events
            ):
                continue

            consumed_events.add(
                event_key
            )

            breakout_i = i

            break

        if breakout_i is None:
            continue

        breakout_time = int(
            df_1h.iloc[
                breakout_i
            ]["timestamp"]
        )

        if (
            p["direction"]
            == "LONG"
        ):

            level = (
                p.get(
                    "upper_level"
                )
                or p[
                    "breakout_level"
                ]
            )

        else:

            level = (
                p.get(
                    "lower_level"
                )
                or p[
                    "breakout_level"
                ]
            )

        entry = find_entry(
            df_5m,
            breakout_time,
            p["direction"],
            level,
        )

        if entry is None:
            continue

        signal_key = (
            f"{asset}|"
            f"{p['pattern']}|"
            f"{p['direction']}|"
            f"{breakout_time}|"
            f"{entry['entry_time']}"
        )

        entries.append(
            {
                "signal_key": signal_key,
                "asset": asset,
                "symbol": contract,
                "pattern": p[
                    "pattern"
                ],
                "direction": p[
                    "direction"
                ],
                "pattern_start": int(
                    df_1h.iloc[
                        p["start"]
                    ]["timestamp"]
                ),
                "pattern_end": int(
                    df_1h.iloc[
                        p["end"]
                    ]["timestamp"]
                ),
                "breakout_time":
                    breakout_time,
                "retest_time":
                    entry[
                        "retest_time"
                    ],
                "entry_time":
                    entry[
                        "entry_time"
                    ],
                "entry_price":
                    entry[
                        "entry_price"
                    ],
                "tp_price": (
                    entry[
                        "entry_price"
                    ]
                    * (
                        1.0
                        + TP_PCT
                    )
                    if p[
                        "direction"
                    ] == "LONG"
                    else
                    entry[
                        "entry_price"
                    ]
                    * (
                        1.0
                        - TP_PCT
                    )
                ),
                "sl_price": (
                    entry[
                        "entry_price"
                    ]
                    * (
                        1.0
                        - SL_PCT
                    )
                    if p[
                        "direction"
                    ] == "LONG"
                    else
                    entry[
                        "entry_price"
                    ]
                    * (
                        1.0
                        + SL_PCT
                    )
                ),
            }
        )

    return entries


# ============================================================
# PROCESS OPEN TRADES
# ============================================================

def process_open_trades():

    open_trades = get_open_trades()

    if not open_trades:
        return []

    closed_now = []

    for trade in open_trades:

        current_price = (
            fetch_current_price(
                trade["symbol"]
            )
        )

        if current_price is None:
            continue

        update_trade_open_price(
            trade["id"],
            current_price,
        )

        direction = trade[
            "direction"
        ]

        tp_price = float(
            trade["tp_price"]
        )

        sl_price = float(
            trade["sl_price"]
        )

        entry_time = int(
            trade["entry_time"]
        )

        hit_tp = False
        hit_sl = False

        if direction == "LONG":

            if current_price >= tp_price:
                hit_tp = True

            if current_price <= sl_price:
                hit_sl = True

        else:

            if current_price <= tp_price:
                hit_tp = True

            if current_price >= sl_price:
                hit_sl = True

        # ----------------------------------------------------
        # SAME PRICE:
        # SL FIRST
        # ----------------------------------------------------

        if hit_tp and hit_sl:

            close_trade(
                trade["id"],
                "FAILURE",
                int(
                    utc_now().timestamp()
                ),
                sl_price,
                -1.0,
            )

            closed_now.append(
                (
                    trade,
                    "FAILURE",
                    sl_price,
                    -1.0,
                )
            )

            continue

        if hit_sl:

            close_trade(
                trade["id"],
                "FAILURE",
                int(
                    utc_now().timestamp()
                ),
                sl_price,
                -1.0,
            )

            closed_now.append(
                (
                    trade,
                    "FAILURE",
                    sl_price,
                    -1.0,
                )
            )

            continue

        if hit_tp:

            close_trade(
                trade["id"],
                "SUCCESS",
                int(
                    utc_now().timestamp()
                ),
                tp_price,
                RR,
            )

            closed_now.append(
                (
                    trade,
                    "SUCCESS",
                    tp_price,
                    RR,
                )
            )

            continue

        age_seconds = (
            int(
                utc_now().timestamp()
            )
            - entry_time
        )

        if (
            age_seconds
            >= MAX_HOLD_HOURS * 3600
        ):

            close_trade(
                trade["id"],
                "TIME_EXIT",
                int(
                    utc_now().timestamp()
                ),
                current_price,
                0.0,
            )

            closed_now.append(
                (
                    trade,
                    "TIME_EXIT",
                    current_price,
                    0.0,
                )
            )

    return closed_now


# ============================================================
# HISTORICAL CLOSED 5M CHECK FOR OPEN TRADES
# ============================================================

def resolve_open_trade_from_candles(
    trade,
    df_5m,
):

    entry_time = int(
        trade["entry_time"]
    )

    future = df_5m[
        df_5m["timestamp"]
        > entry_time
    ].copy()

    if future.empty:
        return None

    max_hold_seconds = (
        MAX_HOLD_HOURS
        * 3600
    )

    future = future[
        future["timestamp"]
        <=
        entry_time
        + max_hold_seconds
    ]

    if future.empty:
        return None

    tp_price = float(
        trade["tp_price"]
    )

    sl_price = float(
        trade["sl_price"]
    )

    direction = trade[
        "direction"
    ]

    for _, row in (
        future.iterrows()
    ):

        high = float(
            row["high"]
        )

        low = float(
            row["low"]
        )

        if direction == "LONG":

            hit_tp = (
                high >= tp_price
            )

            hit_sl = (
                low <= sl_price
            )

        else:

            hit_tp = (
                low <= tp_price
            )

            hit_sl = (
                high >= sl_price
            )

        if hit_tp and hit_sl:

            return {
                "result": "FAILURE",
                "exit_time": int(
                    row["timestamp"]
                ),
                "exit_price":
                    sl_price,
                "r_multiple": -1.0,
            }

        if hit_sl:

            return {
                "result": "FAILURE",
                "exit_time": int(
                    row["timestamp"]
                ),
                "exit_price":
                    sl_price,
                "r_multiple": -1.0,
            }

        if hit_tp:

            return {
                "result": "SUCCESS",
                "exit_time": int(
                    row["timestamp"]
                ),
                "exit_price":
                    tp_price,
                "r_multiple": RR,
            }

    return None


# ============================================================
# UPDATE ALL OPEN TRADES USING 5M CANDLES
# ============================================================

def update_open_trades_from_history(
    contracts,
):

    open_trades = get_open_trades()

    if not open_trades:
        return []

    closed_now = []

    grouped = {}

    for trade in open_trades:

        grouped.setdefault(
            trade["symbol"],
            []
        ).append(trade)

    for symbol, trades in (
        grouped.items()
    ):

        df_5m = fetch_recent_candles(
            symbol,
            ENTRY_INTERVAL,
            ENTRY_LOOKBACK,
        )

        if df_5m.empty:
            continue

        for trade in trades:

            result = (
                resolve_open_trade_from_candles(
                    trade,
                    df_5m,
                )
            )

            if result is None:
                continue

            close_trade(
                trade["id"],
                result["result"],
                result["exit_time"],
                result["exit_price"],
                result["r_multiple"],
            )

            closed_now.append(
                (
                    trade,
                    result["result"],
                    result["exit_price"],
                    result["r_multiple"],
                )
            )

    return closed_now


# ============================================================
# AGGREGATE STATISTICS
# ============================================================

def aggregate_stats():

    conn = db_connect()

    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(
                CASE
                    WHEN result = 'SUCCESS'
                    THEN 1 ELSE 0
                END
            ) AS wins,
            SUM(
                CASE
                    WHEN result = 'FAILURE'
                    THEN 1 ELSE 0
                END
            ) AS losses,
            SUM(
                CASE
                    WHEN result = 'TIME_EXIT'
                    THEN 1 ELSE 0
                END
            ) AS time_exits,
            COALESCE(
                SUM(r_multiple),
                0
            ) AS net_r
        FROM trades
        WHERE status = 'CLOSED'
        """
    ).fetchone()

    conn.close()

    total = int(
        row["total"] or 0
    )

    wins = int(
        row["wins"] or 0
    )

    losses = int(
        row["losses"] or 0
    )

    time_exits = int(
        row["time_exits"] or 0
    )

    net_r = float(
        row["net_r"] or 0.0
    )

    # WR is based on decided TP/SL trades.
    decided = (
        wins
        + losses
    )

    wr = (
        wins / decided * 100
        if decided
        else 0.0
    )

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "time_exits": time_exits,
        "decided": decided,
        "wr": wr,
        "net_r": net_r,
    }


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(
    text,
):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "Telegram token not configured."
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "Telegram chat ID not configured."
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/"
        "sendMessage"
    )

    payload = {
        "chat_id":
            TELEGRAM_CHAT_ID,

        "text":
            text,

        "parse_mode":
            "HTML",

        "disable_web_page_preview":
            True,
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        print(
            "Telegram response:",
            response.status_code,
        )

        response.raise_for_status()

        return True

    except Exception as exc:

        print(
            f"Telegram error: {exc}"
        )

        return False


# ============================================================
# FORMAT NEW SIGNAL
# ============================================================

def format_signal(
    trade,
    current_price,
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

    tp = float(
        trade["tp_price"]
    )

    sl = float(
        trade["sl_price"]
    )

    current = (
        current_price
        if current_price is not None
        else entry
    )

    move_pct = directional_move_pct(
        current,
        entry,
        direction,
    )

    tp_pct, sl_pct = (
        tp_sl_pct_from_entry(
            entry,
            tp,
            sl,
            direction,
        )
    )

    current_to_tp = distance_pct(
        tp,
        current,
    )

    current_to_sl = distance_pct(
        sl,
        current,
    )

    return (
        f"{emoji} <b>{trade['asset']} "
        f"{direction}</b>\n"
        f"Pattern: {trade['pattern']}\n"
        f"Entry: <code>{fmt_price(entry)}</code>\n"
        f"Current: <code>{fmt_price(current)}</code> "
        f"({move_pct:+.2f}%)\n"
        f"TP: <code>{fmt_price(tp)}</code> "
        f"({tp_pct:+.2f}%)\n"
        f"SL: <code>{fmt_price(sl)}</code> "
        f"({sl_pct:+.2f}%)\n"
        f"To TP: {current_to_tp:.2f}%  |  "
        f"To SL: {current_to_sl:.2f}%\n"
        f"RR: {RR:.2f}  |  "
        f"Entry: {format_time(trade['entry_time'])}"
    )


# ============================================================
# FORMAT OPEN TRADES
# ============================================================

def format_open_trades(
    trades,
    prices,
):

    if not trades:

        return (
            "<b>📂 OPEN TRADES</b>\n"
            "None"
        )

    lines = [
        "<b>📂 OPEN TRADES</b>"
    ]

    for trade in trades:

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

        tp = float(
            trade["tp_price"]
        )

        sl = float(
            trade["sl_price"]
        )

        current = prices.get(
            trade["symbol"]
        )

        if current is None:
            current = entry

        move_pct = directional_move_pct(
            current,
            entry,
            direction,
        )

        tp_pct, sl_pct = (
            tp_sl_pct_from_entry(
                entry,
                tp,
                sl,
                direction,
            )
        )

        to_tp = distance_pct(
            tp,
            current,
        )

        to_sl = distance_pct(
            sl,
            current,
        )

        lines.append("")
        lines.append(
            f"{emoji} <b>"
            f"{trade['asset']} "
            f"{direction}</b>"
        )

        lines.append(
            f"Entry "
            f"<code>{fmt_price(entry)}</code>  "
            f"Current "
            f"<code>{fmt_price(current)}</code> "
            f"({move_pct:+.2f}%)"
        )

        lines.append(
            f"TP "
            f"<code>{fmt_price(tp)}</code> "
            f"({tp_pct:+.2f}%)  |  "
            f"SL "
            f"<code>{fmt_price(sl)}</code> "
            f"({sl_pct:+.2f}%)"
        )

        lines.append(
            f"To TP {to_tp:.2f}%  |  "
            f"To SL {to_sl:.2f}%"
        )

        lines.append(
            f"Pattern: {trade['pattern']}"
        )

    return "\n".join(
        lines
    )


# ============================================================
# FORMAT CLOSED RESULTS
# ============================================================

def format_closed_results(
    closed_now,
):

    if not closed_now:
        return ""

    lines = [
        "<b>🏁 CLOSED THIS RUN</b>"
    ]

    for (
        trade,
        result,
        exit_price,
        r,
    ) in closed_now:

        if result == "SUCCESS":

            emoji = "✅"
            label = "TP"

        elif result == "FAILURE":

            emoji = "❌"
            label = "SL"

        else:

            emoji = "⏱"
            label = "TIME"

        lines.append(
            f"{emoji} "
            f"<b>{trade['asset']} "
            f"{trade['direction']}</b> "
            f"{label} "
            f"({r:+.2f}R)"
        )

    return "\n".join(
        lines
    )


# ============================================================
# FORMAT AGGREGATE
# ============================================================

def format_stats(
    stats,
):

    return (
        "<b>📈 PERFORMANCE</b>\n"
        f"Closed: {stats['total']}\n"
        f"Wins: {stats['wins']}  |  "
        f"Losses: {stats['losses']}\n"
        f"Time Exit: {stats['time_exits']}\n"
        f"Win Rate: {stats['wr']:.2f}%\n"
        f"Net R: {stats['net_r']:+.2f}R"
    )


# ============================================================
# FILTER NEW SIGNALS THAT CLOSED THIS RUN
# ============================================================

def filter_new_trades_after_closure(
    new_trades,
    closed_now,
):

    if not new_trades:
        return []

    closed_keys = {
        trade["signal_key"]
        for (
            trade,
            _,
            _,
            _,
        ) in closed_now
    }

    return [
        trade
        for trade in new_trades
        if trade["signal_key"]
        not in closed_keys
    ]


# ============================================================
# SEND FULL TELEGRAM REPORT
# ============================================================

def send_report(
    new_trades,
    open_trades,
    prices,
    closed_now,
):

    # --------------------------------------------------------
    # IMPORTANT:
    # A trade opened and closed during the same run
    # belongs only in CLOSED THIS RUN.
    # --------------------------------------------------------

    new_trades = (
        filter_new_trades_after_closure(
            new_trades,
            closed_now,
        )
    )

    parts = []

    # --------------------------------------------------------
    # HEADER
    # --------------------------------------------------------

    parts.append(
        "<b>📊 KRAKEN PATTERN SCANNER</b>\n"
        f"🕐 {utc_now().strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"⚙️ TP {TP_PCT * 100:.2f}%  |  "
        f"SL {SL_PCT * 100:.2f}%  |  "
        f"RR {RR:.2f}\n"
        f"🧪 PAPER TRADING"
    )

    # --------------------------------------------------------
    # NEW SIGNALS
    # --------------------------------------------------------

    if new_trades:

        signal_lines = [
            "<b>🚨 NEW SIGNALS</b>"
        ]

        for trade in new_trades:

            current = prices.get(
                trade["symbol"]
            )

            signal_lines.append(
                format_signal(
                    trade,
                    current,
                )
            )

        parts.append(
            "\n\n".join(
                signal_lines
            )
        )

    else:

        parts.append(
            "<b>🚨 NEW SIGNALS</b>\n"
            "None"
        )

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    parts.append(
        format_open_trades(
            open_trades,
            prices,
        )
    )

    # --------------------------------------------------------
    # CLOSED THIS RUN
    # --------------------------------------------------------

    closed_text = (
        format_closed_results(
            closed_now
        )
    )

    if closed_text:

        parts.append(
            closed_text
        )

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    stats = aggregate_stats()

    parts.append(
        format_stats(
            stats
        )
    )

    text = "\n\n".join(
        parts
    )

    # --------------------------------------------------------
    # TELEGRAM LIMIT
    # --------------------------------------------------------

    if len(text) <= 3900:

        telegram_send(
            text
        )

        return

    # --------------------------------------------------------
    # SAFE SPLIT
    # --------------------------------------------------------

    chunks = []

    current_chunk = ""

    for block in text.split(
        "\n\n"
    ):

        if (
            len(
                current_chunk
            )
            + len(block)
            + 2
            > 3800
        ):

            if current_chunk:

                chunks.append(
                    current_chunk
                )

            current_chunk = block

        else:

            if current_chunk:

                current_chunk += (
                    "\n\n"
                    + block
                )

            else:

                current_chunk = block

    if current_chunk:

        chunks.append(
            current_chunk
        )

    for chunk in chunks:

        telegram_send(
            chunk
        )


# ============================================================
# MAIN LIVE SCAN
# ============================================================

def main():

    print("=" * 70)
    print(
        "KRAKEN FUTURES PATTERN LIVE SCANNER"
    )
    print(
        "VERSION 5.1 - RR 1.0"
    )
    print("=" * 70)

    print(
        "Real trading: DISABLED"
    )

    print(
        "TP: 1.00%"
    )

    print(
        "SL: 1.00%"
    )

    print(
        "RR: 1.00"
    )

    print("=" * 70)

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    init_db()

    # --------------------------------------------------------
    # CONTRACT DISCOVERY
    # --------------------------------------------------------

    universe = (
        build_dynamic_universe()
    )

    # --------------------------------------------------------
    # FIRST:
    # Resolve existing open trades.
    # --------------------------------------------------------

    print()
    print(
        "CHECKING OPEN TRADES"
    )

    closed_from_history = (
        update_open_trades_from_history(
            universe
        )
    )

    # --------------------------------------------------------
    # CURRENT PRICES
    # --------------------------------------------------------

    prices = {}

    for asset in FIXED_ASSETS:

        contract = universe.get(
            asset
        )

        if not contract:
            continue

        price = (
            fetch_current_price(
                contract
            )
        )

        if price is not None:

            prices[
                contract
            ] = price

    # --------------------------------------------------------
    # LIVE SIGNAL SCAN
    # --------------------------------------------------------

    new_trades = []

    print()
    print(
        "SCANNING FOR NEW SIGNALS"
    )

    for asset in FIXED_ASSETS:

        contract = universe.get(
            asset
        )

        if not contract:
            continue

        try:

            print()
            print(
                f"{asset} "
                f"({contract})"
            )

            df_1h = (
                fetch_recent_candles(
                    contract,
                    MAIN_INTERVAL,
                    MAIN_LOOKBACK,
                )
            )

            df_5m = (
                fetch_recent_candles(
                    contract,
                    ENTRY_INTERVAL,
                    ENTRY_LOOKBACK,
                )
            )

            if df_1h.empty:

                print(
                    f"{asset}: "
                    f"NO 1H DATA"
                )

                continue

            if df_5m.empty:

                print(
                    f"{asset}: "
                    f"NO 5M DATA"
                )

                continue

            print(
                f"{asset}: "
                f"1H={len(df_1h)} "
                f"5M={len(df_5m)}"
            )

            entries = (
                generate_live_entries(
                    asset,
                    contract,
                    df_1h,
                    df_5m,
                )
            )

            print(
                f"{asset}: "
                f"candidate entries="
                f"{len(entries)}"
            )

            for trade in entries:

                if trade_exists(
                    trade["signal_key"]
                ):

                    continue

                insert_trade(
                    trade
                )

                new_trades.append(
                    trade
                )

                print(
                    f"NEW SIGNAL: "
                    f"{asset} "
                    f"{trade['direction']} "
                    f"{trade['pattern']} "
                    f"Entry="
                    f"{trade['entry_price']}"
                )

        except Exception as exc:

            print(
                f"{asset}: ERROR -> "
                f"{exc}"
            )

    # --------------------------------------------------------
    # CHECK NEWLY CREATED OPEN TRADES
    # --------------------------------------------------------

    open_trades = (
        get_open_trades()
    )

    # --------------------------------------------------------
    # REFRESH CURRENT PRICES FOR OPEN
    # --------------------------------------------------------

    for trade in open_trades:

        if (
            trade["symbol"]
            not in prices
        ):

            price = (
                fetch_current_price(
                    trade["symbol"]
                )
            )

            if price is not None:

                prices[
                    trade["symbol"]
                ] = price

    # --------------------------------------------------------
    # PROCESS CURRENT PRICE
    # --------------------------------------------------------

    closed_from_price = (
        process_open_trades()
    )

    # --------------------------------------------------------
    # COMBINE CLOSED TRADES
    # --------------------------------------------------------

    closed_now = (
        closed_from_history
        + closed_from_price
    )

    # --------------------------------------------------------
    # IMPORTANT:
    # Refresh prices for trades that remain OPEN.
    # --------------------------------------------------------

    open_trades = (
        get_open_trades()
    )

    for trade in open_trades:

        if (
            trade["symbol"]
            not in prices
        ):

            price = (
                fetch_current_price(
                    trade["symbol"]
                )
            )

            if price is not None:

                prices[
                    trade["symbol"]
                ] = price

    # --------------------------------------------------------
    # REMOVE SAME-RUN CLOSED SIGNALS FROM NEW SIGNALS
    # --------------------------------------------------------

    new_trades = (
        filter_new_trades_after_closure(
            new_trades,
            closed_now,
        )
    )

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    send_report(
        new_trades,
        open_trades,
        prices,
        closed_now,
    )

    # --------------------------------------------------------
    # CONSOLE SUMMARY
    # --------------------------------------------------------

    stats = aggregate_stats()

    print()
    print("=" * 70)
    print(
        "LIVE SCAN COMPLETE"
    )
    print("=" * 70)

    print(
        f"New Signals: "
        f"{len(new_trades)}"
    )

    print(
        f"Open Trades: "
        f"{len(open_trades)}"
    )

    print(
        f"Closed This Run: "
        f"{len(closed_now)}"
    )

    print(
        f"Total Trades: "
        f"{stats['total']}"
    )

    print(
        f"Wins: "
        f"{stats['wins']}"
    )

    print(
        f"Losses: "
        f"{stats['losses']}"
    )

    print(
        f"Time Exits: "
        f"{stats['time_exits']}"
    )

    print(
        f"Win Rate: "
        f"{stats['wr']:.2f}%"
    )

    print(
        f"Net R: "
        f"{stats['net_r']:+.2f}R"
    )

    print(
        "Real trading: DISABLED"
    )

    print("=" * 70)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
