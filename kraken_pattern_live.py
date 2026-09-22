# ============================================================
# KRAKEN FUTURES TRENDLINE LIVE SIGNAL SCANNER
# VERSION 7.2.3
# ============================================================
#
# PAPER ONLY
# REAL_TRADING = False
#
# STRATEGY
#
# LONG:
#   1H descending resistance trendline
#   minimum 3 confirmed touch points
#        ↓
#   1H breakout ABOVE trendline
#        ↓
#   15M descending resistance trendline
#   minimum 3 confirmed touch points
#        ↓
#   15M breakout ABOVE trendline
#        ↓
#   RVOL confirmation
#        ↓
#   LONG SIGNAL
#
# SHORT:
#   1H ascending support trendline
#   minimum 3 confirmed touch points
#        ↓
#   1H breakout BELOW trendline
#        ↓
#   15M ascending support trendline
#   minimum 3 confirmed touch points
#        ↓
#   15M breakout BELOW trendline
#        ↓
#   RVOL confirmation
#        ↓
#   SHORT SIGNAL
#
# IMPORTANT:
#   - Closed candles only
#   - No lookahead
#   - Minimum 3 trendline touches
#   - Existing DB preserved
#   - PAPER ONLY
# ============================================================

import os
import io
import json
import sqlite3
import traceback
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd
import matplotlib.pyplot as plt


# ============================================================
# VERSION
# ============================================================

VERSION = "7.2.3"


# ============================================================
# MODE
# ============================================================

REAL_TRADING = False
PAPER_TRADING = True


# ============================================================
# DATABASE
# ============================================================

DB_FILE = "kraken_pattern_live_v52.db"


# ============================================================
# KRAKEN
# ============================================================

KRAKEN_TICKER_URL = (
    "https://futures.kraken.com/derivatives/api/v3/tickers"
)

KRAKEN_CHART_URL = (
    "https://futures.kraken.com/api/charts/v1/trade"
)


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
# CONTRACT MAP
# ============================================================

CONTRACT_MAP = {
    asset: f"PF_{asset}USD"
    for asset in ASSETS
}


def contract_for_asset(asset):
    return CONTRACT_MAP.get(
        asset,
        f"PF_{asset}USD"
    )


# ============================================================
# TIMEZONE
# ============================================================

TEHRAN_TZ = timezone(
    timedelta(
        hours=3,
        minutes=30,
    )
)


# ============================================================
# STRATEGY
# ============================================================

PIVOT_LEFT = 3
PIVOT_RIGHT = 3

MIN_TRENDLINE_BARS = 8

TRENDLINE_TOLERANCE_PCT = 0.20

BREAKOUT_BUFFER_PCT = 0.05

# HARD REQUIREMENT
MIN_TRENDLINE_TOUCHES = 3


# ============================================================
# RVOL
# ============================================================

RVOL_LOOKBACK = 20
RVOL_MIN = 1.20


# ============================================================
# RISK
# ============================================================

MIN_RR = 1.01

SL_BUFFER_PCT = 0.05 / 100.0


# ============================================================
# TRADE
# ============================================================

MAX_HOLD_HOURS = 48

MAX_SIGNALS_PER_SCAN = 1


# ============================================================
# HTTP
# ============================================================

REQUEST_TIMEOUT = 20

HEADERS = {
    "User-Agent": (
        f"KrakenTrendlineScanner/{VERSION}"
    )
}


# ============================================================
# STATS
# ============================================================

SCAN_STATS = {
    "assets_scanned": 0,
    "ohlc_1h_ok": 0,
    "ohlc_15m_ok": 0,
    "trendlines_1h": 0,
    "breakouts_1h": 0,
    "trendlines_15m": 0,
    "breakouts_15m": 0,
    "rvol_confirmed": 0,
    "new_signals": 0,
}


# ============================================================
# TIME HELPERS
# ============================================================

def now_utc():
    return datetime.now(
        timezone.utc
    )


def now_tehran():
    return now_utc().astimezone(
        TEHRAN_TZ
    )


def format_time(dt=None):

    if dt is None:
        dt = now_tehran()

    try:

        if isinstance(dt, str):

            dt = pd.to_datetime(
                dt,
                utc=True,
            ).to_pydatetime()

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            TEHRAN_TZ
        ).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    except Exception:

        return str(dt)


def duration_text(seconds):

    try:
        seconds = max(
            0,
            int(seconds)
        )
    except Exception:
        return "-"

    days = seconds // 86400

    hours = (
        seconds % 86400
    ) // 3600

    minutes = (
        seconds % 3600
    ) // 60

    if days > 0:
        return (
            f"{days}d "
            f"{hours}h "
            f"{minutes}m"
        )

    if hours > 0:
        return (
            f"{hours}h "
            f"{minutes}m"
        )

    return f"{minutes}m"


def safe_float(
    value,
    default=None,
):

    try:

        if value is None:
            return default

        return float(value)

    except Exception:

        return default


def pct_change(
    entry,
    current,
):

    if not entry or not current:
        return 0.0

    return (
        (current - entry)
        / entry
    ) * 100.0


# ============================================================
# HTTP
# ============================================================

