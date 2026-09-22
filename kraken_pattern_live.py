import os, io, json, sqlite3, traceback, time
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


# ============================================================
# KRAKEN FUTURES TRENDLINE LIVE SIGNAL SCANNER
# VERSION 7.2.5
# ============================================================
#
# PAPER ONLY
# REAL_TRADING = False
#
# STRATEGY
#
# 1H:
#   Valid swing points
#        ↓
#   Trendline with >= 3 touches
#        ↓
#   Closed-candle breakout
#
# 15M:
#   Trendline with >= 3 touches
#        ↓
#   Closed-candle breakout
#        ↓
#   RVOL confirmation
#        ↓
#   ENTRY
#
# RISK:
#
# LONG:
#   SL = latest valid 15M swing LOW before entry
#   TP = nearest valid 15M swing HIGH above entry
#
# SHORT:
#   SL = latest valid 15M swing HIGH before entry
#   TP = nearest valid 15M swing LOW below entry
#
# CHART:
#   15M candles
#   Trendline
#   All trendline touches
#   Breakout
#   Entry
#   SL
#   TP
#
# ============================================================


VERSION = "7.2.5"

REAL_TRADING = False
PAPER_TRADING = True

DB_FILE = "kraken_pattern_live_v52.db"

KRAKEN_TICKER_URL = (
    "https://futures.kraken.com/derivatives/api/v3/tickers"
)

KRAKEN_CHART_URL = (
    "https://futures.kraken.com/api/charts/v1/trade"
)

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
    "XBT", "ETH", "SOL", "XRP", "LTC",
    "DOGE", "ADA", "LINK", "AVAX", "DOT",
    "BNB", "TRX", "UNI", "AAVE", "SUI",
    "NEAR", "ATOM", "FIL", "ARB", "OP",
    "BCH", "ETC", "XMR", "XLM", "ALGO",
    "ICP", "INJ", "TIA", "SEI", "RUNE",
    "CRV", "HBAR", "HYPE", "ENA", "FET",
    "KAS", "STX", "JUP", "PEPE", "WIF"
]


CONTRACT_MAP = {
    a: f"PF_{a}USD"
    for a in ASSETS
}


# ============================================================
# TIMEZONE
# ============================================================

TEHRAN_TZ = timezone(
    timedelta(
        hours=3,
        minutes=30
    )
)


# ============================================================
# STRATEGY SETTINGS
# ============================================================

PIVOT_LEFT = 3
PIVOT_RIGHT = 3

MIN_TRENDLINE_BARS = 8

TRENDLINE_TOLERANCE_PCT = 0.20

BREAKOUT_BUFFER_PCT = 0.05

MIN_TRENDLINE_TOUCHES = 3

RVOL_LOOKBACK = 20
RVOL_MIN = 1.20

MIN_RR = 1.01

# Small structural SL buffer.
# 0.05% = 0.0005
SL_BUFFER_PCT = 0.05 / 100

MAX_HOLD_HOURS = 48

MAX_SIGNALS_PER_SCAN = 1

REQUEST_TIMEOUT = 20
HTTP_RETRIES = 3
RETRY_SLEEP = 1.0

OHLC_COUNT = 300


# ============================================================
# HTTP HEADERS
# ============================================================

HEADERS = {
    "User-Agent": f"KrakenTrendlineScanner/{VERSION}"
}


# ============================================================
# SCAN STATS
# ============================================================

SCAN_STATS = {
    "assets_scanned": 0,

    "ohlc_1h_ok": 0,
    "ohlc_1h_failed": 0,

    "ohlc_15m_ok": 0,
    "ohlc_15m_failed": 0,

    "trendlines_1h": 0,
    "breakouts_1h": 0,

    "trendlines_15m": 0,
    "breakouts_15m": 0,

    "rvol_confirmed": 0,

    "new_signals": 0
}


FAILURES = []

OHLC_FAILURES = {}

TOP_CANDIDATE = None

TICKERS_CACHE = {}


# ============================================================
# TIME / HELPERS
# ============================================================

def now_utc():

    return datetime.now(
        timezone.utc
    )


def now_tehran():

    return now_utc().astimezone(
        TEHRAN_TZ
    )


def safe_float(
    v,
    default=None
):

    try:

        return (
            default
            if v is None
            else float(v)
        )

    except:

        return default


def format_time(dt=None):

    try:

        if dt is None:
            dt = now_tehran()

        if isinstance(dt, str):

            dt = (
                pd.to_datetime(
                    dt,
                    utc=True
                )
                .to_pydatetime()
            )

        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return (
            dt.astimezone(
                TEHRAN_TZ
            )
            .strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )

    except:

        return str(dt)


def duration_text(seconds):

    try:

        s = max(
            0,
            int(seconds)
        )

    except:

        return "-"

    d = s // 86400

    h = (
        s % 86400
    ) // 3600

    m = (
        s % 3600
    ) // 60

    if d:

        return (
            f"{d}d "
            f"{h}h "
            f"{m}m"
        )

    if h:

        return (
            f"{h}h "
            f"{m}m"
        )

    return f"{m}m"


def pct_change(
    entry,
    current
):

    if not entry or not current:
        return 0.0

    return (
        (current - entry)
        /
        entry
        *
        100
    )


def contract_for_asset(asset):

    return CONTRACT_MAP.get(
        asset,
        f"PF_{asset}USD"
    )


# ============================================================
# HTTP
# ============================================================

def http_get(
    url,
    params=None
):

    last = None

    for attempt in range(
        HTTP_RETRIES
    ):

        try:

            r = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT
            )

            if r.status_code == 200:

                return r.json()

            last = (
                f"HTTP {r.status_code}"
            )

        except Exception as e:

            last = str(e)

        if attempt < HTTP_RETRIES - 1:

            time.sleep(
                RETRY_SLEEP *
                (attempt + 1)
            )

    print(
        f"[HTTP ERROR] {url} -> {last}"
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

    if not isinstance(
        data.get("tickers"),
        list
    ):
        return {}

    return {
        x.get("symbol"): x
        for x in data["tickers"]
        if x.get("symbol")
    }


def get_current_price(asset):

    item = TICKERS_CACHE.get(
        contract_for_asset(asset)
    )

    if not item:

        item = get_tickers().get(
            contract_for_asset(asset)
        )

    if not item:
        return None

    for k in (
        "last",
        "lastPrice",
        "markPrice",
        "indexPrice"
    ):

        v = safe_float(
            item.get(k)
        )

        if (
            v is not None
            and
            v > 0
        ):

            return v

    return None


# ============================================================
# OHLC
# ============================================================

def get_ohlc(
    asset,
    interval
):

    symbol = contract_for_asset(
        asset
    )

    url = (
        f"{KRAKEN_CHART_URL}/"
        f"{symbol}/"
        f"{interval}"
    )

    last_reason = (
        "empty response"
    )

    for attempt in range(
        HTTP_RETRIES
    ):

        data = http_get(
            url,
            {
                "count": OHLC_COUNT
            }
        )

        candles = (
            data.get("candles")
            if isinstance(
                data,
                dict
            )
            else
            data
            if isinstance(
                data,
                list
            )
            else
            None
        )

        if candles:

            rows = []

            for c in candles:

                try:

                    if isinstance(
                        c,
                        dict
                    ):

                        ts = c.get(
                            "time",
                            c.get(
                                "timestamp",
                                c.get("ts")
                            )
                        )

                        op = c.get("open")
                        hi = c.get("high")
                        lo = c.get("low")
                        cl = c.get("close")

                        vol = c.get(
                            "volume",
                            c.get(
                                "vol",
                                0
                            )
                        )

                    else:

                        if len(c) < 5:
                            continue

                        ts, op, hi, lo, cl = (
                            c[:5]
                        )

                        vol = (
                            c[5]
                            if len(c) > 5
                            else 0
                        )

                    ts = float(ts)

                    if ts > 10_000_000_000:

                        ts = ts / 1000

                    rows.append(
                        {
                            "time":
                                pd.to_datetime(
                                    ts,
                                    unit="s",
                                    utc=True
                                ),

                            "open":
                                float(op),

                            "high":
                                float(hi),

                            "low":
                                float(lo),

                            "close":
                                float(cl),

                            "volume":
                                float(
                                    vol or 0
                                )
                        }
                    )

                except:

                    continue

            if rows:

                df = (
                    pd.DataFrame(rows)
                    .sort_values("time")
                    .drop_duplicates(
                        "time"
                    )
                    .reset_index(
                        drop=True
                    )
                )

                # Remove current still-open candle.
                if len(df) >= 2:

                    df = df.iloc[
                        :-1
                    ].copy()

                if len(df) >= 50:

                    return (
                        df.reset_index(
                            drop=True
                        )
                    )

                last_reason = (
                    f"only {len(df)} "
                    "closed candles returned "
                    "(<50)"
                )

            else:

                last_reason = (
                    "response contained "
                    "no usable candles"
                )

        else:

            last_reason = (
                "HTTP/API returned "
                "no candles"
            )

        if attempt < HTTP_RETRIES - 1:

            time.sleep(
                RETRY_SLEEP *
                (attempt + 1)
            )

    OHLC_FAILURES[
        f"{asset}_{interval}"
    ] = last_reason

    return None


# ============================================================
# PIVOTS
# ============================================================

def find_pivot_highs(df):

    a = df.high.to_numpy()

    out = []

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT
    ):

        if (
            a[i]
            >
            a[
                i - PIVOT_LEFT:i
            ].max()
            and
            a[i]
            >=
            a[
                i + 1:
                i + 1 + PIVOT_RIGHT
            ].max()
        ):

            out.append(
                {
                    "index": i,
                    "time": df.iloc[i].time,
                    "price": float(
                        a[i]
                    )
                }
            )

    return out


def find_pivot_lows(df):

    a = df.low.to_numpy()

    out = []

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT
    ):

        if (
            a[i]
            <
            a[
                i - PIVOT_LEFT:i
            ].min()
            and
            a[i]
            <=
            a[
                i + 1:
                i + 1 + PIVOT_RIGHT
            ].min()
        ):

            out.append(
                {
                    "index": i,
                    "time": df.iloc[i].time,
                    "price": float(
                        a[i]
                    )
                }
            )

    return out


# ============================================================
# TRENDLINE
# ============================================================

def line_price(
    p1,
    p2,
    index
):

    if (
        p2["index"]
        ==
        p1["index"]
    ):

        return float(
            p1["price"]
        )

    return (
        p1["price"]
        +
        (
            (
                p2["price"]
                -
                p1["price"]
            )
            /
            (
                p2["index"]
                -
                p1["index"]
            )
        )
        *
        (
            index
            -
            p1["index"]
        )
    )


def trendline_error_pct(
    price,
    line
):

    if not line:
        return 999

    return abs(
        (
            price - line
        )
        /
        line
    ) * 100


def find_valid_trendline(
    df,
    direction,
    max_lookback=120
):

    piv = (
        find_pivot_highs(df)
        if direction == "LONG"
        else
        find_pivot_lows(df)
    )

    start = max(
        0,
        len(df) - max_lookback
    )

    piv = [
        p
        for p in piv
        if p["index"] >= start
    ]

    if len(piv) < MIN_TRENDLINE_TOUCHES:

        return None

    best = None

    for a in range(
        len(piv) - 1
    ):

        for b in range(
            a + 1,
            len(piv)
        ):

            p1 = piv[a]
            p2 = piv[b]

            if (
                p2["index"]
                -
                p1["index"]
                <
                MIN_TRENDLINE_BARS
            ):

                continue

            # LONG = descending resistance
            if (
                direction == "LONG"
                and
                p2["price"]
                >=
                p1["price"]
            ):

                continue

            # SHORT = ascending support
            if (
                direction == "SHORT"
                and
                p2["price"]
                <=
                p1["price"]
            ):

                continue

            touches = []

            for p in piv:

                if (
                    p["index"]
                    <
                    p1["index"]
                ):

                    continue

                lp = line_price(
                    p1,
                    p2,
                    p["index"]
                )

                err = trendline_error_pct(
                    p["price"],
                    lp
                )

                if (
                    err
                    <=
                    TRENDLINE_TOLERANCE_PCT
                ):

                    touches.append(
                        {
                            **p,
                            "line_price": lp,
                            "error_pct": err
                        }
                    )

            if (
                len(touches)
                <
                MIN_TRENDLINE_TOUCHES
            ):

                continue

            touches = sorted(
                touches,
                key=lambda x:
                    x["index"]
            )

            a1 = touches[0]
            a2 = touches[1]

            if (
                a2["index"]
                -
                a1["index"]
                <
                MIN_TRENDLINE_BARS
            ):

                continue

            if (
                direction == "LONG"
                and
                a2["price"]
                >=
                a1["price"]
            ):

                continue

            if (
                direction == "SHORT"
                and
                a2["price"]
                <=
                a1["price"]
            ):

                continue

            final = []

            for p in piv:

                if (
                    p["index"]
                    <
                    a1["index"]
                ):

                    continue

                lp = line_price(
                    a1,
                    a2,
                    p["index"]
                )

                err = trendline_error_pct(
                    p["price"],
                    lp
                )

                if (
                    err
                    <=
                    TRENDLINE_TOLERANCE_PCT
                ):

                    final.append(
                        {
                            **p,
                            "line_price": lp,
                            "error_pct": err
                        }
                    )

            if (
                len(final)
                <
                MIN_TRENDLINE_TOUCHES
            ):

                continue

            span = (
                final[-1]["index"]
                -
                final[0]["index"]
            )

            err = sum(
                x["error_pct"]
                for x in final
            )

            score = (
                len(final) * 1000
                +
                span
                -
                err * 100
            )

            cand = {
                "direction":
                    direction,

                "p1":
                    a1,

                "p2":
                    a2,

                "touches":
                    sorted(
                        final,
                        key=lambda x:
                            x["index"]
                    ),

                "touch_count":
                    len(final),

                "score":
                    score
            }

            if (
                best is None
                or
                score
                >
                best["score"]
            ):

                best = cand

    return best


# ============================================================
# BREAKOUT
# ============================================================

def find_latest_breakout(
    df,
    trendline,
    direction,
    start_index=0
):

    if (
        not trendline
        or
        not trendline.get(
            "touches"
        )
    ):

        return None

    search_start = max(
        start_index,
        trendline[
            "touches"
        ][-1]["index"] + 1
    )

    if (
        search_start
        >=
        len(df)
    ):

        return None

    out = None

    for i in range(
        search_start,
        len(df)
    ):

        c = df.iloc[i]

        line = line_price(
            trendline["p1"],
            trendline["p2"],
            i
        )

        if direction == "LONG":

            threshold = (
                line
                *
                (
                    1
                    +
                    BREAKOUT_BUFFER_PCT
                    /
                    100
                )
            )

        else:

            threshold = (
                line
                *
                (
                    1
                    -
                    BREAKOUT_BUFFER_PCT
                    /
                    100
                )
            )

        if (
            direction == "LONG"
            and
            c.close > threshold
        ) or (
            direction == "SHORT"
            and
            c.close < threshold
        ):

            out = {
                "index":
                    i,

                "time":
                    c.time,

                "price":
                    float(
                        c.close
                    ),

                "line_price":
                    float(
                        line
                    )
            }

    return out


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(
    df,
    index
):

    if index < RVOL_LOOKBACK:

        return None

    avg = (
        df.iloc[
            index - RVOL_LOOKBACK:index
        ].volume.mean()
    )

    cur = safe_float(
        df.iloc[index].volume
    )

    if (
        cur is not None
        and
        avg
        and
        avg > 0
    ):

        return (
            cur / avg
        )

    return None