def http_get(
    url,
    params=None,
):

    try:

        response = requests.get(
            url,
            params=params,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            print(
                f"[HTTP] {response.status_code} "
                f"{url}"
            )

            return None

        return response.json()

    except Exception as e:

        print(
            f"[HTTP ERROR] {url}: {e}"
        )

        return None


# ============================================================
# TICKERS
# ============================================================

def get_tickers():

    data = http_get(
        KRAKEN_TICKER_URL
    )

    if not data:
        return {}

    tickers = data.get(
        "tickers"
    )

    if not isinstance(
        tickers,
        list,
    ):
        return {}

    result = {}

    for item in tickers:

        symbol = item.get(
            "symbol"
        )

        if symbol:
            result[symbol] = item

    return result


def get_current_price(
    asset
):

    symbol = contract_for_asset(
        asset
    )

    tickers = get_tickers()

    item = tickers.get(
        symbol
    )

    if not item:
        return None

    for key in (
        "last",
        "lastPrice",
        "markPrice",
        "indexPrice",
    ):

        value = safe_float(
            item.get(key)
        )

        if (
            value is not None
            and value > 0
        ):
            return value

    return None


# ============================================================
# OHLC
# ============================================================

def get_ohlc(
    asset,
    interval,
):

    symbol = contract_for_asset(
        asset
    )

    url = (
        f"{KRAKEN_CHART_URL}/"
        f"{symbol}/"
        f"{interval}"
    )

    data = http_get(url)

    if not data:
        return None

    candles = None

    if isinstance(
        data,
        dict,
    ):

        candles = data.get(
            "candles"
        )

        if candles is None:
            candles = data.get(
                "data"
            )

    elif isinstance(
        data,
        list,
    ):

        candles = data

    if not candles:
        return None

    rows = []

    for candle in candles:

        try:

            if isinstance(
                candle,
                dict,
            ):

                ts = (
                    candle.get("time")
                    or candle.get("timestamp")
                    or candle.get("ts")
                )

                op = candle.get(
                    "open"
                )

                hi = candle.get(
                    "high"
                )

                lo = candle.get(
                    "low"
                )

                cl = candle.get(
                    "close"
                )

                vol = (
                    candle.get("volume")
                    or candle.get("vol")
                    or 0
                )

            elif isinstance(
                candle,
                (list, tuple),
            ):

                if len(candle) < 5:
                    continue

                ts = candle[0]
                op = candle[1]
                hi = candle[2]
                lo = candle[3]
                cl = candle[4]

                vol = (
                    candle[5]
                    if len(candle) > 5
                    else 0
                )

            else:

                continue

            ts = float(ts)

            if ts > 10_000_000_000:
                ts /= 1000.0

            rows.append(
                {
                    "time": pd.to_datetime(
                        ts,
                        unit="s",
                        utc=True,
                    ),
                    "open": float(op),
                    "high": float(hi),
                    "low": float(lo),
                    "close": float(cl),
                    "volume": float(
                        vol or 0
                    ),
                }
            )

        except Exception:

            continue

    if not rows:
        return None

    df = pd.DataFrame(
        rows
    )

    df = df.sort_values(
        "time"
    )

    df = df.drop_duplicates(
        "time"
    )

    df = df.reset_index(
        drop=True
    )

    # --------------------------------------------------------
    # CLOSED CANDLES ONLY
    # --------------------------------------------------------

    if len(df) >= 2:

        df = df.iloc[:-1].copy()

    if len(df) < 50:
        return None

    return df.reset_index(
        drop=True
    )


# ============================================================
# PIVOTS
# ============================================================

def find_pivot_highs(
    df
):

    highs = df[
        "high"
    ].to_numpy()

    pivots = []

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT,
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

            pivots.append(
                {
                    "index": i,
                    "time": df.iloc[i][
                        "time"
                    ],
                    "price": float(
                        highs[i]
                    ),
                }
            )

    return pivots


def find_pivot_lows(
    df
):

    lows = df[
        "low"
    ].to_numpy()

    pivots = []

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT,
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

            pivots.append(
                {
                    "index": i,
                    "time": df.iloc[i][
                        "time"
                    ],
                    "price": float(
                        lows[i]
                    ),
                }
            )

    return pivots


# ============================================================
# TRENDLINE MATH
# ============================================================

def line_price(
    p1,
    p2,
    index,
):

    x1 = float(
        p1["index"]
    )

    y1 = float(
        p1["price"]
    )

    x2 = float(
        p2["index"]
    )

    y2 = float(
        p2["price"]
    )

    if x2 == x1:
        return y1

    slope = (
        y2 - y1
    ) / (
        x2 - x1
    )

    return (
        y1
        + slope
        * (
            float(index)
            - x1
        )
    )


def trendline_error_pct(
    price,
    line,
):

    if line == 0:
        return 999.0

    return abs(
        (price - line)
        / line
    ) * 100.0


# ============================================================
# VALID TRENDLINE
# ============================================================

def find_valid_trendline(
    df,
    direction,
    max_lookback=120,
):

    if len(df) < MIN_TRENDLINE_BARS:
        return None

    # --------------------------------------------------------
    # Pivot type
    # --------------------------------------------------------

    if direction == "LONG":

        pivots = find_pivot_highs(
            df
        )

    else:

        pivots = find_pivot_lows(
            df
        )

    # --------------------------------------------------------
    # HARD REQUIREMENT
    # --------------------------------------------------------

    if len(pivots) < MIN_TRENDLINE_TOUCHES:

        return None

    start_index = max(
        0,
        len(df) - max_lookback,
    )

    pivots = [
        p
        for p in pivots
        if p["index"] >= start_index
    ]

    if len(pivots) < MIN_TRENDLINE_TOUCHES:
        return None

    best = None

    # --------------------------------------------------------
    # Test every possible anchor pair
    # --------------------------------------------------------

    for a in range(
        len(pivots) - 1
    ):

        p1 = pivots[a]

        for b in range(
            a + 1,
            len(pivots),
        ):

            p2 = pivots[b]

            if (
                p2["index"]
                - p1["index"]
                < MIN_TRENDLINE_BARS
            ):
                continue

            # ------------------------------------------------
            # Direction
            # ------------------------------------------------

            if direction == "LONG":

                # Descending resistance
                if p2["price"] >= p1["price"]:
                    continue

            else:

                # Ascending support
                if p2["price"] <= p1["price"]:
                    continue

            # ------------------------------------------------
            # Find all touches
            # ------------------------------------------------

            touches = []

            for p in pivots:

                if p["index"] < p1["index"]:
                    continue

                expected = line_price(
                    p1,
                    p2,
                    p["index"],
                )

                error = trendline_error_pct(
                    p["price"],
                    expected,
                )

                if (
                    error
                    <= TRENDLINE_TOLERANCE_PCT
                ):

                    touches.append(
                        {
                            "index": p["index"],
                            "time": p["time"],
                            "price": p["price"],
                            "line_price": expected,
                            "error_pct": error,
                        }
                    )

            # ------------------------------------------------
            # MUST HAVE 3 TOUCHES
            # ------------------------------------------------

            if len(touches) < MIN_TRENDLINE_TOUCHES:
                continue

            touches = sorted(
                touches,
                key=lambda x: x["index"]
            )

            # ------------------------------------------------
            # Use first two actual touches as anchors
            # ------------------------------------------------

            anchor1 = {
                "index": touches[0]["index"],
                "time": touches[0]["time"],
                "price": touches[0]["price"],
            }

            anchor2 = {
                "index": touches[1]["index"],
                "time": touches[1]["time"],
                "price": touches[1]["price"],
            }

            if (
                anchor2["index"]
                - anchor1["index"]
                < MIN_TRENDLINE_BARS
            ):
                continue

            if direction == "LONG":

                if (
                    anchor2["price"]
                    >= anchor1["price"]
                ):
                    continue

            else:

                if (
                    anchor2["price"]
                    <= anchor1["price"]
                ):
                    continue

            # ------------------------------------------------
            # Recalculate final touches
            # ------------------------------------------------

            final_touches = []

            for p in pivots:

                if (
                    p["index"]
                    < anchor1["index"]
                ):
                    continue

                expected = line_price(
                    anchor1,
                    anchor2,
                    p["index"],
                )

                error = trendline_error_pct(
                    p["price"],
                    expected,
                )

                if (
                    error
                    <= TRENDLINE_TOLERANCE_PCT
                ):

                    final_touches.append(
                        {
                            "index": p["index"],
                            "time": p["time"],
                            "price": p["price"],
                            "line_price": expected,
                            "error_pct": error,
                        }
                    )

            if (
                len(final_touches)
                < MIN_TRENDLINE_TOUCHES
            ):
                continue

            final_touches = sorted(
                final_touches,
                key=lambda x: x["index"]
            )

            # ------------------------------------------------
            # Score
            # ------------------------------------------------

            total_error = sum(
                x["error_pct"]
                for x in final_touches
            )

            span = (
                final_touches[-1]["index"]
                - final_touches[0]["index"]
            )

            score = (
                len(final_touches)
                * 1000
                + span
                - total_error
                * 100
            )

            candidate = {
                "direction": direction,
                "p1": anchor1,
                "p2": anchor2,
                "touches": final_touches,
                "touch_count": len(
                    final_touches
                ),
                "score": score,
            }

            if (
                best is None
                or score > best["score"]
            ):

                best = candidate

    return best


# ============================================================
# BREAKOUT
# ============================================================

def find_latest_breakout(
    df,
    trendline,
    direction,
    start_index=0,
):

    if trendline is None:
        return None

    touches = trendline.get(
        "touches",
        []
    )

    if not touches:
        return None

    last_touch_index = touches[
        -1
    ]["index"]

    search_start = max(
        start_index,
        last_touch_index + 1,
    )

    if search_start >= len(df):
        return None

    breakout = None

    for i in range(
        search_start,
        len(df),
    ):

        candle = df.iloc[i]

        line = line_price(
            trendline["p1"],
            trendline["p2"],
            i,
        )

        if direction == "LONG":

            threshold = (
                line
                * (
                    1
                    + BREAKOUT_BUFFER_PCT
                    / 100.0
                )
            )

            if (
                candle["close"]
                > threshold
            ):

                breakout = {
                    "index": i,
                    "time": candle["time"],
                    "price": float(
                        candle["close"]
                    ),
                    "line_price": float(
                        line
                    ),
                }

        else:

            threshold = (
                line
                * (
                    1
                    - BREAKOUT_BUFFER_PCT
                    / 100.0
                )
            )

            if (
                candle["close"]
                < threshold
            ):

                breakout = {
                    "index": i,
                    "time": candle["time"],
                    "price": float(
                        candle["close"]
                    ),
                    "line_price": float(
                        line
                    ),
                }

    return breakout


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(
    df,
    index,
):

    if index < RVOL_LOOKBACK:
        return None

    current_volume = safe_float(
        df.iloc[index][
            "volume"
        ]
    )

    if current_volume is None:
        return None

    avg_volume = df.iloc[
        index - RVOL_LOOKBACK:index
    ][
        "volume"
    ].mean()

    if (
        avg_volume is None
        or avg_volume <= 0
    ):
        return None

    return (
        current_volume
        / avg_volume
    )


# ============================================================
# TP / SL
# ============================================================

def get_tp_sl(
    df,
    direction,
    entry_index,
    entry_price,
):

    highs = find_pivot_highs(
        df
    )

    lows = find_pivot_lows(
        df
    )

    confirmed_highs = [
        p
        for p in highs
        if p["index"] < entry_index
    ]

    confirmed_lows = [
        p
        for p in lows
        if p["index"] < entry_index
    ]

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        tp_candidates = [
            p
            for p in confirmed_highs
            if p["price"] > entry_price
        ]

        sl_candidates = [
            p
            for p in confirmed_lows
            if p["price"] < entry_price
        ]

        if (
            not tp_candidates
            or not sl_candidates
        ):
            return None

        tp_point = min(
            tp_candidates,
            key=lambda p: p["price"]
        )

        sl_point = max(
            sl_candidates,
            key=lambda p: p["index"]
        )

        tp = float(
            tp_point["price"]
        )

        sl = float(
            sl_point["price"]
            * (
                1
                - SL_BUFFER_PCT
            )
        )

        reward = (
            tp
            - entry_price
        )

        risk = (
            entry_price
            - sl
        )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        tp_candidates = [
            p
            for p in confirmed_lows
            if p["price"] < entry_price
        ]

        sl_candidates = [
            p
            for p in confirmed_highs
            if p["price"] > entry_price
        ]

        if (
            not tp_candidates
            or not sl_candidates
        ):
            return None

        tp_point = max(
            tp_candidates,
            key=lambda p: p["price"]
        )

        sl_point = max(
            sl_candidates,
            key=lambda p: p["index"]
        )

        tp = float(
            tp_point["price"]
        )

        sl = float(
            sl_point["price"]
            * (
                1
                + SL_BUFFER_PCT
            )
        )

        reward = (
            entry_price
            - tp
        )

        risk = (
            sl
            - entry_price
        )

    if risk <= 0:
        return None

    rr = (
        reward
        / risk
    )

    if rr < MIN_RR:
        return None

    return {
        "tp": tp,
        "sl": sl,
        "rr": rr,
        "tp_point": tp_point,
        "sl_point": sl_point,
    }


# ============================================================
# SERIALIZE TRENDLINE
# ============================================================

def serialize_trendline(
    trendline
):

    if not trendline:
        return None

    touches = []

    for n, p in enumerate(
        trendline.get(
            "touches",
            []
        ),
        start=1,
    ):

        touches.append(
            {
                "label": f"P{n}",
                "index": int(
                    p["index"]
                ),
                "time": str(
                    p["time"]
                ),
                "price": float(
                    p["price"]
                ),
                "line_price": float(
                    p["line_price"]
                ),
                "error_pct": float(
                    p["error_pct"]
                ),
            }
        )

    return json.dumps(
        {
            "direction": trendline.get(
                "direction"
            ),
            "touch_count": len(
                touches
            ),
            "touches": touches,
            "p1": {
                "index": int(
                    trendline["p1"][
                        "index"
                    ]
                ),
                "time": str(
                    trendline["p1"][
                        "time"
                    ]
                ),
                "price": float(
                    trendline["p1"][
                        "price"
                    ]
                ),
            },
            "p2": {
                "index": int(
                    trendline["p2"][
                        "index"
                    ]
                ),
                "time": str(
                    trendline["p2"][
                        "time"
                    ]
                ),
                "price": float(
                    trendline["p2"][
                        "price"
                    ]
                ),
            },
        },
        default=str,
    )


# ============================================================
# DATABASE CONNECTION
# ============================================================

def db_connect():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30,
    )

    conn.row_factory = sqlite3.Row

    return conn


# ============================================================
# DATABASE INITIALIZATION / MIGRATION
# ============================================================

def init_db():

    conn = db_connect()

    # --------------------------------------------------------
    # Create table if completely new
    # --------------------------------------------------------

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset TEXT,
            contract TEXT,
            direction TEXT,
            pattern TEXT,
            entry_price REAL,
            tp REAL,
            sl REAL,
            rr REAL,
            signal_time TEXT,
            status TEXT DEFAULT 'OPEN',
            exit_price REAL,
            exit_time TEXT,
            exit_reason TEXT,
            duration_seconds INTEGER,
            trendline_1h TEXT,
            trendline_15m TEXT,
            breakout_1h_time TEXT,
            breakout_15m_time TEXT,
            breakout_5m_time TEXT,
            trendline_5m TEXT,
            rvol REAL
        )
        """
    )

    conn.commit()

    # --------------------------------------------------------
    # REQUIRED COLUMNS
    #
    # This is the important DB fix.
    # --------------------------------------------------------

    required_columns = {

        "asset": "TEXT",

        "contract": "TEXT",

        "direction": "TEXT",

        "pattern": "TEXT",

        "entry_price": "REAL",

        "tp": "REAL",

        "sl": "REAL",

        "rr": "REAL",

        "signal_time": "TEXT",

        "status": "TEXT DEFAULT 'OPEN'",

        "exit_price": "REAL",

        "exit_time": "TEXT",

        "exit_reason": "TEXT",

        "duration_seconds": "INTEGER",

        "trendline_1h": "TEXT",

        "trendline_15m": "TEXT",

        "breakout_1h_time": "TEXT",

        "breakout_15m_time": "TEXT",

        # Backward compatibility
        "breakout_5m_time": "TEXT",

        # Backward compatibility
        "trendline_5m": "TEXT",

        "rvol": "REAL",
    }

    columns = conn.execute(
        "PRAGMA table_info(signals)"
    ).fetchall()

    existing_columns = {
        row["name"]
        for row in columns
    }

    # --------------------------------------------------------
    # Add missing columns
    # --------------------------------------------------------

    for column, definition in (
        required_columns.items()
    ):

        if column not in existing_columns:

            print(
                "[DB MIGRATION] "
                f"Adding missing column: "
                f"{column}"
            )

            try:

                conn.execute(
                    f"""
                    ALTER TABLE signals
                    ADD COLUMN {column}
                    {definition}
                    """
                )

                conn.commit()

            except Exception as e:

                print(
                    f"[DB MIGRATION ERROR] "
                    f"{column}: {e}"
                )

    # --------------------------------------------------------
    # Make sure existing rows have contracts
    # --------------------------------------------------------

    try:

        rows = conn.execute(
            """
            SELECT id, asset, contract
            FROM signals
            """
        ).fetchall()

        for row in rows:

            asset = row["asset"]
            contract = row["contract"]

            if (
                asset
                and not contract
            ):

                conn.execute(
                    """
                    UPDATE signals
                    SET contract = ?
                    WHERE id = ?
                    """,
                    (
                        contract_for_asset(
                            asset
                        ),
                        row["id"],
                    ),
                )

        conn.commit()

    except Exception:

        traceback.print_exc()

    conn.close()


# ============================================================
# OPEN TRADE CHECK
# ============================================================

def has_open_trade(
    conn,
    asset,
    direction,
):

    try:

        row = conn.execute(
            """
            SELECT id
            FROM signals
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

    except Exception:

        traceback.print_exc()

        return False