# ============================================================
# TP / SL
# ============================================================
#
# STRUCTURAL RISK MODEL
#
# LONG:
#   SL = latest confirmed swing LOW below entry
#   TP = nearest confirmed swing HIGH above entry
#
# SHORT:
#   SL = latest confirmed swing HIGH above entry
#   TP = nearest confirmed swing LOW below entry
#
# This uses only CLOSED candles before entry.
#
# ============================================================

def evaluate_tp_sl(
    df,
    direction,
    entry_index,
    entry_price
):

    highs = [
        p
        for p in find_pivot_highs(df)
        if p["index"] < entry_index
    ]

    lows = [
        p
        for p in find_pivot_lows(df)
        if p["index"] < entry_index
    ]

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        # Upper target / previous resistance point.
        tps = [
            p
            for p in highs
            if p["price"] > entry_price
        ]

        # Most recent valid lower structural point.
        sls = [
            p
            for p in lows
            if p["price"] < entry_price
        ]

        if not tps or not sls:

            return {
                "ok": False,
                "reason":
                    "No confirmed 15M "
                    "TP/SL swing available "
                    "before entry."
            }

        # Nearest upper valid pivot.
        tp_point = min(
            tps,
            key=lambda p:
                p["price"]
        )

        # Most recent confirmed lower pivot.
        sl_point = max(
            sls,
            key=lambda p:
                p["index"]
        )

        tp = float(
            tp_point["price"]
        )

        sl = (
            float(
                sl_point["price"]
            )
            *
            (
                1
                -
                SL_BUFFER_PCT
            )
        )

        reward = (
            tp
            -
            entry_price
        )

        risk = (
            entry_price
            -
            sl
        )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        # Lower target / previous support point.
        tps = [
            p
            for p in lows
            if p["price"] < entry_price
        ]

        # Most recent valid upper structural point.
        sls = [
            p
            for p in highs
            if p["price"] > entry_price
        ]

        if not tps or not sls:

            return {
                "ok": False,
                "reason":
                    "No confirmed 15M "
                    "TP/SL swing available "
                    "before entry."
            }

        # Nearest lower valid pivot.
        tp_point = max(
            tps,
            key=lambda p:
                p["price"]
        )

        # Most recent confirmed upper pivot.
        sl_point = max(
            sls,
            key=lambda p:
                p["index"]
        )

        tp = float(
            tp_point["price"]
        )

        sl = (
            float(
                sl_point["price"]
            )
            *
            (
                1
                +
                SL_BUFFER_PCT
            )
        )

        reward = (
            entry_price
            -
            tp
        )

        risk = (
            sl
            -
            entry_price
        )

    # --------------------------------------------------------
    # VALIDATION
    # --------------------------------------------------------

    if risk <= 0:

        return {
            "ok": False,
            "reason":
                "Calculated structural "
                "risk is not positive.",
            "tp": tp,
            "sl": sl
        }

    if reward <= 0:

        return {
            "ok": False,
            "reason":
                "Calculated structural "
                "reward is not positive.",
            "tp": tp,
            "sl": sl
        }

    rr = (
        reward
        /
        risk
    )

    result = {
        "ok": rr >= MIN_RR,

        "tp": tp,
        "sl": sl,

        "rr": rr,

        "tp_point":
            tp_point,

        "sl_point":
            sl_point,

        "tp_pct":
            (
                reward
                /
                entry_price
                *
                100
            ),

        "sl_pct":
            (
                risk
                /
                entry_price
                *
                100
            )
    }

    if rr < MIN_RR:

        result["reason"] = (
            f"RR {rr:.2f} "
            f"< minimum {MIN_RR:.2f}"
        )

    else:

        result["reason"] = (
            "Qualified structural "
            "TP/SL and RR."
        )

    return result


# ============================================================
# SERIALIZATION
# ============================================================

def serialize_trendline(t):

    if not t:
        return None

    return json.dumps(
        {
            "direction":
                t["direction"],

            "touch_count":
                len(t["touches"]),

            "touches": [
                {
                    "label":
                        f"P{i}",

                    "index":
                        int(
                            p["index"]
                        ),

                    "time":
                        str(
                            p["time"]
                        ),

                    "price":
                        float(
                            p["price"]
                        ),

                    "line_price":
                        float(
                            p["line_price"]
                        ),

                    "error_pct":
                        float(
                            p["error_pct"]
                        )
                }

                for i, p
                in enumerate(
                    t["touches"],
                    1
                )
            ],

            "p1":
                t["p1"],

            "p2":
                t["p2"]
        },
        default=str
    )


# ============================================================
# DATABASE
# ============================================================

def db_connect():

    c = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    c.row_factory = sqlite3.Row

    return c