# ============================================================
# INSERT SIGNAL
# ============================================================

def insert_signal(
    asset,
    direction,
    entry_price,
    tp,
    sl,
    rr,
    signal_time,
    trendline_1h,
    trendline_15m,
    breakout_1h_time,
    breakout_15m_time,
    rvol,
):

    conn = db_connect()

    try:

        if has_open_trade(
            conn,
            asset,
            direction,
        ):

            return None

        cursor = conn.execute(
            """
            INSERT INTO signals (
                asset,
                contract,
                direction,
                pattern,
                entry_price,
                tp,
                sl,
                rr,
                signal_time,
                status,
                trendline_1h,
                trendline_15m,
                breakout_1h_time,
                breakout_15m_time,
                breakout_5m_time,
                trendline_5m,
                rvol
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                'OPEN',
                ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                asset,
                contract_for_asset(
                    asset
                ),
                direction,
                "Trendline Breakout",
                entry_price,
                tp,
                sl,
                rr,
                str(signal_time),
                serialize_trendline(
                    trendline_1h
                ),
                serialize_trendline(
                    trendline_15m
                ),
                str(
                    breakout_1h_time
                ),
                str(
                    breakout_15m_time
                ),
                str(
                    breakout_15m_time
                ),
                serialize_trendline(
                    trendline_15m
                ),
                rvol,
            ),
        )

        conn.commit()

        return cursor.lastrowid

    except Exception:

        conn.rollback()

        traceback.print_exc()

        return None

    finally:

        conn.close()


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades():

    conn = db_connect()

    closed = []

    try:

        rows = conn.execute(
            """
            SELECT *
            FROM signals
            WHERE status = 'OPEN'
            ORDER BY id
            """
        ).fetchall()

        for row in rows:

            # ------------------------------------------------
            # Convert sqlite Row to dict.
            #
            # This prevents legacy-schema crashes.
            # ------------------------------------------------

            data = dict(row)

            trade_id = data.get(
                "id"
            )

            asset = data.get(
                "asset"
            )

            direction = data.get(
                "direction"
            )

            entry = safe_float(
                data.get(
                    "entry_price"
                )
            )

            tp = safe_float(
                data.get(
                    "tp"
                )
            )

            sl = safe_float(
                data.get(
                    "sl"
                )
            )

            signal_time = data.get(
                "signal_time"
            )

            # ------------------------------------------------
            # Validate legacy row
            # ------------------------------------------------

            if not asset:

                print(
                    f"[DB WARNING] "
                    f"Trade ID={trade_id} "
                    f"has no asset. Skipping."
                )

                continue

            if direction not in (
                "LONG",
                "SHORT",
            ):

                print(
                    f"[DB WARNING] "
                    f"Trade ID={trade_id} "
                    f"invalid direction. "
                    f"Skipping."
                )

                continue

            if (
                entry is None
                or tp is None
                or sl is None
            ):

                print(
                    f"[DB WARNING] "
                    f"Trade ID={trade_id} "
                    f"has incomplete "
                    f"Entry/TP/SL. "
                    f"Skipping."
                )

                continue

            # ------------------------------------------------
            # Current price
            # ------------------------------------------------

            current = get_current_price(
                asset
            )

            if current is None:
                continue

            # ------------------------------------------------
            # Duration
            # ------------------------------------------------

            try:

                opened = pd.to_datetime(
                    signal_time,
                    utc=True,
                ).to_pydatetime()

            except Exception:

                opened = now_utc()

            duration = int(
                (
                    now_utc()
                    - opened
                ).total_seconds()
            )

            exit_price = None
            exit_reason = None

            # ------------------------------------------------
            # LONG
            #
            # SL checked first.
            # ------------------------------------------------

            if direction == "LONG":

                if current <= sl:

                    exit_price = current
                    exit_reason = "SL"

                elif current >= tp:

                    exit_price = current
                    exit_reason = "TP"

            # ------------------------------------------------
            # SHORT
            # ------------------------------------------------

            else:

                if current >= sl:

                    exit_price = current
                    exit_reason = "SL"

                elif current <= tp:

                    exit_price = current
                    exit_reason = "TP"

            # ------------------------------------------------
            # TIME EXIT
            # ------------------------------------------------

            if (
                exit_reason is None
                and duration
                >= MAX_HOLD_HOURS * 3600
            ):

                exit_price = current
                exit_reason = "TIME"

            # ------------------------------------------------
            # Close
            # ------------------------------------------------

            if exit_reason is not None:

                exit_time = (
                    now_utc()
                    .isoformat()
                )

                conn.execute(
                    """
                    UPDATE signals
                    SET status = 'CLOSED',
                        exit_price = ?,
                        exit_time = ?,
                        exit_reason = ?,
                        duration_seconds = ?
                    WHERE id = ?
                    """,
                    (
                        exit_price,
                        exit_time,
                        exit_reason,
                        duration,
                        trade_id,
                    ),
                )

                closed.append(
                    {
                        "id": trade_id,
                        "asset": asset,
                        "direction": direction,
                        "entry": entry,
                        "exit": exit_price,
                        "reason": exit_reason,
                        "duration": duration,
                    }
                )

        conn.commit()

    except Exception:

        conn.rollback()

        traceback.print_exc()

    finally:

        conn.close()

    return closed


# ============================================================
# GET OPEN TRADES
# ============================================================

def get_open_trades():

    conn = db_connect()

    result = []

    try:

        rows = conn.execute(
            """
            SELECT *
            FROM signals
            WHERE status = 'OPEN'
            ORDER BY id DESC
            """
        ).fetchall()

        for row in rows:

            # ------------------------------------------------
            # Convert to dict first.
            # ------------------------------------------------

            item = dict(row)

            # ------------------------------------------------
            # Safe access
            # ------------------------------------------------

            asset = item.get(
                "asset"
            )

            direction = item.get(
                "direction"
            )

            entry = safe_float(
                item.get(
                    "entry_price"
                )
            )

            tp = safe_float(
                item.get(
                    "tp"
                )
            )

            sl = safe_float(
                item.get(
                    "sl"
                )
            )

            signal_time = item.get(
                "signal_time"
            )

            # ------------------------------------------------
            # Ignore malformed legacy records
            # ------------------------------------------------

            if (
                not asset
                or direction not in (
                    "LONG",
                    "SHORT",
                )
                or entry is None
                or tp is None
                or sl is None
            ):

                print(
                    "[DB WARNING] "
                    f"Ignoring malformed "
                    f"OPEN trade "
                    f"ID={item.get('id')}"
                )

                continue

            # ------------------------------------------------
            # Current
            # ------------------------------------------------

            current = get_current_price(
                asset
            )

            item[
                "current_price"
            ] = current

            # ------------------------------------------------
            # Duration
            # ------------------------------------------------

            try:

                opened = pd.to_datetime(
                    signal_time,
                    utc=True,
                ).to_pydatetime()

                duration = int(
                    (
                        now_utc()
                        - opened
                    ).total_seconds()
                )

            except Exception:

                duration = 0

            item[
                "duration_seconds"
            ] = duration

            result.append(
                item
            )

    except Exception:

        traceback.print_exc()

    finally:

        conn.close()

    return result


# ============================================================
# PERFORMANCE
# ============================================================

def get_performance():

    conn = db_connect()

    try:

        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(
                    CASE
                        WHEN status = 'CLOSED'
                         AND exit_reason = 'TP'
                        THEN 1
                        ELSE 0
                    END
                ) AS wins,
                SUM(
                    CASE
                        WHEN status = 'CLOSED'
                         AND exit_reason = 'SL'
                        THEN 1
                        ELSE 0
                    END
                ) AS losses
            FROM signals
            """
        ).fetchone()

        total = int(
            row["total"] or 0
        )

        wins = int(
            row["wins"] or 0
        )

        losses = int(
            row["losses"] or 0
        )

        decided = (
            wins
            + losses
        )

        winrate = (
            wins
            / decided
            * 100.0
            if decided
            else 0.0
        )

        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "winrate": winrate,
        }

    except Exception:

        traceback.print_exc()

        return {
            "total": 0,
            "wins": 0,
            "losses": 0,
            "winrate": 0.0,
        }

    finally:

        conn.close()


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def telegram_send_message(
    message
):

    if not TELEGRAM_BOT_TOKEN:
        return False

    if not TELEGRAM_CHAT_ID:
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/"
        "sendMessage"
    )

    try:

        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=REQUEST_TIMEOUT,
        )

        return (
            response.status_code == 200
        )

    except Exception:

        return False


# ============================================================
# TELEGRAM PHOTO
# ============================================================

def telegram_send_photo(
    image_bytes,
    caption,
):

    if not TELEGRAM_BOT_TOKEN:
        return False

    if not TELEGRAM_CHAT_ID:
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/"
        "sendPhoto"
    )

    try:

        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
            },
            files={
                "photo": (
                    "chart.png",
                    image_bytes,
                    "image/png",
                )
            },
            timeout=REQUEST_TIMEOUT,
        )

        return (
            response.status_code == 200
        )

    except Exception:

        return False


# ============================================================
# SIGNAL FORMAT
# ============================================================

def format_signal(
    asset,
    direction,
    entry,
    sl,
    tp,
    rr,
    rvol,
    signal_time,
):

    emoji = (
        "🟢 LONG"
        if direction == "LONG"
        else "🔴 SHORT"
    )

    return (
        f"{emoji} {asset}\n"
        f"Entry: {entry:.8g}\n"
        f"SL: {sl:.8g}\n"
        f"TP: {tp:.8g}\n"
        f"RR: {rr:.2f}\n"
        f"RVOL: {rvol:.2f}\n"
        f"Time: {format_time(signal_time)}"
    )


# ============================================================
# OPEN TRADES REPORT
# ============================================================

def format_open_trades():

    trades = get_open_trades()

    if not trades:

        return (
            "OPEN TRADES\n"
            "None"
        )

    lines = [
        "OPEN TRADES"
    ]

    for trade in trades:

        asset = trade.get(
            "asset"
        )

        direction = trade.get(
            "direction"
        )

        entry = safe_float(
            trade.get(
                "entry_price"
            )
        )

        current = safe_float(
            trade.get(
                "current_price"
            )
        )

        tp = safe_float(
            trade.get(
                "tp"
            )
        )

        sl = safe_float(
            trade.get(
                "sl"
            )
        )

        if (
            entry is None
            or tp is None
            or sl is None
        ):
            continue

        emoji = (
            "🟢"
            if direction == "LONG"
            else "🔴"
        )

        if current is not None:

            change = pct_change(
                entry,
                current,
            )

            if direction == "SHORT":
                change = -change

            current_text = (
                f"{current:.8g} "
                f"({change:+.2f}%)"
            )

        else:

            current_text = "-"

        duration = duration_text(
            trade.get(
                "duration_seconds",
                0,
            )
        )

        lines.append(
            f"\n{emoji} "
            f"{direction} {asset}\n"
            f"Entry: {entry:.8g}\n"
            f"Current: {current_text}\n"
            f"SL: {sl:.8g}\n"
            f"TP: {tp:.8g}\n"
            f"Duration: {duration}"
        )

    return "\n".join(
        lines
    )