def init_db():

    c = db_connect()

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS signals(
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

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS scanner_meta(
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

    cols = {
        r["name"]
        for r in c.execute(
            "PRAGMA table_info(signals)"
        ).fetchall()
    }

    defs = {
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
        "breakout_5m_time": "TEXT",
        "trendline_5m": "TEXT",
        "rvol": "REAL"
    }

    for col, typ in defs.items():

        if col not in cols:

            print(
                f"[DB MIGRATION] "
                f"Adding missing column: {col}"
            )

            c.execute(
                f"""
                ALTER TABLE signals
                ADD COLUMN {col} {typ}
                """
            )

    for r in c.execute(
        """
        SELECT id, asset
        FROM signals
        WHERE contract IS NULL
        OR contract=''
        """
    ).fetchall():

        c.execute(
            """
            UPDATE signals
            SET contract=?
            WHERE id=?
            """,
            (
                contract_for_asset(
                    r["asset"]
                ),
                r["id"]
            )
        )

    c.commit()

    c.close()


# ============================================================
# PERFORMANCE START
# ============================================================

def performance_start():

    key = (
        f"performance_start_"
        f"{VERSION.replace('.', '_')}"
    )

    c = db_connect()

    r = c.execute(
        """
        SELECT value
        FROM scanner_meta
        WHERE key=?
        """,
        (key,)
    ).fetchone()

    if not r:

        value = (
            now_utc()
            .isoformat()
        )

        c.execute(
            """
            INSERT INTO scanner_meta(
                key,
                value
            )
            VALUES(?,?)
            """,
            (
                key,
                value
            )
        )

        c.commit()

    else:

        value = r["value"]

    c.close()

    return value


# ============================================================
# OPEN TRADE CHECK
# ============================================================

def has_open_trade(
    c,
    asset,
    direction
):

    return (
        c.execute(
            """
            SELECT id
            FROM signals
            WHERE asset=?
            AND direction=?
            AND status='OPEN'
            LIMIT 1
            """,
            (
                asset,
                direction
            )
        ).fetchone()
        is not None
    )


# ============================================================
# INSERT SIGNAL
# ============================================================

def insert_signal(
    asset,
    direction,
    entry,
    tp,
    sl,
    rr,
    signal_time,
    t1,
    t15,
    b1,
    b15,
    rvol
):

    c = db_connect()

    try:

        if has_open_trade(
            c,
            asset,
            direction
        ):

            return None

        cur = c.execute(
            """
            INSERT INTO signals(
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
            VALUES(
                ?,?,?,?,?,?,?,?,?, 'OPEN',
                ?,?,?,?,?,?,?
            )
            """,
            (
                asset,

                contract_for_asset(
                    asset
                ),

                direction,

                "Trendline Breakout",

                entry,

                tp,

                sl,

                rr,

                str(
                    signal_time
                ),

                serialize_trendline(
                    t1
                ),

                serialize_trendline(
                    t15
                ),

                str(b1),

                str(b15),

                str(b15),

                serialize_trendline(
                    t15
                ),

                rvol
            )
        )

        c.commit()

        return cur.lastrowid

    except:

        c.rollback()

        traceback.print_exc()

        return None

    finally:

        c.close()


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades():

    c = db_connect()

    closed = []

    try:

        rows = c.execute(
            """
            SELECT *
            FROM signals
            WHERE status='OPEN'
            ORDER BY id
            """
        ).fetchall()

        for row in rows:

            d = dict(row)

            asset = d.get(
                "asset"
            )

            direction = d.get(
                "direction"
            )

            entry = safe_float(
                d.get(
                    "entry_price"
                )
            )

            tp = safe_float(
                d.get("tp")
            )

            sl = safe_float(
                d.get("sl")
            )

            if (
                not asset
                or
                direction
                not in (
                    "LONG",
                    "SHORT"
                )
                or
                None
                in (
                    entry,
                    tp,
                    sl
                )
            ):

                print(
                    "[DB WARNING] "
                    f"Ignoring malformed "
                    f"OPEN trade "
                    f"ID={d.get('id')}"
                )

                continue

            cur = get_current_price(
                asset
            )

            if cur is None:
                continue

            try:

                opened = (
                    pd.to_datetime(
                        d.get(
                            "signal_time"
                        ),
                        utc=True
                    )
                    .to_pydatetime()
                )

            except:

                opened = now_utc()

            dur = int(
                (
                    now_utc()
                    -
                    opened
                ).total_seconds()
            )

            reason = None
            exitp = None

            if direction == "LONG":

                if cur <= sl:

                    exitp = cur
                    reason = "SL"

                elif cur >= tp:

                    exitp = cur
                    reason = "TP"

            else:

                if cur >= sl:

                    exitp = cur
                    reason = "SL"

                elif cur <= tp:

                    exitp = cur
                    reason = "TP"

            if (
                reason is None
                and
                dur
                >=
                MAX_HOLD_HOURS * 3600
            ):

                exitp = cur
                reason = "TIME"

            if reason:

                c.execute(
                    """
                    UPDATE signals
                    SET
                        status='CLOSED',
                        exit_price=?,
                        exit_time=?,
                        exit_reason=?,
                        duration_seconds=?
                    WHERE id=?
                    """,
                    (
                        exitp,

                        now_utc()
                        .isoformat(),

                        reason,

                        dur,

                        d["id"]
                    )
                )

                closed.append(
                    {
                        "id":
                            d["id"],

                        "asset":
                            asset,

                        "direction":
                            direction,

                        "entry":
                            entry,

                        "exit":
                            exitp,

                        "reason":
                            reason,

                        "duration":
                            dur
                    }
                )

        c.commit()

    except:

        c.rollback()

        traceback.print_exc()

    finally:

        c.close()

    return closed


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():

    c = db_connect()

    out = []

    try:

        rows = c.execute(
            """
            SELECT *
            FROM signals
            WHERE status='OPEN'
            ORDER BY id DESC
            """
        ).fetchall()

        for row in rows:

            d = dict(row)

            entry = safe_float(
                d.get(
                    "entry_price"
                )
            )

            tp = safe_float(
                d.get("tp")
            )

            sl = safe_float(
                d.get("sl")
            )

            if (
                not d.get("asset")
                or
                d.get("direction")
                not in (
                    "LONG",
                    "SHORT"
                )
                or
                None
                in (
                    entry,
                    tp,
                    sl
                )
            ):

                continue

            d["current_price"] = (
                get_current_price(
                    d["asset"]
                )
            )

            try:

                d["duration_seconds"] = int(
                    (
                        now_utc()
                        -
                        pd.to_datetime(
                            d[
                                "signal_time"
                            ],
                            utc=True
                        )
                        .to_pydatetime()
                    ).total_seconds()
                )

            except:

                d["duration_seconds"] = 0

            out.append(d)

    finally:

        c.close()

    return out


# ============================================================
# PERFORMANCE
# ============================================================

def get_performance():

    c = db_connect()

    try:

        start = performance_start()

        rows = c.execute(
            """
            SELECT
                entry_price,
                exit_price,
                direction,
                exit_reason
            FROM signals
            WHERE status='CLOSED'
            AND signal_time>=?
            AND exit_price IS NOT NULL
            AND entry_price IS NOT NULL
            """,
            (start,)
        ).fetchall()

        total = 0
        wins = 0
        losses = 0
        pnl = 0.0

        for r in rows:

            total += 1

            if r["exit_reason"] == "TP":

                wins += 1

            elif r["exit_reason"] == "SL":

                losses += 1

            if r["direction"] == "LONG":

                pnl += (
                    (
                        r["exit_price"]
                        -
                        r["entry_price"]
                    )
                    /
                    r["entry_price"]
                    *
                    100
                )

            else:

                pnl += (
                    (
                        r["entry_price"]
                        -
                        r["exit_price"]
                    )
                    /
                    r["entry_price"]
                    *
                    100
                )

        decided = (
            wins
            +
            losses
        )

        return {
            "total":
                total,

            "wins":
                wins,

            "losses":
                losses,

            "winrate":
                (
                    wins
                    /
                    decided
                    *
                    100
                )
                if decided
                else 0,

            "pnl":
                pnl,

            "start":
                start
        }

    finally:

        c.close()


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send_message(
    message
):

    if (
        not TELEGRAM_BOT_TOKEN
        or
        not TELEGRAM_CHAT_ID
    ):

        return False

    try:

        r = requests.post(
            (
                "https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/"
                "sendMessage"
            ),
            data={
                "chat_id":
                    TELEGRAM_CHAT_ID,

                "text":
                    message,

                "disable_web_page_preview":
                    True
            },
            timeout=REQUEST_TIMEOUT
        )

        return (
            r.status_code == 200
        )

    except:

        return False


def telegram_send_photo(
    image_bytes,
    caption
):

    if (
        not TELEGRAM_BOT_TOKEN
        or
        not TELEGRAM_CHAT_ID
    ):

        return False

    try:

        r = requests.post(
            (
                "https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/"
                "sendPhoto"
            ),
            data={
                "chat_id":
                    TELEGRAM_CHAT_ID,

                "caption":
                    caption
            },
            files={
                "photo": (
                    "chart.png",
                    image_bytes,
                    "image/png"
                )
            },
            timeout=REQUEST_TIMEOUT
        )

        return (
            r.status_code == 200
        )

    except:

        return False


# ============================================================
# FORMAT OPEN TRADES
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

    for t in trades:

        entry = t[
            "entry_price"
        ]

        cur = t[
            "current_price"
        ]

        ch = (
            pct_change(
                entry,
                cur
            )
            if cur
            else 0
        )

        if (
            t["direction"]
            ==
            "SHORT"
        ):

            ch = -ch

        lines += [
            (
                f"\n"
                f"{'🟢' if t['direction']=='LONG' else '🔴'} "
                f"{t['direction']} "
                f"{t['asset']}"
            ),

            f"Entry: {entry:.8g}",

            (
                f"Current: "
                f"{cur:.8g} "
                f"({ch:+.2f}%)"
                if cur
                else
                "Current: -"
            ),

            f"SL: {t['sl']:.8g}",

            f"TP: {t['tp']:.8g}",

            (
                "Duration: "
                +
                duration_text(
                    t[
                        "duration_seconds"
                    ]
                )
            )
        ]

    return "\n".join(lines)


# ============================================================
# FORMAT PERFORMANCE
# ============================================================

def format_performance():

    p = get_performance()

    return (
        f"PERFORMANCE\n"
        f"Since V{VERSION}\n\n"
        f"Total: {p['total']}\n"
        f"Wins: {p['wins']}\n"
        f"Losses: {p['losses']}\n"
        f"Win Rate: {p['winrate']:.2f}%\n"
        f"Total PnL: {p['pnl']:+.2f}%"
    )


# ============================================================
# TOP CANDIDATE
# ============================================================

def format_top_candidate():

    c = TOP_CANDIDATE

    if not c:

        return (
            "🔎 TOP CANDIDATE\n"
            "None\n\n"
            "❌ No setup reached "
            "the first trendline/"
            "breakout stage."
        )

    lines = [
        "🔎 TOP CANDIDATE",

        (
            f"{c['asset']} "
            f"{'🟢 LONG' if c['direction']=='LONG' else '🔴 SHORT'}"
        ),

        f"Stage: {c['stage']}",

        f"Touches: {c.get('touches', '-')}"
    ]

    if c.get("entry") is not None:

        lines.append(
            f"Entry: "
            f"{c['entry']:.8g}"
        )

    if c.get("sl") is not None:

        lines.append(
            f"SL: "
            f"{c['sl']:.8g}"
        )

    if c.get("tp") is not None:

        lines.append(
            f"TP: "
            f"{c['tp']:.8g}"
        )

    if c.get("sl_pct") is not None:

        lines.append(
            f"SL Distance: "
            f"{c['sl_pct']:.2f}%"
        )

    if c.get("tp_pct") is not None:

        lines.append(
            f"TP Distance: "
            f"{c['tp_pct']:.2f}%"
        )

    if c.get("rvol") is not None:

        lines.append(
            f"RVOL: "
            f"{c['rvol']:.2f}"
        )

    if c.get("rr") is not None:

        lines.append(
            f"RR: "
            f"{c['rr']:.2f}"
        )

    lines += [
        "",
        "❌ REJECTED",
        c["reason"]
    ]

    return "\n".join(lines)


# ============================================================
# SCAN SUMMARY
# ============================================================

def scan_summary():

    return f"""📊 SCAN SUMMARY

Version: {VERSION}
Mode: PAPER ONLY
Time: Tehran

Assets Scanned: {SCAN_STATS['assets_scanned']}/{len(ASSETS)}
1H OHLC OK: {SCAN_STATS['ohlc_1h_ok']}/{len(ASSETS)}
1H OHLC FAILED: {SCAN_STATS['ohlc_1h_failed']}
15M OHLC OK: {SCAN_STATS['ohlc_15m_ok']}/{len(ASSETS)}
15M OHLC FAILED: {SCAN_STATS['ohlc_15m_failed']}
1H Trendlines: {SCAN_STATS['trendlines_1h']}
1H Breakouts: {SCAN_STATS['breakouts_1h']}
15M Trendlines: {SCAN_STATS['trendlines_15m']}
15M Breakouts: {SCAN_STATS['breakouts_15m']}
RVOL Confirmed: {SCAN_STATS['rvol_confirmed']}
New Signals: {SCAN_STATS['new_signals']}"""


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
    sl
):

    if (
        df is None
        or
        df.empty
        or
        not trendline
        or
        not breakout
    ):

        return None

    touches = trendline.get(
        "touches",
        []
    )

    inds = [
        int(
            p["index"]
        )
        for p in touches
    ]

    inds.append(
        int(
            breakout["index"]
        )
    )

    # Include SL/TP pivot indices if available.
    try:

        if TOP_CANDIDATE:

            for key in (
                "tp_point",
                "sl_point"
            ):

                point = TOP_CANDIDATE.get(
                    key
                )

                if point:

                    inds.append(
                        int(
                            point["index"]
                        )
                    )

    except:

        pass

    left = max(
        0,
        min(inds) - 25
    )

    right = min(
        len(df),
        max(inds) + 30
    )

    # Never show too little chart.
    if (
        right - left
        <
        70
    ):

        center = int(
            (
                left
                +
                right
            ) / 2
        )

        left = max(
            0,
            center - 35
        )

        right = min(
            len(df),
            center + 35
        )

    cd = df.iloc[
        left:right
    ].copy()

    if cd.empty:
        return None

    fig, ax = plt.subplots(
        figsize=(16, 9)
    )

    # --------------------------------------------------------
    # CANDLES
    # --------------------------------------------------------

    for x, (_, c) in enumerate(
        cd.iterrows()
    ):

        op = float(c.open)
        hi = float(c.high)
        lo = float(c.low)
        cl = float(c.close)

        bullish = cl >= op

        # Wick
        ax.plot(
            [x, x],
            [lo, hi],
            linewidth=1
        )

        # Body
        body_low = min(
            op,
            cl
        )

        body_height = max(
            abs(cl - op),
            1e-12
        )

        rect = Rectangle(
            (
                x - 0.32,
                body_low
            ),
            0.64,
            body_height,
            fill=True,
            alpha=0.75
        )

        ax.add_patch(rect)

    # --------------------------------------------------------
    # TRENDLINE
    # --------------------------------------------------------

    xs = list(
        range(
            left,
            right
        )
    )

    ys = [
        line_price(
            trendline["p1"],
            trendline["p2"],
            i
        )
        for i in xs
    ]

    ax.plot(
        [
            x - left
            for x in xs
        ],
        ys,
        linewidth=2.5,
        label=(
            f"15M Trendline "
            f"({len(touches)} touches)"
        )
    )

    # --------------------------------------------------------
    # TOUCH POINTS
    # --------------------------------------------------------

    for n, p in enumerate(
        touches,
        1
    ):

        i = int(
            p["index"]
        )

        if (
            left
            <=
            i
            <
            right
        ):

            x = i - left

            ax.scatter(
                [x],
                [p["price"]],
                s=90,
                zorder=8
            )

            ax.annotate(
                f"P{n}",
                (
                    x,
                    p["price"]
                ),
                xytext=(
                    0,
                    12
                ),
                textcoords=(
                    "offset points"
                ),
                ha="center",
                fontsize=10,
                fontweight="bold"
            )

    # --------------------------------------------------------
    # BREAKOUT
    # --------------------------------------------------------

    bi = int(
        breakout["index"]
    )

    if (
        left
        <=
        bi
        <
        right
    ):

        bx = bi - left

        ax.scatter(
            [bx],
            [breakout["price"]],
            marker="*",
            s=260,
            zorder=12,
            label="BREAKOUT"
        )

        ax.annotate(
            "BREAKOUT",
            (
                bx,
                breakout["price"]
            ),
            xytext=(
                0,
                20
            ),
            textcoords=(
                "offset points"
            ),
            ha="center",
            fontsize=11,
            fontweight="bold"
        )

    # --------------------------------------------------------
    # ENTRY / TP / SL
    # --------------------------------------------------------

    ax.axhline(
        entry,
        linestyle="-.",
        linewidth=1.8,
        label=(
            f"ENTRY "
            f"{entry:.8g}"
        )
    )

    ax.axhline(
        tp,
        linestyle="--",
        linewidth=1.8,
        label=(
            f"TP "
            f"{tp:.8g}"
        )
    )

    ax.axhline(
        sl,
        linestyle="--",
        linewidth=1.8,
        label=(
            f"SL "
            f"{sl:.8g}"
        )
    )

    # --------------------------------------------------------
    # TP / SL PIVOT MARKERS
    # --------------------------------------------------------

    try:

        if TOP_CANDIDATE:

            tp_point = TOP_CANDIDATE.get(
                "tp_point"
            )

            sl_point = TOP_CANDIDATE.get(
                "sl_point"
            )

            if tp_point:

                ti = int(
                    tp_point["index"]
                )

                if (
                    left
                    <=
                    ti
                    <
                    right
                ):

                    tx = ti - left

                    ax.scatter(
                        [tx],
                        [tp_point["price"]],
                        marker="D",
                        s=75,
                        zorder=10
                    )

                    ax.annotate(
                        "TP POINT",
                        (
                            tx,
                            tp_point["price"]
                        ),
                        xytext=(
                            0,
                            -22
                        ),
                        textcoords=(
                            "offset points"
                        ),
                        ha="center",
                        fontsize=9,
                        fontweight="bold"
                    )

            if sl_point:

                si = int(
                    sl_point["index"]
                )

                if (
                    left
                    <=
                    si
                    <
                    right
                ):

                    sx = si - left

                    ax.scatter(
                        [sx],
                        [sl_point["price"]],
                        marker="D",
                        s=75,
                        zorder=10
                    )

                    ax.annotate(
                        "SL POINT",
                        (
                            sx,
                            sl_point["price"]
                        ),
                        xytext=(
                            0,
                            -22
                        ),
                        textcoords=(
                            "offset points"
                        ),
                        ha="center",
                        fontsize=9,
                        fontweight="bold"
                    )

    except:

        pass

    # --------------------------------------------------------
    # ENTRY MARKER
    # --------------------------------------------------------

    entry_index = int(
        breakout["index"]
    )

    if (
        left
        <=
        entry_index
        <
        right
    ):

        ex = (
            entry_index
            -
            left
        )

        ax.scatter(
            [ex],
            [entry],
            marker="o",
            s=100,
            zorder=12,
            label="ENTRY"
        )

    # --------------------------------------------------------
    # TIME AXIS
    # --------------------------------------------------------

    step = max(
        1,
        len(cd) // 10
    )

    ticks = list(
        range(
            0,
            len(cd),
            step
        )
    )

    labels = []

    for i in ticks:

        try:

            ts = pd.Timestamp(
                cd.iloc[i].time
            )

            if ts.tzinfo is None:

                ts = ts.tz_localize(
                    timezone.utc
                )

            labels.append(
                ts.tz_convert(
                    TEHRAN_TZ
                )
                .strftime(
                    "%m-%d %H:%M"
                )
            )

        except:

            labels.append(
                str(
                    cd.iloc[i].time
                )
            )

    ax.set_xticks(
        ticks
    )

    ax.set_xticklabels(
        labels,
        rotation=35,
        ha="right"
    )

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    rr_text = ""

    if (
        entry
        and
        tp
        and
        sl
    ):

        if direction == "LONG":

            risk = (
                entry - sl
            )

            reward = (
                tp - entry
            )

        else:

            risk = (
                sl - entry
            )

            reward = (
                entry - tp
            )

        if risk > 0:

            rr_text = (
                f" | RR "
                f"{reward / risk:.2f}"
            )

    ax.set_title(
        (
            f"KRAKEN 15M | "
            f"{asset} | "
            f"{'🟢 LONG' if direction == 'LONG' else '🔴 SHORT'} "
            f"Trendline Breakout"
            f"{rr_text}"
        ),
        fontsize=14,
        fontweight="bold"
    )

    ax.set_xlabel(
        "15M Closed Candles"
    )

    ax.set_ylabel(
        "Price"
    )

    ax.grid(
        True,
        alpha=0.25
    )

    ax.legend(
        loc="best",
        fontsize=9
    )

    plt.tight_layout()

    image = io.BytesIO()

    plt.savefig(
        image,
        format="png",
        dpi=160,
        bbox_inches="tight"
    )

    plt.close(fig)

    image.seek(0)

    return image.getvalue()


# ============================================================
# CLOSE ALERTS
# ============================================================

def send_close_alerts(
    closed
):

    for t in closed:

        result = pct_change(
            t["entry"],
            t["exit"]
        )

        if (
            t["direction"]
            ==
            "SHORT"
        ):

            result = -result

        telegram_send_message(
            f"🔔 CLOSE {t['asset']}\n"
            f"{'🟢' if t['direction']=='LONG' else '🔴'} "
            f"{t['direction']}\n"
            f"Entry: {t['entry']:.8g}\n"
            f"Exit: {t['exit']:.8g}\n"
            f"Result: {result:+.2f}%\n"
            f"Reason: {t['reason']}\n"
            f"Duration: "
            f"{duration_text(t['duration'])}"
        )


# ============================================================
# NEW SIGNAL ALERT
# ============================================================

def send_new_signal_alert(s):

    telegram_send_message(
        "🚨 NEW SIGNAL\n\n"
        +
        (
            f"{'🟢 LONG' if s['direction']=='LONG' else '🔴 SHORT'} "
            f"{s['asset']}\n"
        )
        +
        f"Entry: {s['entry']:.8g}\n"
        f"SL: {s['sl']:.8g}\n"
        f"TP: {s['tp']:.8g}\n"
        f"RR: {s['rr']:.2f}\n"
        f"SL Distance: "
        f"{s.get('sl_pct', 0):.2f}%\n"
        f"TP Distance: "
        f"{s.get('tp_pct', 0):.2f}%\n"
        f"RVOL: {s['rvol']:.2f}\n"
        f"Time: "
        f"{format_time(s['signal_time'])}"
    )

    if s.get("chart"):

        telegram_send_photo(
            s["chart"],
            (
                f"{'🟢 LONG' if s['direction']=='LONG' else '🔴 SHORT'} "
                f"{s['asset']} | 15M\n"
                f"Trendline + Touches + Breakout\n"
                f"Entry: {s['entry']:.8g}\n"
                f"SL: {s['sl']:.8g}\n"
                f"TP: {s['tp']:.8g}\n"
                f"RR: {s['rr']:.2f}\n"
                f"SL: {s.get('sl_pct', 0):.2f}%\n"
                f"TP: {s.get('tp_pct', 0):.2f}%\n"
                f"RVOL: {s['rvol']:.2f}"
            )
        )


# ============================================================
# CANDIDATE CHART ALERT
# ============================================================

def send_candidate_chart():

    c = TOP_CANDIDATE

    if not c:
        return

    chart = c.get(
        "chart"
    )

    if not chart:
        return

    direction = c[
        "direction"
    ]

    caption = (
        f"🔎 TOP CANDIDATE | "
        f"{c['asset']} | 15M\n"
        f"{'🟢 LONG' if direction == 'LONG' else '🔴 SHORT'}\n"
        f"Stage: {c['stage']}\n"
        f"Touches: "
        f"{c.get('touches', '-')}\n"
    )

    if c.get("entry") is not None:

        caption += (
            f"Entry: "
            f"{c['entry']:.8g}\n"
        )

    if c.get("sl") is not None:

        caption += (
            f"SL: "
            f"{c['sl']:.8g}\n"
        )

    if c.get("tp") is not None:

        caption += (
            f"TP: "
            f"{c['tp']:.8g}\n"
        )

    if c.get("rr") is not None:

        caption += (
            f"RR: "
            f"{c['rr']:.2f}\n"
        )

    if c.get("rvol") is not None:

        caption += (
            f"RVOL: "
            f"{c['rvol']:.2f}\n"
        )

    caption += (
        f"\nStatus: "
        f"{c['reason']}"
    )

    telegram_send_photo(
        chart,
        caption
    )


# ============================================================
# CANDIDATE TRACKING
# ============================================================

def consider_candidate(
    asset,
    direction,
    stage,
    reason,
    touches=0,
    rvol=None,
    rr=None,
    entry=None,
    tp=None,
    sl=None,
    sl_pct=None,
    tp_pct=None,
    tp_point=None,
    sl_point=None,
    df15=None,
    trendline=None,
    breakout=None
):

    global TOP_CANDIDATE

    stage_rank = {
        "1H TRENDLINE": 1,
        "1H BREAKOUT": 2,
        "15M DATA": 2,
        "15M TRENDLINE": 3,
        "15M BREAKOUT": 4,
        "RVOL": 5,
        "RISK": 6,
        "READY": 7
    }

    score = (
        stage_rank.get(
            stage,
            0
        )
        *
        10000
        +
        touches * 100
        +
        (rvol or 0) * 10
        +
        (rr or 0)
    )

    c = {
        "asset":
            asset,

        "direction":
            direction,

        "stage":
            stage,

        "reason":
            reason,

        "touches":
            touches,

        "rvol":
            rvol,

        "rr":
            rr,

        "entry":
            entry,

        "tp":
            tp,

        "sl":
            sl,

        "sl_pct":
            sl_pct,

        "tp_pct":
            tp_pct,

        "tp_point":
            tp_point,

        "sl_point":
            sl_point,

        "score":
            score,

        "df15":
            df15,

        "trendline":
            trendline,

        "breakout":
            breakout
    }

    if (
        TOP_CANDIDATE is None
        or
        score
        >
        TOP_CANDIDATE["score"]
    ):

        TOP_CANDIDATE = c

        # Create candidate chart immediately.
        if (
            df15 is not None
            and
            trendline is not None
            and
            breakout is not None
            and
            entry is not None
            and
            tp is not None
            and
            sl is not None
        ):

            try:

                TOP_CANDIDATE[
                    "chart"
                ] = create_chart(
                    asset,
                    df15,
                    direction,
                    trendline,
                    breakout,
                    entry,
                    tp,
                    sl
                )

            except Exception:

                TOP_CANDIDATE[
                    "chart"
                ] = None

                traceback.print_exc()


# ============================================================
# SCAN ONE ASSET
# ============================================================

def scan_asset(asset):

    # --------------------------------------------------------
    # 1H DATA
    # --------------------------------------------------------

    df1 = get_ohlc(
        asset,
        "1h"
    )

    if df1 is None:

        reason = OHLC_FAILURES.get(
            f"{asset}_1h",
            "unknown API/data error"
        )

        SCAN_STATS[
            "ohlc_1h_failed"
        ] += 1

        FAILURES.append(
            f"{asset} 1H: {reason}"
        )

        return None

    SCAN_STATS[
        "ohlc_1h_ok"
    ] += 1

    # --------------------------------------------------------
    # 15M DATA
    # --------------------------------------------------------

    df15 = get_ohlc(
        asset,
        "15m"
    )

    if df15 is None:

        reason = OHLC_FAILURES.get(
            f"{asset}_15m",
            "unknown API/data error"
        )

        SCAN_STATS[
            "ohlc_15m_failed"
        ] += 1

        FAILURES.append(
            f"{asset} 15M: {reason}"
        )

        consider_candidate(
            asset,
            "LONG",
            "15M DATA",
            (
                "15M OHLC unavailable: "
                f"{reason}"
            )
        )

        return None

    SCAN_STATS[
        "ohlc_15m_ok"
    ] += 1

    # --------------------------------------------------------
    # 1H TRENDLINES
    # --------------------------------------------------------

    lines = {}

    for d in (
        "LONG",
        "SHORT"
    ):

        lines[d] = find_valid_trendline(
            df1,
            d
        )

        if lines[d]:

            SCAN_STATS[
                "trendlines_1h"
            ] += 1

    # --------------------------------------------------------
    # 1H BREAKOUTS
    # --------------------------------------------------------

    b1s = []

    for d in (
        "LONG",
        "SHORT"
    ):

        if lines[d]:

            b = find_latest_breakout(
                df1,
                lines[d],
                d
            )

            if b:

                b1s.append(
                    (
                        b,
                        d,
                        lines[d]
                    )
                )

                SCAN_STATS[
                    "breakouts_1h"
                ] += 1

    if not b1s:

        for d in (
            "LONG",
            "SHORT"
        ):

            if lines[d]:

                consider_candidate(
                    asset,
                    d,
                    "1H TRENDLINE",
                    (
                        "No valid 1H "
                        "closed-candle "
                        "breakout after "
                        f"the last "
                        f"{lines[d]['touch_count']}-touch "
                        "trendline."
                    ),
                    lines[d][
                        "touch_count"
                    ]
                )

        return None

    # Latest 1H breakout
    b1, d, t1 = max(
        b1s,
        key=lambda x:
            x[0]["index"]
    )

    # --------------------------------------------------------
    # EXISTING OPEN TRADE
    # --------------------------------------------------------

    c = db_connect()

    try:

        if has_open_trade(
            c,
            asset,
            d
        ):

            consider_candidate(
                asset,
                d,
                "1H BREAKOUT",
                (
                    "Existing OPEN "
                    "trade in the "
                    "same direction."
                ),
                t1[
                    "touch_count"
                ]
            )

            return None

    finally:

        c.close()

    # --------------------------------------------------------
    # 15M TRENDLINE
    # --------------------------------------------------------

    t15 = find_valid_trendline(
        df15,
        d
    )

    if not t15:

        consider_candidate(
            asset,
            d,
            "1H BREAKOUT",
            (
                "No valid 15M "
                "trendline with at "
                "least 3 confirmed "
                "touches."
            ),
            t1[
                "touch_count"
            ]
        )

        return None

    SCAN_STATS[
        "trendlines_15m"
    ] += 1

    # --------------------------------------------------------
    # START AFTER 1H BREAKOUT
    # --------------------------------------------------------

    start = next(
        (
            i
            for i in range(
                len(df15)
            )
            if
            pd.Timestamp(
                df15.iloc[i].time
            )
            >=
            pd.Timestamp(
                b1["time"]
            )
        ),
        0
    )

    # --------------------------------------------------------
    # 15M BREAKOUT
    # --------------------------------------------------------

    b15 = find_latest_breakout(
        df15,
        t15,
        d,
        start
    )

    if not b15:

        consider_candidate(
            asset,
            d,
            "15M TRENDLINE",
            (
                "No valid 15M "
                "closed-candle "
                "breakout after "
                "the 1H breakout."
            ),
            t15[
                "touch_count"
            ]
        )

        return None

    SCAN_STATS[
        "breakouts_15m"
    ] += 1

    # --------------------------------------------------------
    # RVOL
    # --------------------------------------------------------

    rvol = calculate_rvol(
        df15,
        b15["index"]
    )

    if rvol is None:

        consider_candidate(
            asset,
            d,
            "15M BREAKOUT",
            (
                "RVOL could not be "
                "calculated at the "
                "15M breakout candle."
            ),
            t15[
                "touch_count"
            ]
        )

        return None

    if rvol < RVOL_MIN:

        consider_candidate(
            asset,
            d,
            "RVOL",
            (
                f"RVOL {rvol:.2f} "
                f"< minimum "
                f"{RVOL_MIN:.2f}."
            ),
            t15[
                "touch_count"
            ],
            rvol
        )

        return None

    SCAN_STATS[
        "rvol_confirmed"
    ] += 1

    # --------------------------------------------------------
    # ENTRY
    # --------------------------------------------------------

    entry_index = b15[
        "index"
    ]

    entry = float(
        df15.iloc[
            entry_index
        ].close
    )

    # --------------------------------------------------------
    # TP / SL
    # --------------------------------------------------------

    risk = evaluate_tp_sl(
        df15,
        d,
        entry_index,
        entry
    )

    # --------------------------------------------------------
    # RISK REJECT
    # --------------------------------------------------------

    if not risk["ok"]:

        consider_candidate(
            asset,
            d,
            "RISK",
            risk["reason"],
            t15[
                "touch_count"
            ],
            rvol,
            risk.get("rr"),
            entry,
            risk.get("tp"),
            risk.get("sl"),
            risk.get("sl_pct"),
            risk.get("tp_pct"),
            risk.get("tp_point"),
            risk.get("sl_point"),
            df15,
            t15,
            b15
        )

        return None

    # --------------------------------------------------------
    # QUALIFIED
    # --------------------------------------------------------

    consider_candidate(
        asset,
        d,
        "READY",
        "Qualified candidate.",
        t15[
            "touch_count"
        ],
        rvol,
        risk["rr"],
        entry,
        risk["tp"],
        risk["sl"],
        risk.get("sl_pct"),
        risk.get("tp_pct"),
        risk.get("tp_point"),
        risk.get("sl_point"),
        df15,
        t15,
        b15
    )

    return {
        "asset":
            asset,

        "direction":
            d,

        "entry":
            entry,

        "tp":
            risk["tp"],

        "sl":
            risk["sl"],

        "rr":
            risk["rr"],

        "rvol":
            rvol,

        "sl_pct":
            risk.get(
                "sl_pct"
            ),

        "tp_pct":
            risk.get(
                "tp_pct"
            ),

        "tp_point":
            risk.get(
                "tp_point"
            ),

        "sl_point":
            risk.get(
                "sl_point"
            ),

        "signal_time":
            df15.iloc[
                entry_index
            ].time,

        "t1":
            t1,

        "t15":
            t15,

        "b1":
            b1,

        "b15":
            b15,

        "df15":
            df15
    }


# ============================================================
# RESET RUN STATS
# ============================================================

def reset_stats():

    global TOP_CANDIDATE
    global FAILURES
    global OHLC_FAILURES

    TOP_CANDIDATE = None

    FAILURES = []

    OHLC_FAILURES = {}

    for k in SCAN_STATS:

        SCAN_STATS[k] = 0


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=" * 70
    )

    print(
        "KRAKEN FUTURES "
        "TRENDLINE LIVE SIGNAL "
        f"SCANNER V{VERSION}"
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
        "TP/SL MODEL: "
        "STRUCTURAL 15M SWINGS"
    )

    print(
        "CHART: "
        "15M TRENDLINE + TOUCHES + BREAKOUT"
    )

    print(
        "=" * 70
    )

    if REAL_TRADING:

        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    # --------------------------------------------------------
    # DB
    # --------------------------------------------------------

    init_db()

    reset_stats()

    # --------------------------------------------------------
    # TICKERS
    # --------------------------------------------------------

    global TICKERS_CACHE

    TICKERS_CACHE = get_tickers()

    # --------------------------------------------------------
    # UPDATE OPEN TRADES
    # --------------------------------------------------------

    closed = update_open_trades()

    if closed:

        send_close_alerts(
            closed
        )

    # --------------------------------------------------------
    # SCAN ALL 40 ASSETS
    # --------------------------------------------------------

    candidates = []

    for asset in ASSETS:

        SCAN_STATS[
            "assets_scanned"
        ] += 1

        print(
            f"[SCAN] {asset}"
        )

        try:

            s = scan_asset(
                asset
            )

            if s:

                candidates.append(
                    s
                )

        except Exception:

            print(
                f"[ERROR] {asset}"
            )

            traceback.print_exc()

    # --------------------------------------------------------
    # INSERT BEST QUALIFIED SIGNAL
    # --------------------------------------------------------

    new = []

    candidates = sorted(
        candidates,
        key=lambda x: (
            x["rr"],
            x["rvol"]
        ),
        reverse=True
    )

    for s in candidates[
        :MAX_SIGNALS_PER_SCAN
    ]:

        sid = insert_signal(
            s["asset"],
            s["direction"],
            s["entry"],
            s["tp"],
            s["sl"],
            s["rr"],
            s["signal_time"],
            s["t1"],
            s["t15"],
            s["b1"]["time"],
            s["b15"]["time"],
            s["rvol"]
        )

        if sid:

            SCAN_STATS[
                "new_signals"
            ] += 1

            s["id"] = sid

            s["chart"] = create_chart(
                s["asset"],
                s["df15"],
                s["direction"],
                s["t15"],
                s["b15"],
                s["entry"],
                s["tp"],
                s["sl"]
            )

            new.append(s)

    # --------------------------------------------------------
    # SEND NEW SIGNALS
    # --------------------------------------------------------

    for s in new:

        send_new_signal_alert(
            s
        )

    # --------------------------------------------------------
    # TELEGRAM REPORT
    # --------------------------------------------------------

    report = (
        scan_summary()
        +
        "\n\n"
    )

    if (
        SCAN_STATS[
            "new_signals"
        ]
        ==
        0
    ):

        report += (
            format_top_candidate()
            +
            "\n\n"
        )

    # --------------------------------------------------------
    # 15M DATA ERRORS
    # --------------------------------------------------------

    if (
        FAILURES
        and
        SCAN_STATS[
            "ohlc_15m_failed"
        ] > 0
    ):

        report += (
            "15M DATA ERRORS\n"
            +
            "\n".join(
                "• " + x
                for x in FAILURES
                if "15M:" in x
            )[
                :3500
            ]
            +
            "\n\n"
        )

    # --------------------------------------------------------
    # OPEN TRADES + PERFORMANCE
    # --------------------------------------------------------

    report += (
        format_open_trades()
        +
        "\n\n"
        +
        format_performance()
    )

    telegram_send_message(
        report
    )

    # --------------------------------------------------------
    # TOP CANDIDATE CHART
    # --------------------------------------------------------
    #
    # If there is no NEW signal, send the chart
    # of the best candidate, including rejected
    # RISK candidates.
    #
    # --------------------------------------------------------

    if (
        SCAN_STATS[
            "new_signals"
        ]
        ==
        0
    ):

        send_candidate_chart()

    # --------------------------------------------------------
    # CONSOLE
    # --------------------------------------------------------

    print()

    print(
        scan_summary()
    )

    print()

    print(
        format_top_candidate()
    )

    print()

    print(
        format_open_trades()
    )

    print()

    print(
        format_performance()
    )

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
# RUN
# ============================================================

if __name__ == "__main__":

    main()