# ============================================================
# PERFORMANCE REPORT
# ============================================================

def format_performance():

    performance = (
        get_performance()
    )

    return (
        "PERFORMANCE\n"
        f"Total: "
        f"{performance['total']}\n"
        f"Wins: "
        f"{performance['wins']}\n"
        f"Losses: "
        f"{performance['losses']}\n"
        f"Win Rate: "
        f"{performance['winrate']:.2f}%"
    )


# ============================================================
# CHART
# ============================================================

def create_chart(
    asset,
    df,
    direction,
    trendline,
    breakout,
    entry,
    tp,
    sl,
):

    touches = trendline.get(
        "touches",
        []
    )

    if len(touches) < MIN_TRENDLINE_TOUCHES:

        return None

    important_indices = [
        int(
            p["index"]
        )
        for p in touches
    ]

    important_indices.append(
        int(
            breakout["index"]
        )
    )

    left_index = max(
        0,
        min(
            important_indices
        ) - 20,
    )

    right_index = min(
        len(df),
        max(
            important_indices
        ) + 25,
    )

    chart_df = df.iloc[
        left_index:right_index
    ].copy()

    if chart_df.empty:
        return None

    fig, ax = plt.subplots(
        figsize=(15, 8)
    )

    # --------------------------------------------------------
    # Candles
    # --------------------------------------------------------

    for local_i, (
        _,
        candle,
    ) in enumerate(
        chart_df.iterrows()
    ):

        op = float(
            candle["open"]
        )

        hi = float(
            candle["high"]
        )

        lo = float(
            candle["low"]
        )

        cl = float(
            candle["close"]
        )

        body_low = min(
            op,
            cl,
        )

        body_high = max(
            op,
            cl,
        )

        # Wick
        ax.plot(
            [local_i, local_i],
            [lo, hi],
            linewidth=1,
        )

        # Body
        width = 0.65

        ax.add_patch(
            plt.Rectangle(
                (
                    local_i
                    - width / 2,
                    body_low,
                ),
                width,
                max(
                    body_high
                    - body_low,
                    1e-12,
                ),
                fill=True,
                alpha=0.70,
            )
        )

    # --------------------------------------------------------
    # Trendline
    # --------------------------------------------------------

    line_x = []
    line_y = []

    for global_i in range(
        left_index,
        right_index,
    ):

        value = line_price(
            trendline["p1"],
            trendline["p2"],
            global_i,
        )

        line_x.append(
            global_i
            - left_index
        )

        line_y.append(
            value
        )

    ax.plot(
        line_x,
        line_y,
        linewidth=2.2,
        label=(
            "Trendline "
            f"({len(touches)} touches)"
        ),
    )

    # --------------------------------------------------------
    # ALL TOUCH POINTS
    # --------------------------------------------------------

    for number, touch in enumerate(
        touches,
        start=1,
    ):

        global_index = int(
            touch["index"]
        )

        if (
            global_index
            < left_index
            or global_index
            >= right_index
        ):
            continue

        local_x = (
            global_index
            - left_index
        )

        price = float(
            touch["price"]
        )

        ax.scatter(
            [local_x],
            [price],
            s=80,
            zorder=6,
        )

        ax.annotate(
            f"P{number}",
            (
                local_x,
                price,
            ),
            xytext=(
                0,
                10,
            ),
            textcoords="offset points",
            ha="center",
            fontsize=10,
            fontweight="bold",
        )

    # --------------------------------------------------------
    # BREAKOUT
    # --------------------------------------------------------

    breakout_index = int(
        breakout["index"]
    )

    if (
        left_index
        <= breakout_index
        < right_index
    ):

        bx = (
            breakout_index
            - left_index
        )

        by = float(
            breakout["price"]
        )

        ax.scatter(
            [bx],
            [by],
            marker="*",
            s=220,
            zorder=10,
            label="BREAKOUT",
        )

        ax.annotate(
            "BREAKOUT",
            (
                bx,
                by,
            ),
            xytext=(
                0,
                18,
            ),
            textcoords="offset points",
            ha="center",
            fontsize=11,
            fontweight="bold",
        )

    # --------------------------------------------------------
    # Entry
    # --------------------------------------------------------

    ax.axhline(
        entry,
        linestyle="--",
        linewidth=1.5,
        label=(
            f"Entry {entry:.8g}"
        ),
    )

    # --------------------------------------------------------
    # TP
    # --------------------------------------------------------

    ax.axhline(
        tp,
        linestyle="--",
        linewidth=1.2,
        label=(
            f"TP {tp:.8g}"
        ),
    )

    # --------------------------------------------------------
    # SL
    # --------------------------------------------------------

    ax.axhline(
        sl,
        linestyle="--",
        linewidth=1.2,
        label=(
            f"SL {sl:.8g}"
        ),
    )

    # --------------------------------------------------------
    # X-axis
    # --------------------------------------------------------

    step = max(
        1,
        len(chart_df) // 10,
    )

    ticks = list(
        range(
            0,
            len(chart_df),
            step,
        )
    )

    labels = []

    for i in ticks:

        try:

            dt = pd.Timestamp(
                chart_df.iloc[i][
                    "time"
                ]
            ).tz_convert(
                TEHRAN_TZ
            )

            labels.append(
                dt.strftime(
                    "%m-%d %H:%M"
                )
            )

        except Exception:

            labels.append(
                str(
                    chart_df.iloc[i][
                        "time"
                    ]
                )
            )

    ax.set_xticks(
        ticks
    )

    ax.set_xticklabels(
        labels,
        rotation=35,
        ha="right",
    )

    # --------------------------------------------------------
    # Title
    # --------------------------------------------------------

    ax.set_title(
        f"KRAKEN 15M {asset} "
        f"{direction} Trendline Breakout "
        f"| {len(touches)} Touches"
    )

    ax.set_xlabel(
        "15M Closed Candles"
    )

    ax.set_ylabel(
        "Price"
    )

    ax.grid(
        True,
        alpha=0.25,
    )

    ax.legend(
        loc="best"
    )

    plt.tight_layout()

    image = io.BytesIO()

    plt.savefig(
        image,
        format="png",
        dpi=150,
        bbox_inches="tight",
    )

    plt.close(
        fig
    )

    image.seek(0)

    return image.getvalue()


# ============================================================
# NEW SIGNAL ALERT
# ============================================================

def send_new_signal_alert(
    signal
):

    asset = signal[
        "asset"
    ]

    direction = signal[
        "direction"
    ]

    entry = signal[
        "entry"
    ]

    sl = signal[
        "sl"
    ]

    tp = signal[
        "tp"
    ]

    rr = signal[
        "rr"
    ]

    rvol = signal[
        "rvol"
    ]

    signal_time = signal[
        "signal_time"
    ]

    message = (
        "🚨 NEW SIGNAL\n\n"
        + format_signal(
            asset=asset,
            direction=direction,
            entry=entry,
            sl=sl,
            tp=tp,
            rr=rr,
            rvol=rvol,
            signal_time=signal_time,
        )
    )

    telegram_send_message(
        message
    )

    chart = signal.get(
        "chart"
    )

    if chart:

        emoji = (
            "🟢 LONG"
            if direction == "LONG"
            else "🔴 SHORT"
        )

        caption = (
            f"{emoji} {asset} | 15M\n"
            f"Entry: {entry:.8g}\n"
            f"SL: {sl:.8g}\n"
            f"TP: {tp:.8g}\n"
            f"RR: {rr:.2f}\n"
            f"RVOL: {rvol:.2f}"
        )

        telegram_send_photo(
            chart,
            caption,
        )


# ============================================================
# CLOSE ALERTS
# ============================================================

def send_close_alerts(
    closed_trades
):

    for trade in closed_trades:

        asset = trade[
            "asset"
        ]

        direction = trade[
            "direction"
        ]

        entry = trade[
            "entry"
        ]

        exit_price = trade[
            "exit"
        ]

        reason = trade[
            "reason"
        ]

        duration = trade[
            "duration"
        ]

        result = pct_change(
            entry,
            exit_price,
        )

        if direction == "SHORT":
            result = -result

        emoji = (
            "🟢"
            if direction == "LONG"
            else "🔴"
        )

        message = (
            f"🔔 CLOSE {asset}\n"
            f"{emoji} {direction}\n"
            f"Entry: {entry:.8g}\n"
            f"Exit: {exit_price:.8g}\n"
            f"Result: {result:+.2f}%\n"
            f"Reason: {reason}\n"
            f"Duration: "
            f"{duration_text(duration)}"
        )

        telegram_send_message(
            message
        )


# ============================================================
# SCAN ONE ASSET
# ============================================================

def scan_asset(
    asset
):

    # --------------------------------------------------------
    # 1H OHLC
    # --------------------------------------------------------

    df_1h = get_ohlc(
        asset,
        "1h",
    )

    if df_1h is None:
        return None

    SCAN_STATS[
        "ohlc_1h_ok"
    ] += 1

    # --------------------------------------------------------
    # 1H LONG TRENDLINE
    # --------------------------------------------------------

    long_line_1h = (
        find_valid_trendline(
            df_1h,
            "LONG",
        )
    )

    # --------------------------------------------------------
    # 1H SHORT TRENDLINE
    # --------------------------------------------------------

    short_line_1h = (
        find_valid_trendline(
            df_1h,
            "SHORT",
        )
    )

    if long_line_1h:

        SCAN_STATS[
            "trendlines_1h"
        ] += 1

    if short_line_1h:

        SCAN_STATS[
            "trendlines_1h"
        ] += 1

    # --------------------------------------------------------
    # 1H LONG BREAKOUT
    # --------------------------------------------------------

    long_breakout_1h = None

    if long_line_1h:

        long_breakout_1h = (
            find_latest_breakout(
                df_1h,
                long_line_1h,
                "LONG",
            )
        )

    # --------------------------------------------------------
    # 1H SHORT BREAKOUT
    # --------------------------------------------------------

    short_breakout_1h = None

    if short_line_1h:

        short_breakout_1h = (
            find_latest_breakout(
                df_1h,
                short_line_1h,
                "SHORT",
            )
        )

    if long_breakout_1h:

        SCAN_STATS[
            "breakouts_1h"
        ] += 1

    if short_breakout_1h:

        SCAN_STATS[
            "breakouts_1h"
        ] += 1

    # --------------------------------------------------------
    # Candidate breakouts
    # --------------------------------------------------------

    candidates = []

    if long_breakout_1h:

        candidates.append(
            (
                long_breakout_1h,
                "LONG",
                long_line_1h,
            )
        )

    if short_breakout_1h:

        candidates.append(
            (
                short_breakout_1h,
                "SHORT",
                short_line_1h,
            )
        )

    if not candidates:
        return None

    # --------------------------------------------------------
    # Most recent 1H breakout
    # --------------------------------------------------------

    candidates.sort(
        key=lambda item:
        item[0]["index"],
        reverse=True,
    )

    (
        breakout_1h,
        direction,
        trendline_1h,
    ) = candidates[0]

    # --------------------------------------------------------
    # Check existing open trade
    # --------------------------------------------------------

    conn = db_connect()

    try:

        if has_open_trade(
            conn,
            asset,
            direction,
        ):

            return None

    finally:

        conn.close()

    # --------------------------------------------------------
    # 15M OHLC
    # --------------------------------------------------------

    df_15m = get_ohlc(
        asset,
        "15m",
    )

    if df_15m is None:
        return None

    SCAN_STATS[
        "ohlc_15m_ok"
    ] += 1

    # --------------------------------------------------------
    # 15M trendline
    # --------------------------------------------------------

    trendline_15m = (
        find_valid_trendline(
            df_15m,
            direction,
        )
    )

    if trendline_15m is None:
        return None

    SCAN_STATS[
        "trendlines_15m"
    ] += 1

    # --------------------------------------------------------
    # Find first 15M candle at/after 1H breakout
    # --------------------------------------------------------

    breakout_1h_time = pd.Timestamp(
        breakout_1h["time"]
    )

    start_index = 0

    for i in range(
        len(df_15m)
    ):

        candle_time = pd.Timestamp(
            df_15m.iloc[i][
                "time"
            ]
        )

        if candle_time >= breakout_1h_time:

            start_index = i

            break

    # --------------------------------------------------------
    # 15M breakout
    # --------------------------------------------------------

    breakout_15m = (
        find_latest_breakout(
            df_15m,
            trendline_15m,
            direction,
            start_index=start_index,
        )
    )

    if breakout_15m is None:
        return None

    SCAN_STATS[
        "breakouts_15m"
    ] += 1

    # --------------------------------------------------------
    # RVOL
    # --------------------------------------------------------

    rvol = calculate_rvol(
        df_15m,
        breakout_15m[
            "index"
        ],
    )

    if rvol is None:
        return None

    if rvol < RVOL_MIN:
        return None

    SCAN_STATS[
        "rvol_confirmed"
    ] += 1

    # --------------------------------------------------------
    # Entry
    # --------------------------------------------------------

    entry_index = (
        breakout_15m[
            "index"
        ]
    )

    entry_price = float(
        df_15m.iloc[
            entry_index
        ][
            "close"
        ]
    )

    # --------------------------------------------------------
    # TP / SL
    # --------------------------------------------------------

    risk = get_tp_sl(
        df_15m,
        direction,
        entry_index,
        entry_price,
    )

    if risk is None:
        return None

    tp = risk[
        "tp"
    ]

    sl = risk[
        "sl"
    ]

    rr = risk[
        "rr"
    ]

    # --------------------------------------------------------
    # Signal time
    # --------------------------------------------------------

    signal_time = (
        df_15m.iloc[
            entry_index
        ][
            "time"
        ]
    )

    # --------------------------------------------------------
    # Insert
    # --------------------------------------------------------

    signal_id = insert_signal(
        asset=asset,
        direction=direction,
        entry_price=entry_price,
        tp=tp,
        sl=sl,
        rr=rr,
        signal_time=str(
            signal_time
        ),
        trendline_1h=trendline_1h,
        trendline_15m=trendline_15m,
        breakout_1h_time=(
            breakout_1h[
                "time"
            ]
        ),
        breakout_15m_time=(
            breakout_15m[
                "time"
            ]
        ),
        rvol=rvol,
    )

    if signal_id is None:
        return None

    SCAN_STATS[
        "new_signals"
    ] += 1

    # --------------------------------------------------------
    # Chart
    # --------------------------------------------------------

    chart = create_chart(
        asset=asset,
        df=df_15m,
        direction=direction,
        trendline=trendline_15m,
        breakout=breakout_15m,
        entry=entry_price,
        tp=tp,
        sl=sl,
    )

    return {
        "id": signal_id,
        "asset": asset,
        "direction": direction,
        "entry": entry_price,
        "tp": tp,
        "sl": sl,
        "rr": rr,
        "rvol": rvol,
        "signal_time": signal_time,
        "chart": chart,
        "trendline_1h": trendline_1h,
        "trendline_15m": trendline_15m,
    }


# ============================================================
# RESET STATS
# ============================================================

def reset_stats():

    for key in SCAN_STATS:

        SCAN_STATS[key] = 0


# ============================================================
# SCAN SUMMARY
# ============================================================

def scan_summary():

    return (
        "📊 SCAN SUMMARY\n\n"
        f"Version: {VERSION}\n"
        "Mode: PAPER ONLY\n"
        "Time: Tehran\n\n"
        f"Assets Scanned: "
        f"{SCAN_STATS['assets_scanned']}/"
        f"{len(ASSETS)}\n"
        f"1H OHLC OK: "
        f"{SCAN_STATS['ohlc_1h_ok']}\n"
        f"15M OHLC OK: "
        f"{SCAN_STATS['ohlc_15m_ok']}\n"
        f"1H Trendlines: "
        f"{SCAN_STATS['trendlines_1h']}\n"
        f"1H Breakouts: "
        f"{SCAN_STATS['breakouts_1h']}\n"
        f"15M Trendlines: "
        f"{SCAN_STATS['trendlines_15m']}\n"
        f"15M Breakouts: "
        f"{SCAN_STATS['breakouts_15m']}\n"
        f"RVOL Confirmed: "
        f"{SCAN_STATS['rvol_confirmed']}\n"
        f"New Signals: "
        f"{SCAN_STATS['new_signals']}"
    )


# ============================================================
# PERIODIC REPORT
# ============================================================

def send_periodic_report():

    report = (
        scan_summary()
        + "\n\n"
        + format_open_trades()
        + "\n\n"
        + format_performance()
    )

    telegram_send_message(
        report
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=" * 70
    )

    print(
        "KRAKEN FUTURES TRENDLINE "
        f"LIVE SIGNAL SCANNER V{VERSION}"
    )

    print(
        "=" * 70
    )

    print(
        "MODE: PAPER ONLY"
    )

    print(
        f"MIN TRENDLINE TOUCHES: "
        f"{MIN_TRENDLINE_TOUCHES}"
    )

    print(
        f"RVOL MIN: {RVOL_MIN}"
    )

    print(
        f"MIN RR: {MIN_RR}"
    )

    print(
        "=" * 70
    )

    # --------------------------------------------------------
    # Safety
    # --------------------------------------------------------

    if REAL_TRADING:

        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    # --------------------------------------------------------
    # DB migration FIRST
    # --------------------------------------------------------

    init_db()

    reset_stats()

    # --------------------------------------------------------
    # Reconcile existing open trades
    # --------------------------------------------------------

    try:

        closed = (
            update_open_trades()
        )

        if closed:

            send_close_alerts(
                closed
            )

    except Exception:

        traceback.print_exc()

    # --------------------------------------------------------
    # Scan
    # --------------------------------------------------------

    new_signals = []

    for asset in ASSETS:

        SCAN_STATS[
            "assets_scanned"
        ] += 1

        try:

            print(
                f"[SCAN] {asset}"
            )

            signal = scan_asset(
                asset
            )

            if signal:

                print(
                    "[SIGNAL] "
                    f"{signal['asset']} "
                    f"{signal['direction']} "
                    f"Entry="
                    f"{signal['entry']}"
                )

                new_signals.append(
                    signal
                )

                # ------------------------------------------------
                # Maximum one new signal
                # per scan
                # ------------------------------------------------

                if (
                    len(new_signals)
                    >= MAX_SIGNALS_PER_SCAN
                ):

                    break

        except Exception:

            print(
                f"[ERROR] {asset}"
            )

            traceback.print_exc()

    # --------------------------------------------------------
    # New signal alerts
    # --------------------------------------------------------

    for signal in new_signals:

        try:

            send_new_signal_alert(
                signal
            )

        except Exception:

            traceback.print_exc()

    # --------------------------------------------------------
    # Unified report
    # --------------------------------------------------------

    try:

        send_periodic_report()

    except Exception:

        traceback.print_exc()

    # --------------------------------------------------------
    # Console report
    # --------------------------------------------------------

    print()
    print(
        scan_summary()
    )

    print()

    print(
        format_open_trades()
    )

    print()

    print(
        format_performance()
    )

    print()

    print(
        "=" * 70
    )

    print(
        "SCAN FINISHED"
    )

    print(
        "=" * 70
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
