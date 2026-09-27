# ============================================================
# NDS M30 -> M1 LIVE SCANNER
# VERSION 4.2.0
# ============================================================
#
# PAPER ONLY
#
# ============================================================
# STRATEGY
# ============================================================
#
# M30 POSITIVE HOOK
#
#       H1
#        \
#         L1
#           \
#            H2
#             \
#              L2
#                \
#                 H3
#
# CONDITIONS:
#
#   H2 > H1
#   L2 < L1
#   H3 > H2
#
# AFTER H3:
#   monitor M1 for an upward corrective structure.
#
#
# M1 POSITIVE CORRECTION:
#
#   H1 -> L1 -> H2 -> L2 -> H3 -> L3 -> F
#
#   H1 < H2 < H3 < F
#
# F = newest confirmed M1 HIGH
#
# WHEN F IS CONFIRMED:
#
#   SHORT
#
# SL:
#   slightly above previous M1 highs
#
# TP:
#   previous M30 low
#
#
# ============================================================
# M30 NEGATIVE HOOK
# ============================================================
#
#       L1
#        \
#         H1
#           \
#            L2
#             \
#              H2
#                \
#                 L3
#
# CONDITIONS:
#
#   L2 < L1
#   H2 > H1
#   L3 < L2
#
# AFTER L3:
#   monitor M1 for a downward corrective structure.
#
#
# M1 NEGATIVE CORRECTION:
#
#   L1 -> H1 -> L2 -> H2 -> L3 -> H3 -> F
#
#   L1 > L2 > L3 > F
#
# F = newest confirmed M1 LOW
#
# WHEN F IS CONFIRMED:
#
#   LONG
#
# SL:
#   slightly below previous M1 lows
#
# TP:
#   previous M30 high
#
#
# ============================================================
# IMPORTANT
# ============================================================
#
# F is a CONFIRMED M1 pivot.
#
# Entry happens only after F is confirmed.
#
# F must also be fresh.
#
# ACTIVE HOOK CHART:
#
# Every NEW/CHANGED active M30 hook gets a chart.
#
# POSITIVE:
#   1 = H1
#   2 = L1
#   3 = H2
#   4 = L2
#   5 = H3
#
# NEGATIVE:
#   1 = L1
#   2 = H1
#   3 = L2
#   4 = H2
#   5 = L3
#
# No Kraken order API is used.
# PAPER ONLY.
#
# ============================================================

import os
import time
import sqlite3
import traceback
from datetime import datetime, timezone

import requests

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "4.2.0"

PAPER_ONLY = True

DB_FILE = "nds_m30_m1_v40.db"
CHART_DIR = "nds_charts"

KRAKEN_CHART_BASE = (
    "https://futures.kraken.com/api/charts/v1"
)

KRAKEN_REST_BASE = (
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


# ============================================================
# ASSETS
# ============================================================

TARGET_ASSETS = 100

ASSET_REFRESH_SECONDS = 3600


# ============================================================
# M30
# ============================================================

M30_CANDLES = 320

M30_SCAN_SECONDS = 300

M30_TIMEFRAME_MS = 30 * 60 * 1000


# ============================================================
# M1
# ============================================================

M1_CANDLES = 1500

M1_SCAN_SECONDS = 60

M1_TIMEFRAME_MS = 60 * 1000

M1_F_MAX_AGE_MINUTES = 5


# ============================================================
# PIVOTS
# ============================================================

PIVOT_LEFT = 2
PIVOT_RIGHT = 2


# ============================================================
# HOOK FILTERS
# ============================================================

HOOK_MIN_RANGE_PCT = 0.20


# ============================================================
# M1 CORRECTION
# ============================================================

M1_MIN_SWING_PCT = 0.07

M1_MIN_HIGH_COUNT = 4
M1_MIN_LOW_COUNT = 4

M1_MAX_STRUCTURE_DISTANCE_PCT = 3.0


# ============================================================
# TRADE MANAGEMENT
# ============================================================

MAX_OPEN_TRADES = 3

SIGNAL_COOLDOWN_MINUTES = 60

SL_BUFFER_PCT = 0.15


# ============================================================
# REPORT
# ============================================================

REPORT_INTERVAL_SECONDS = 900


# ============================================================
# NETWORK
# ============================================================

HTTP_TIMEOUT = 15

REQUEST_SLEEP = 0.05

ERROR_SLEEP = 10


# ============================================================
# CHART
# ============================================================

CHART_ENABLED = True

CHART_CANDLES = 240

ACTIVE_HOOK_CHART_CANDLES = 100


# ============================================================
# GLOBALS
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent":
            "NDS-M30-M1-Scanner/4.2.0",
        "Accept":
            "application/json",
    }
)

ACTIVE_ASSETS = []

ACTIVE_HOOKS = {}

LAST_M30_SCAN = 0

LAST_ASSET_REFRESH = 0

LAST_REPORT_TIME = 0

LAST_M1_SCAN = 0


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

    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS scanner_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at INTEGER NOT NULL,
            finished_at INTEGER,
            assets_scanned INTEGER DEFAULT 0,
            active_hooks INTEGER DEFAULT 0,
            new_signals INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            hook_type TEXT NOT NULL,
            hook_time INTEGER NOT NULL,
            f_time INTEGER NOT NULL,
            entry REAL NOT NULL,
            sl REAL NOT NULL,
            tp1 REAL NOT NULL,
            tp2 REAL NOT NULL,
            tp3 REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            current_price REAL,
            pnl_pct REAL,
            result TEXT,
            exit_price REAL,
            exit_time INTEGER,
            exit_reason TEXT,
            created_at INTEGER NOT NULL,
            UNIQUE(symbol, side, f_time)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_events (
            symbol TEXT NOT NULL,
            event_key TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY(symbol, event_key)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS hooks (
            symbol TEXT PRIMARY KEY,
            hook_type TEXT NOT NULL,
            hook_time INTEGER NOT NULL,
            direction TEXT NOT NULL,
            h3_time INTEGER,
            l3_time INTEGER,
            active INTEGER NOT NULL DEFAULT 1,
            updated_at INTEGER NOT NULL
        )
        """
    )

    conn.commit()

    conn.close()


# ============================================================
# HTTP
# ============================================================

def get_json(
    url,
    params=None,
):

    try:

        response = session.get(
            url,
            params=params,
            timeout=HTTP_TIMEOUT,
        )

        response.raise_for_status()

        return response.json()

    except Exception as exc:

        print(
            f"[HTTP ERROR] {url}: {exc}"
        )

        return None


# ============================================================
# TIME
# ============================================================

def now_ms():

    return int(
        time.time() * 1000
    )


def fmt_time(ms):

    if not ms:
        return "-"

    try:

        dt = datetime.fromtimestamp(
            ms / 1000,
            tz=timezone.utc,
        )

        return dt.strftime(
            "%Y-%m-%d %H:%M UTC"
        )

    except Exception:

        return "-"


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(
    text,
    photo_path=None,
):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):
        return False

    try:

        if (
            photo_path
            and os.path.exists(photo_path)
        ):

            url = (
                "https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/"
                "sendPhoto"
            )

            with open(
                photo_path,
                "rb",
            ) as photo:

                response = requests.post(
                    url,
                    data={
                        "chat_id":
                            TELEGRAM_CHAT_ID,
                        "caption":
                            text[:1024],
                    },
                    files={
                        "photo":
                            photo,
                    },
                    timeout=20,
                )

        else:

            url = (
                "https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/"
                "sendMessage"
            )

            response = requests.post(
                url,
                data={
                    "chat_id":
                        TELEGRAM_CHAT_ID,
                    "text":
                        text,
                    "parse_mode":
                        "HTML",
                    "disable_web_page_preview":
                        True,
                },
                timeout=20,
            )

        return response.ok

    except Exception as exc:

        print(
            "Telegram error:",
            exc,
        )

        return False


# ============================================================
# KRAKEN MARKET DATA
# ============================================================

def fetch_candles(
    symbol,
    resolution,
    count,
):

    url = (
        f"{KRAKEN_CHART_BASE}/trade/"
        f"{symbol}/{resolution}"
    )

    data = get_json(
        url,
        params={
            "count": count,
        },
    )

    if not data:
        return []

    candles = data.get(
        "candles"
    )

    if not isinstance(
        candles,
        list,
    ):
        return []

    result = []

    for c in candles:

        try:

            result.append(
                {
                    "time":
                        int(c["time"]),

                    "open":
                        float(c["open"]),

                    "high":
                        float(c["high"]),

                    "low":
                        float(c["low"]),

                    "close":
                        float(c["close"]),

                    "volume":
                        float(
                            c.get(
                                "volume",
                                0,
                            )
                        ),
                }
            )

        except Exception:

            continue

    result.sort(
        key=lambda x:
            x["time"]
    )

    return result


def fetch_all_tickers():

    url = (
        f"{KRAKEN_REST_BASE}/tickers"
    )

    data = get_json(url)

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

    for ticker in tickers:

        symbol = str(
            ticker.get(
                "symbol",
                "",
            )
        ).upper()

        if not symbol:
            continue

        result[symbol] = ticker

    return result


# ============================================================
# ASSET DISCOVERY
# ============================================================

def discover_assets():

    global ACTIVE_ASSETS

    print(
        "Refreshing Kraken asset universe..."
    )

    url = (
        f"{KRAKEN_REST_BASE}/instruments"
    )

    data = get_json(url)

    instruments = []

    if data:

        instruments = data.get(
            "instruments",
            [],
        )

    if not isinstance(
        instruments,
        list,
    ):
        instruments = []

    candidates = []

    for inst in instruments:

        try:

            symbol = str(
                inst.get(
                    "symbol",
                    "",
                )
            ).upper()

            tradeable = bool(
                inst.get(
                    "tradeable",
                    False,
                )
            )

            if not tradeable:
                continue

            if not symbol.startswith(
                "PF_"
            ):
                continue

            if not symbol.endswith(
                "USD"
            ):
                continue

            candidates.append(
                symbol
            )

        except Exception:

            continue

    if not candidates:

        print(
            "Instrument discovery returned "
            "no PF contracts. "
            "Using ticker fallback."
        )

        tickers = fetch_all_tickers()

        for symbol in tickers:

            if (
                symbol.startswith("PF_")
                and symbol.endswith("USD")
            ):

                candidates.append(
                    symbol
                )

    candidates = sorted(
        set(candidates)
    )

    tickers = fetch_all_tickers()

    ranked = []

    for symbol in candidates:

        ticker = tickers.get(
            symbol,
            {},
        )

        volume_quote = ticker.get(
            "volumeQuote",
            0,
        )

        vol24h = ticker.get(
            "vol24h",
            0,
        )

        try:

            volume_quote = float(
                volume_quote or 0
            )

        except Exception:

            volume_quote = 0

        try:

            vol24h = float(
                vol24h or 0
            )

        except Exception:

            vol24h = 0

        score = (
            volume_quote
            if volume_quote > 0
            else vol24h
        )

        ranked.append(
            (
                score,
                symbol,
            )
        )

    ranked.sort(
        key=lambda x:
            x[0],
        reverse=True,
    )

    selected = [
        symbol
        for _, symbol in ranked[
            :TARGET_ASSETS
        ]
    ]

    if len(selected) < TARGET_ASSETS:

        existing = set(
            selected
        )

        for symbol in candidates:

            if symbol in existing:
                continue

            selected.append(
                symbol
            )

            existing.add(
                symbol
            )

            if (
                len(selected)
                >= TARGET_ASSETS
            ):
                break

    ACTIVE_ASSETS = selected

    print(
        f"Active assets: "
        f"{len(ACTIVE_ASSETS)}"
    )

    if ACTIVE_ASSETS:

        print(
            "Top assets:",
            ", ".join(
                ACTIVE_ASSETS[:20]
            ),
        )

    return ACTIVE_ASSETS


# ============================================================
# CLOSED CANDLES
# ============================================================

def closed_candles(
    candles,
    timeframe_ms,
):

    current = now_ms()

    result = []

    for candle in candles:

        if (
            candle["time"]
            + timeframe_ms
            <= current
        ):

            result.append(
                candle
            )

    return result


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(candles):

    highs = []

    lows = []

    n = len(candles)

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT,
    ):

        center = candles[i]

        left = candles[
            i - PIVOT_LEFT:i
        ]

        right = candles[
            i + 1:
            i + 1 + PIVOT_RIGHT
        ]

        # ----------------------------------------------------
        # HIGH
        # ----------------------------------------------------

        is_high = True

        for c in left + right:

            if (
                c["high"]
                >= center["high"]
            ):

                is_high = False

                break

        if is_high:

            highs.append(
                {
                    "kind":
                        "H",

                    "index":
                        i,

                    "time":
                        center["time"],

                    "price":
                        center["high"],
                }
            )

        # ----------------------------------------------------
        # LOW
        # ----------------------------------------------------

        is_low = True

        for c in left + right:

            if (
                c["low"]
                <= center["low"]
            ):

                is_low = False

                break

        if is_low:

            lows.append(
                {
                    "kind":
                        "L",

                    "index":
                        i,

                    "time":
                        center["time"],

                    "price":
                        center["low"],
                }
            )

    return highs, lows


# ============================================================
# COMBINED PIVOTS
# ============================================================

def combined_pivots(
    highs,
    lows,
):

    pivots = (
        highs
        + lows
    )

    pivots.sort(
        key=lambda x:
            x["time"]
    )

    cleaned = []

    for p in pivots:

        if not cleaned:

            cleaned.append(p)

            continue

        last = cleaned[-1]

        if (
            p["kind"]
            != last["kind"]
        ):

            cleaned.append(p)

            continue

        # Same type.
        # Keep the more extreme pivot.

        if p["kind"] == "H":

            if (
                p["price"]
                > last["price"]
            ):

                cleaned[-1] = p

        else:

            if (
                p["price"]
                < last["price"]
            ):

                cleaned[-1] = p

    return cleaned


# ============================================================
# PERCENT DISTANCE
# ============================================================

def pct_distance(
    a,
    b,
):

    if a == 0:
        return 0

    return (
        abs(
            (b - a) / a
        )
        * 100.0
    )


# ============================================================
# M30 POSITIVE HOOK
# ============================================================

def detect_positive_hook(
    pivots,
):

    """
    H1 -> L1 -> H2 -> L2 -> H3

    H2 > H1
    L2 < L1
    H3 > H2
    """

    if len(pivots) < 5:
        return None

    for end in range(
        len(pivots) - 1,
        3,
        -1,
    ):

        seq = pivots[
            end - 4:
            end + 1
        ]

        if [
            p["kind"]
            for p in seq
        ] != [
            "H",
            "L",
            "H",
            "L",
            "H",
        ]:

            continue

        h1, l1, h2, l2, h3 = seq

        if (
            h2["price"]
            <= h1["price"]
        ):
            continue

        if (
            l2["price"]
            >= l1["price"]
        ):
            continue

        if (
            h3["price"]
            <= h2["price"]
        ):
            continue

        hook_range = pct_distance(
            l2["price"],
            h3["price"],
        )

        if (
            hook_range
            < HOOK_MIN_RANGE_PCT
        ):
            continue

        return {
            "type":
                "POSITIVE",

            "h1":
                h1,

            "l1":
                l1,

            "h2":
                h2,

            "l2":
                l2,

            "h3":
                h3,

            "hook_time":
                h3["time"],

            "target_price":
                l2["price"],
        }

    return None


# ============================================================
# M30 NEGATIVE HOOK
# ============================================================

def detect_negative_hook(
    pivots,
):

    """
    L1 -> H1 -> L2 -> H2 -> L3

    L2 < L1
    H2 > H1
    L3 < L2
    """

    if len(pivots) < 5:
        return None

    for end in range(
        len(pivots) - 1,
        3,
        -1,
    ):

        seq = pivots[
            end - 4:
            end + 1
        ]

        if [
            p["kind"]
            for p in seq
        ] != [
            "L",
            "H",
            "L",
            "H",
            "L",
        ]:

            continue

        l1, h1, l2, h2, l3 = seq

        if (
            l2["price"]
            >= l1["price"]
        ):
            continue

        if (
            h2["price"]
            <= h1["price"]
        ):
            continue

        if (
            l3["price"]
            >= l2["price"]
        ):
            continue

        hook_range = pct_distance(
            h2["price"],
            l3["price"],
        )

        if (
            hook_range
            < HOOK_MIN_RANGE_PCT
        ):
            continue

        return {
            "type":
                "NEGATIVE",

            "l1":
                l1,

            "h1":
                h1,

            "l2":
                l2,

            "h2":
                h2,

            "l3":
                l3,

            "hook_time":
                l3["time"],

            "target_price":
                h2["price"],
        }

    return None


# ============================================================
# ACTIVE M30 HOOK
# ============================================================

def determine_active_hook(
    candles,
):

    candles = closed_candles(
        candles,
        M30_TIMEFRAME_MS,
    )

    if len(candles) < 20:
        return None

    highs, lows = find_pivots(
        candles
    )

    pivots = combined_pivots(
        highs,
        lows,
    )

    if len(pivots) < 5:
        return None

    positive = detect_positive_hook(
        pivots
    )

    negative = detect_negative_hook(
        pivots
    )

    candidates = []

    if positive:
        candidates.append(
            positive
        )

    if negative:
        candidates.append(
            negative
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x:
            x["hook_time"],
        reverse=True,
    )

    hook = candidates[0]

    # --------------------------------------------------------
    # POSITIVE
    # --------------------------------------------------------

    if hook["type"] == "POSITIVE":

        h3_time = hook[
            "h3"
        ]["time"]

        for p in pivots:

            if (
                p["kind"] == "L"
                and p["time"] > h3_time
            ):

                hook[
                    "window_end"
                ] = p["time"]

                return None

        hook[
            "window_end"
        ] = None

    # --------------------------------------------------------
    # NEGATIVE
    # --------------------------------------------------------

    else:

        l3_time = hook[
            "l3"
        ]["time"]

        for p in pivots:

            if (
                p["kind"] == "H"
                and p["time"] > l3_time
            ):

                hook[
                    "window_end"
                ] = p["time"]

                return None

        hook[
            "window_end"
        ] = None

    return hook


# ============================================================
# M1 PIVOTS
# ============================================================

def get_m1_pivots(
    candles,
):

    closed = closed_candles(
        candles,
        M1_TIMEFRAME_MS,
    )

    highs, lows = find_pivots(
        closed
    )

    pivots = combined_pivots(
        highs,
        lows,
    )

    return closed, pivots


# ============================================================
# M1 FRESHNESS
# ============================================================

def is_fresh_f(
    f_time,
):

    age_ms = (
        now_ms()
        - f_time
    )

    return (
        0
        <= age_ms
        <=
        M1_F_MAX_AGE_MINUTES
        * 60
        * 1000
    )


# ============================================================
# M1 POSITIVE CORRECTION
# ============================================================

def detect_positive_m1_correction(
    candles,
    hook,
):

    """
    Positive M30 Hook -> SHORT

    H1 -> L1 -> H2 -> L2
    -> H3 -> L3 -> F

    H1 < H2 < H3 < F
    """

    if not hook:
        return None

    hook_time = hook[
        "hook_time"
    ]

    work = [
        c
        for c in candles
        if c["time"] > hook_time
    ]

    closed, pivots = get_m1_pivots(
        work
    )

    if len(pivots) < 7:
        return None

    for end in range(
        len(pivots) - 1,
        5,
        -1,
    ):

        f = pivots[end]

        if f["kind"] != "H":
            continue

        if not is_fresh_f(
            f["time"]
        ):
            continue

        seq = pivots[
            end - 6:
            end + 1
        ]

        expected = [
            "H",
            "L",
            "H",
            "L",
            "H",
            "L",
            "H",
        ]

        if [
            p["kind"]
            for p in seq
        ] != expected:

            continue

        h1, l1, h2, l2, h3, l3, fp = seq

        if not (
            h2["price"] > h1["price"]
            and
            h3["price"] > h2["price"]
            and
            fp["price"] > h3["price"]
        ):
            continue

        swing_pct = pct_distance(
            h1["price"],
            fp["price"],
        )

        if (
            swing_pct
            < M1_MIN_SWING_PCT
        ):
            continue

        distance = pct_distance(
            h3["price"],
            fp["price"],
        )

        if (
            distance
            > M1_MAX_STRUCTURE_DISTANCE_PCT
        ):
            continue

        return {
            "type":
                "POSITIVE_M1_123F",

            "side":
                "SHORT",

            "h1":
                h1,

            "l1":
                l1,

            "h2":
                h2,

            "l2":
                l2,

            "h3":
                h3,

            "l3":
                l3,

            "f":
                fp,

            "f_time":
                fp["time"],

            "f_price":
                fp["price"],

            "sl_highs": [
                h1,
                h2,
                h3,
            ],
        }

    return None


# ============================================================
# M1 NEGATIVE CORRECTION
# ============================================================

def detect_negative_m1_correction(
    candles,
    hook,
):

    """
    Negative M30 Hook -> LONG

    L1 -> H1 -> L2 -> H2
    -> L3 -> H3 -> F

    L1 > L2 > L3 > F
    """

    if not hook:
        return None

    hook_time = hook[
        "hook_time"
    ]

    work = [
        c
        for c in candles
        if c["time"] > hook_time
    ]

    closed, pivots = get_m1_pivots(
        work
    )

    if len(pivots) < 7:
        return None

    for end in range(
        len(pivots) - 1,
        5,
        -1,
    ):

        f = pivots[end]

        if f["kind"] != "L":
            continue

        if not is_fresh_f(
            f["time"]
        ):
            continue

        seq = pivots[
            end - 6:
            end + 1
        ]

        expected = [
            "L",
            "H",
            "L",
            "H",
            "L",
            "H",
            "L",
        ]

        if [
            p["kind"]
            for p in seq
        ] != expected:

            continue

        l1, h1, l2, h2, l3, h3, fp = seq

        if not (
            l2["price"] < l1["price"]
            and
            l3["price"] < l2["price"]
            and
            fp["price"] < l3["price"]
        ):
            continue

        swing_pct = pct_distance(
            l1["price"],
            fp["price"],
        )

        if (
            swing_pct
            < M1_MIN_SWING_PCT
        ):
            continue

        distance = pct_distance(
            l3["price"],
            fp["price"],
        )

        if (
            distance
            > M1_MAX_STRUCTURE_DISTANCE_PCT
        ):
            continue

        return {
            "type":
                "NEGATIVE_M1_123F",

            "side":
                "LONG",

            "l1":
                l1,

            "h1":
                h1,

            "l2":
                l2,

            "h2":
                h2,

            "l3":
                l3,

            "h3":
                h3,

            "f":
                fp,

            "f_time":
                fp["time"],

            "f_price":
                fp["price"],

            "sl_lows": [
                l1,
                l2,
                l3,
            ],
        }

    return None


# ============================================================
# STRUCTURAL SL / TP
# ============================================================

def calculate_trade_levels(
    side,
    entry,
    setup,
    hook,
):

    if side == "SHORT":

        highs = setup.get(
            "sl_highs",
            [],
        )

        if not highs:
            return None

        highest_high = max(
            p["price"]
            for p in highs
        )

        sl = (
            highest_high
            *
            (
                1.0
                +
                SL_BUFFER_PCT / 100.0
            )
        )

        tp = float(
            hook["target_price"]
        )

        if (
            tp >= entry
            or
            sl <= entry
        ):
            return None

        return {
            "entry":
                float(entry),

            "sl":
                sl,

            "tp1":
                tp,

            "tp2":
                tp,

            "tp3":
                tp,
        }

    lows = setup.get(
        "sl_lows",
        [],
    )

    if not lows:
        return None

    lowest_low = min(
        p["price"]
        for p in lows
    )

    sl = (
        lowest_low
        *
        (
            1.0
            -
            SL_BUFFER_PCT / 100.0
        )
    )

    tp = float(
        hook["target_price"]
    )

    if (
        tp <= entry
        or
        sl >= entry
    ):
        return None

    return {
        "entry":
            float(entry),

        "sl":
            sl,

        "tp1":
            tp,

        "tp2":
            tp,

        "tp3":
            tp,
    }


# ============================================================
# DUPLICATE
# ============================================================

def already_processed(
    symbol,
    event_key,
):

    conn = db_connect()

    row = conn.execute(
        """
        SELECT 1
        FROM processed_events
        WHERE symbol = ?
          AND event_key = ?
        LIMIT 1
        """,
        (
            symbol,
            event_key,
        ),
    ).fetchone()

    conn.close()

    return row is not None


def mark_processed(
    symbol,
    event_key,
):

    conn = db_connect()

    conn.execute(
        """
        INSERT OR IGNORE INTO processed_events
        (
            symbol,
            event_key,
            created_at
        )
        VALUES (?, ?, ?)
        """,
        (
            symbol,
            event_key,
            now_ms(),
        ),
    )

    conn.commit()

    conn.close()


# ============================================================
# COOLDOWN
# ============================================================

def cooldown_active(
    symbol,
    side,
):

    conn = db_connect()

    row = conn.execute(
        """
        SELECT created_at
        FROM signals
        WHERE symbol = ?
          AND side = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (
            symbol,
            side,
        ),
    ).fetchone()

    conn.close()

    if not row:
        return False

    age_ms = (
        now_ms()
        -
        int(row["created_at"])
    )

    return (
        age_ms
        <
        SIGNAL_COOLDOWN_MINUTES
        *
        60
        *
        1000
    )


# ============================================================
# OPEN TRADE COUNT
# ============================================================

def open_trade_count():

    conn = db_connect()

    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM signals
        WHERE status = 'OPEN'
        """
    ).fetchone()

    conn.close()

    return int(
        row["n"]
    )


# ============================================================
# GET OPEN TRADES
# ============================================================

def get_open_trades():

    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY created_at ASC
        """
    ).fetchall()

    conn.close()

    return rows


# ============================================================
# SAVE ACTIVE HOOK
# ============================================================

def save_active_hook(
    symbol,
    hook,
):

    conn = db_connect()

    if hook["type"] == "POSITIVE":

        hook_time = hook[
            "h3"
        ]["time"]

        h3_time = hook[
            "h3"
        ]["time"]

        l3_time = None

    else:

        hook_time = hook[
            "l3"
        ]["time"]

        h3_time = None

        l3_time = hook[
            "l3"
        ]["time"]

    conn.execute(
        """
        INSERT INTO hooks
        (
            symbol,
            hook_type,
            hook_time,
            direction,
            h3_time,
            l3_time,
            active,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, ?)

        ON CONFLICT(symbol)
        DO UPDATE SET
            hook_type = excluded.hook_type,
            hook_time = excluded.hook_time,
            direction = excluded.direction,
            h3_time = excluded.h3_time,
            l3_time = excluded.l3_time,
            active = 1,
            updated_at = excluded.updated_at
        """,
        (
            symbol,
            hook["type"],
            hook_time,
            (
                "SHORT"
                if hook["type"]
                == "POSITIVE"
                else "LONG"
            ),
            h3_time,
            l3_time,
            now_ms(),
        ),
    )

    conn.commit()

    conn.close()


# ============================================================
# MARK HOOK INACTIVE
# ============================================================

def mark_hook_inactive(
    symbol,
):

    conn = db_connect()

    conn.execute(
        """
        UPDATE hooks
        SET active = 0,
            updated_at = ?
        WHERE symbol = ?
        """,
        (
            now_ms(),
            symbol,
        ),
    )

    conn.commit()

    conn.close()


# ============================================================
# HOOK EVENT KEY
# ============================================================

def hook_event_key(
    symbol,
    hook,
):

    return (
        f"HOOK_"
        f"{hook['type']}_"
        f"{hook['hook_time']}"
    )


# ============================================================
# ACTIVE HOOK CHART
# ============================================================

def save_active_hook_chart(
    symbol,
    hook,
    candles,
):

    """
    Creates an M30 chart for the ACTIVE HOOK.

    Positive:
        1 = H1
        2 = L1
        3 = H2
        4 = L2
        5 = H3

    Negative:
        1 = L1
        2 = H1
        3 = L2
        4 = H2
        5 = L3
    """

    if not CHART_ENABLED:
        return None

    try:

        os.makedirs(
            CHART_DIR,
            exist_ok=True,
        )

        data = closed_candles(
            candles,
            M30_TIMEFRAME_MS,
        )

        if not data:
            return None

        # ----------------------------------------------------
        # Keep the latest candles while ensuring all Hook
        # points are visible.
        # ----------------------------------------------------

        hook_points = []

        if hook["type"] == "POSITIVE":

            hook_points = [
                hook["h1"],
                hook["l1"],
                hook["h2"],
                hook["l2"],
                hook["h3"],
            ]

        else:

            hook_points = [
                hook["l1"],
                hook["h1"],
                hook["l2"],
                hook["h2"],
                hook["l3"],
            ]

        first_hook_time = min(
            p["time"]
            for p in hook_points
        )

        last_hook_time = max(
            p["time"]
            for p in hook_points
        )

        start_time = min(
            first_hook_time
            -
            30
            *
            M30_TIMEFRAME_MS,
            data[-1]["time"]
            -
            ACTIVE_HOOK_CHART_CANDLES
            *
            M30_TIMEFRAME_MS,
        )

        end_time = max(
            last_hook_time
            +
            10
            *
            M30_TIMEFRAME_MS,
            data[-1]["time"],
        )

        chart_data = [
            c
            for c in data
            if (
                c["time"] >= start_time
                and
                c["time"] <= end_time
            )
        ]

        if len(chart_data) < 20:

            chart_data = data[
                -ACTIVE_HOOK_CHART_CANDLES:
            ]

        if not chart_data:
            return None

        fig, ax = plt.subplots(
            figsize=(16, 9)
        )

        # ----------------------------------------------------
        # Candle width
        # ----------------------------------------------------

        if len(chart_data) >= 2:

            spacing = (
                chart_data[-1]["time"]
                -
                chart_data[-2]["time"]
            )

            width = (
                spacing
                / 1000
                / 60
                * 0.65
            )

        else:

            width = 15.0

        # ----------------------------------------------------
        # Candles
        # ----------------------------------------------------

        for c in chart_data:

            x = (
                c["time"]
                / 1000
                / 60
            )

            o = c["open"]
            h = c["high"]
            l = c["low"]
            cl = c["close"]

            body_low = min(
                o,
                cl,
            )

            body_high = max(
                o,
                cl,
            )

            body_height = (
                body_high
                -
                body_low
            )

            # Prevent invisible zero-height bodies.
            if body_height == 0:

                body_height = (
                    max(
                        abs(h - l),
                        abs(c["close"]) * 0.00001,
                    )
                )

            ax.plot(
                [x, x],
                [l, h],
                linewidth=0.9,
                alpha=0.8,
            )

            ax.bar(
                x,
                body_height,
                bottom=body_low,
                width=width,
                align="center",
                alpha=0.70,
            )

        # ----------------------------------------------------
        # NUMBERED HOOK POINTS
        # ----------------------------------------------------

        if hook["type"] == "POSITIVE":

            numbered_points = [
                (
                    1,
                    "H1",
                    hook["h1"],
                ),
                (
                    2,
                    "L1",
                    hook["l1"],
                ),
                (
                    3,
                    "H2",
                    hook["h2"],
                ),
                (
                    4,
                    "L2",
                    hook["l2"],
                ),
                (
                    5,
                    "H3",
                    hook["h3"],
                ),
            ]

            direction = "SHORT"

        else:

            numbered_points = [
                (
                    1,
                    "L1",
                    hook["l1"],
                ),
                (
                    2,
                    "H1",
                    hook["h1"],
                ),
                (
                    3,
                    "L2",
                    hook["l2"],
                ),
                (
                    4,
                    "H2",
                    hook["h2"],
                ),
                (
                    5,
                    "L3",
                    hook["l3"],
                ),
            ]

            direction = "LONG"

        line_x = []
        line_y = []

        for number, label, p in numbered_points:

            x = (
                p["time"]
                / 1000
                / 60
            )

            y = p["price"]

            line_x.append(x)
            line_y.append(y)

            ax.scatter(
                [x],
                [y],
                s=100,
                zorder=8,
            )

            # Large visible number.
            ax.annotate(
                str(number),
                (
                    x,
                    y,
                ),
                xytext=(
                    0,
                    18,
                ),
                textcoords=
                    "offset points",
                ha="center",
                va="bottom",
                fontsize=15,
                fontweight="bold",
                zorder=10,
            )

            # Pivot name below/above the number.
            ax.annotate(
                label,
                (
                    x,
                    y,
                ),
                xytext=(
                    7,
                    -18,
                ),
                textcoords=
                    "offset points",
                fontsize=9,
                zorder=10,
            )

        # ----------------------------------------------------
        # Connect Hook structure
        # ----------------------------------------------------

        ax.plot(
            line_x,
            line_y,
            linestyle="--",
            linewidth=2.0,
            marker="o",
            markersize=4,
            zorder=6,
        )

        # ----------------------------------------------------
        # Direction arrow / label
        # ----------------------------------------------------

        last_point = numbered_points[-1][2]

        last_x = (
            last_point["time"]
            / 1000
            / 60
        )

        last_y = last_point["price"]

        ax.annotate(
            (
                f"ACTIVE HOOK\n"
                f"#{5} {numbered_points[-1][1]}\n"
                f"NEXT: {direction}"
            ),
            (
                last_x,
                last_y,
            ),
            xytext=(
                35,
                45,
            ),
            textcoords=
                "offset points",
            fontsize=10,
            fontweight="bold",
            arrowprops={
                "arrowstyle":
                    "->",
                "linewidth":
                    1.2,
            },
            bbox={
                "boxstyle":
                    "round,pad=0.4",
                "alpha":
                    0.85,
            },
            zorder=11,
        )

        # ----------------------------------------------------
        # Target level
        # ----------------------------------------------------

        target = float(
            hook["target_price"]
        )

        ax.axhline(
            target,
            linestyle=":",
            linewidth=1.4,
            label=(
                f"Previous M30 "
                f"Target {target:.8g}"
            ),
        )

        # ----------------------------------------------------
        # Title
        # ----------------------------------------------------

        hook_name = (
            "POSITIVE HOOK"
            if hook["type"]
            == "POSITIVE"
            else
            "NEGATIVE HOOK"
        )

        ax.set_title(
            (
                f"NDS M30 ACTIVE HOOK "
                f"| {symbol} "
                f"| {hook_name} "
                f"| NEXT: {direction}"
            ),
            fontsize=14,
            fontweight="bold",
        )

        ax.set_xlabel(
            "Time (UTC)"
        )

        ax.set_ylabel(
            "Price"
        )

        ax.legend(
            loc="best",
            fontsize=8,
        )

        ax.grid(
            alpha=0.2
        )

        fig.tight_layout()

        filename = (
            f"ACTIVE_HOOK_"
            f"{symbol}_"
            f"{hook['type']}_"
            f"{hook['hook_time']}.png"
        )

        path = os.path.join(
            CHART_DIR,
            filename,
        )

        fig.savefig(
            path,
            dpi=140,
        )

        plt.close(fig)

        return path

    except Exception as exc:

        print(
            "Active hook chart error:",
            exc,
        )

        try:
            plt.close("all")
        except Exception:
            pass

        return None


# ============================================================
# ACTIVE HOOK MESSAGE
# ============================================================

def active_hook_message(
    symbol,
    hook,
):

    if hook["type"] == "POSITIVE":

        direction = "🔴 SHORT"

        structure = (
            "1=H1 → "
            "2=L1 → "
            "3=H2 → "
            "4=L2 → "
            "5=H3"
        )

        last_label = "H3"

    else:

        direction = "🟢 LONG"

        structure = (
            "1=L1 → "
            "2=H1 → "
            "3=L2 → "
            "4=H2 → "
            "5=L3"
        )

        last_label = "L3"

    return (
        f"📌 "
        f"<b>ACTIVE HOOK</b>\n\n"

        f"<b>{symbol}</b>\n"

        f"Type: "
        f"{hook['type']}\n"

        f"Next: "
        f"<b>{direction}</b>\n\n"

        f"Structure:\n"
        f"{structure}\n\n"

        f"{last_label}: "
        f"<b>{hook['target_price']:.8g}</b>\n"

        f"Hook Time: "
        f"{fmt_time(hook['hook_time'])}\n\n"

        f"Waiting for M1 correction.\n"

        f"Mode: "
        f"PAPER ONLY"
    )


# ============================================================
# INSERT SIGNAL
# ============================================================

def insert_signal(
    symbol,
    hook,
    setup,
    levels,
):

    conn = db_connect()

    entry = float(
        setup["f_price"]
    )

    side = setup[
        "side"
    ]

    hook_time = hook[
        "hook_time"
    ]

    f_time = setup[
        "f_time"
    ]

    try:

        cur = conn.execute(
            """
            INSERT INTO signals
            (
                symbol,
                side,
                hook_type,
                hook_time,
                f_time,
                entry,
                sl,
                tp1,
                tp2,
                tp3,
                status,
                current_price,
                pnl_pct,
                created_at
            )
            VALUES
            (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                'OPEN',
                ?, ?, ?
            )
            """,
            (
                symbol,
                side,
                hook["type"],
                hook_time,
                f_time,
                entry,
                levels["sl"],
                levels["tp1"],
                levels["tp2"],
                levels["tp3"],
                entry,
                0.0,
                now_ms(),
            ),
        )

        signal_id = cur.lastrowid

        conn.commit()

    except sqlite3.IntegrityError:

        conn.close()

        return None

    conn.close()

    return signal_id


# ============================================================
# SIGNAL CHART
# ============================================================

def save_signal_chart(
    symbol,
    hook,
    setup,
    levels,
    candles,
    signal_id,
):

    if not CHART_ENABLED:
        return None

    try:

        os.makedirs(
            CHART_DIR,
            exist_ok=True,
        )

        data = [
            c
            for c in candles
            if c["time"]
            >=
            setup["f_time"]
            -
            (
                CHART_CANDLES
                *
                M1_TIMEFRAME_MS
            )
        ]

        if len(data) < 20:

            data = candles[
                -CHART_CANDLES:
            ]

        if not data:
            return None

        fig, ax = plt.subplots(
            figsize=(16, 9)
        )

        if len(data) >= 2:

            spacing = (
                data[-1]["time"]
                -
                data[-2]["time"]
            )

            width = (
                spacing
                / 1000
                / 60
                * 0.65
            )

        else:

            width = 0.6

        # ----------------------------------------------------
        # Candles
        # ----------------------------------------------------

        for c in data:

            x = (
                c["time"]
                / 1000
                / 60
            )

            o = c["open"]
            h = c["high"]
            l = c["low"]
            cl = c["close"]

            body_low = min(
                o,
                cl,
            )

            body_high = max(
                o,
                cl,
            )

            body_height = (
                body_high
                -
                body_low
            )

            if body_height == 0:

                body_height = (
                    max(
                        abs(h - l),
                        abs(cl) * 0.00001,
                    )
                )

            ax.plot(
                [x, x],
                [l, h],
                linewidth=0.8,
            )

            ax.bar(
                x,
                body_height,
                bottom=body_low,
                width=width,
                align="center",
                alpha=0.75,
            )

        # ----------------------------------------------------
        # M1 STRUCTURE
        # ----------------------------------------------------

        structure_keys = [
            "h1",
            "l1",
            "h2",
            "l2",
            "h3",
            "l3",
        ]

        structure_points = []

        for key in structure_keys:

            p = setup.get(
                key
            )

            if not p:
                continue

            x = (
                p["time"]
                / 1000
                / 60
            )

            y = p["price"]

            structure_points.append(
                (
                    x,
                    y,
                )
            )

            ax.scatter(
                [x],
                [y],
                s=65,
                zorder=5,
            )

            ax.annotate(
                key.upper(),
                (
                    x,
                    y,
                ),
                xytext=(
                    5,
                    8,
                ),
                textcoords=
                    "offset points",
                fontsize=9,
            )

        # ----------------------------------------------------
        # F
        # ----------------------------------------------------

        f = setup["f"]

        fx = (
            f["time"]
            / 1000
            / 60
        )

        fy = f["price"]

        ax.scatter(
            [fx],
            [fy],
            s=150,
            marker="*",
            zorder=7,
        )

        ax.annotate(
            "F",
            (
                fx,
                fy,
            ),
            xytext=(
                8,
                12,
            ),
            textcoords=
                "offset points",
            fontsize=13,
            fontweight="bold",
        )

        # ----------------------------------------------------
        # Connect same-direction pivots
        # ----------------------------------------------------

        if setup["side"] == "SHORT":

            highs = [
                setup["h1"],
                setup["h2"],
                setup["h3"],
                setup["f"],
            ]

            hx = [
                p["time"]
                / 1000
                / 60
                for p in highs
            ]

            hy = [
                p["price"]
                for p in highs
            ]

            ax.plot(
                hx,
                hy,
                linestyle="--",
                linewidth=1.0,
            )

        else:

            lows = [
                setup["l1"],
                setup["l2"],
                setup["l3"],
                setup["f"],
            ]

            lx = [
                p["time"]
                / 1000
                / 60
                for p in lows
            ]

            ly = [
                p["price"]
                for p in lows
            ]

            ax.plot(
                lx,
                ly,
                linestyle="--",
                linewidth=1.0,
            )

        # ----------------------------------------------------
        # M30 HOOK
        # ----------------------------------------------------

        m30_points = []

        if hook["type"] == "POSITIVE":

            m30_points = [
                (
                    "M30 H1",
                    hook["h1"],
                ),
                (
                    "M30 L1",
                    hook["l1"],
                ),
                (
                    "M30 H2",
                    hook["h2"],
                ),
                (
                    "M30 L2 / TP",
                    hook["l2"],
                ),
                (
                    "M30 H3",
                    hook["h3"],
                ),
            ]

        else:

            m30_points = [
                (
                    "M30 L1",
                    hook["l1"],
                ),
                (
                    "M30 H1",
                    hook["h1"],
                ),
                (
                    "M30 L2",
                    hook["l2"],
                ),
                (
                    "M30 H2 / TP",
                    hook["h2"],
                ),
                (
                    "M30 L3",
                    hook["l3"],
                ),
            ]

        first_time = data[0]["time"]
        last_time = data[-1]["time"]

        for label, p in m30_points:

            if (
                p["time"] < first_time
                or
                p["time"] > last_time
            ):
                continue

            x = (
                p["time"]
                / 1000
                / 60
            )

            y = p["price"]

            ax.scatter(
                [x],
                [y],
                s=45,
                marker="x",
                zorder=5,
            )

            ax.annotate(
                label,
                (
                    x,
                    y,
                ),
                xytext=(
                    5,
                    -12,
                ),
                textcoords=
                    "offset points",
                fontsize=8,
            )

        # ----------------------------------------------------
        # Entry
        # ----------------------------------------------------

        ax.axhline(
            levels["entry"],
            linestyle="--",
            linewidth=1.2,
            label=(
                f"Entry "
                f"{levels['entry']:.8g}"
            ),
        )

        # ----------------------------------------------------
        # SL
        # ----------------------------------------------------

        ax.axhline(
            levels["sl"],
            linestyle="--",
            linewidth=1.0,
            label=(
                f"SL "
                f"{levels['sl']:.8g}"
            ),
        )

        # ----------------------------------------------------
        # TP
        # ----------------------------------------------------

        ax.axhline(
            levels["tp1"],
            linestyle=":",
            linewidth=1.3,
            label=(
                f"TP "
                f"{levels['tp1']:.8g}"
            ),
        )

        # ----------------------------------------------------
        # Title
        # ----------------------------------------------------

        ax.set_title(
            (
                f"NDS M30 → M1 "
                f"| {symbol} "
                f"| {setup['side']} "
                f"| Signal #{signal_id}"
            )
        )

        ax.set_xlabel(
            "Time (UTC)"
        )

        ax.set_ylabel(
            "Price"
        )

        ax.legend(
            loc="best",
            fontsize=8,
        )

        ax.grid(
            alpha=0.2
        )

        fig.tight_layout()

        filename = (
            f"{symbol}_"
            f"{setup['side']}_"
            f"{setup['f_time']}_"
            f"{signal_id}.png"
        )

        path = os.path.join(
            CHART_DIR,
            filename,
        )

        fig.savefig(
            path,
            dpi=140,
        )

        plt.close(fig)

        return path

    except Exception as exc:

        print(
            "Signal chart error:",
            exc,
        )

        try:
            plt.close("all")
        except Exception:
            pass

        return None


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def signal_message(
    symbol,
    setup,
    levels,
    hook,
):

    emoji = (
        "🟢"
        if setup["side"] == "LONG"
        else "🔴"
    )

    pattern = (
        "H1-H2-H3-F"
        if setup["side"]
        == "SHORT"
        else
        "L1-L2-L3-F"
    )

    return (
        f"{emoji} "
        f"<b>{setup['side']}</b>\n"
        f"<b>{symbol}</b>\n\n"

        f"Strategy: "
        f"NDS M30 → M1\n"

        f"Hook: "
        f"{hook['type']}\n"

        f"M1 Pattern: "
        f"{pattern}\n\n"

        f"Entry: "
        f"<b>{levels['entry']:.8g}</b>\n"

        f"SL: "
        f"{levels['sl']:.8g}\n"

        f"TP: "
        f"{levels['tp1']:.8g}\n\n"

        f"F: "
        f"{fmt_time(setup['f_time'])}\n"

        f"Hook: "
        f"{fmt_time(hook['hook_time'])}\n"

        f"Mode: "
        f"PAPER ONLY"
    )


# ============================================================
# M30 HOOK REFRESH
# ============================================================

def refresh_active_hooks():

    global LAST_M30_SCAN

    print(
        "\n========== M30 SCAN =========="
    )

    assets_scanned = 0

    active_count = 0

    new_hooks = {}

    for symbol in ACTIVE_ASSETS:

        try:

            candles = fetch_candles(
                symbol,
                "30m",
                M30_CANDLES,
            )

            time.sleep(
                REQUEST_SLEEP
            )

            if not candles:
                continue

            assets_scanned += 1

            hook = determine_active_hook(
                candles
            )

            if hook:

                new_hooks[
                    symbol
                ] = hook

                save_active_hook(
                    symbol,
                    hook,
                )

                active_count += 1

                print(
                    "[HOOK]",
                    symbol,
                    hook["type"],
                    fmt_time(
                        hook["hook_time"]
                    ),
                )

                # ------------------------------------------------
                # SEND ACTIVE HOOK CHART ONLY FOR A NEW/CHANGED
                # HOOK. This prevents duplicate chart spam.
                # ------------------------------------------------

                old_hook = ACTIVE_HOOKS.get(
                    symbol
                )

                is_new_hook = (
                    old_hook is None
                )

                is_changed_hook = False

                if old_hook:

                    is_changed_hook = (
                        old_hook.get(
                            "type"
                        )
                        !=
                        hook.get(
                            "type"
                        )
                        or
                        old_hook.get(
                            "hook_time"
                        )
                        !=
                        hook.get(
                            "hook_time"
                        )
                    )

                if (
                    is_new_hook
                    or
                    is_changed_hook
                ):

                    event_key = hook_event_key(
                        symbol,
                        hook,
                    )

                    if not already_processed(
                        symbol,
                        event_key,
                    ):

                        chart_path = (
                            save_active_hook_chart(
                                symbol,
                                hook,
                                candles,
                            )
                        )

                        msg = active_hook_message(
                            symbol,
                            hook,
                        )

                        telegram_send(
                            msg,
                            chart_path,
                        )

                        mark_processed(
                            symbol,
                            event_key,
                        )

                        print(
                            "[ACTIVE HOOK CHART SENT]",
                            symbol,
                            hook["type"],
                        )

            else:

                mark_hook_inactive(
                    symbol
                )

                print(
                    "[NO ACTIVE HOOK]",
                    symbol,
                )

        except Exception as exc:

            print(
                f"[M30 ERROR] "
                f"{symbol}: {exc}"
            )

    # --------------------------------------------------------
    # Replace current active hooks.
    # --------------------------------------------------------

    ACTIVE_HOOKS.clear()

    ACTIVE_HOOKS.update(
        new_hooks
    )

    LAST_M30_SCAN = time.time()

    print(
        f"M30 scanned: "
        f"{assets_scanned}"
    )

    print(
        f"Active hooks: "
        f"{active_count}"
    )

    if new_hooks:

        print(
            "ACTIVE HOOK SYMBOLS:"
        )

        for symbol, hook in (
            new_hooks.items()
        ):

            direction = (
                "SHORT"
                if hook["type"]
                == "POSITIVE"
                else "LONG"
            )

            print(
                f"  {symbol} "
                f"| {hook['type']} "
                f"| {direction}"
            )


# ============================================================
# M1 ENTRY SCAN
# ============================================================

def scan_active_hooks():

    global LAST_M1_SCAN

    if not ACTIVE_HOOKS:
        return 0

    print(
        "\n========== M1 ENTRY SCAN =========="
    )

    signals_created = 0

    if (
        open_trade_count()
        >= MAX_OPEN_TRADES
    ):

        print(
            "Max open trades reached."
        )

        return 0

    for symbol, hook in list(
        ACTIVE_HOOKS.items()
    ):

        try:

            # ------------------------------------------------
            # Recheck M30
            # ------------------------------------------------

            m30 = fetch_candles(
                symbol,
                "30m",
                100,
            )

            time.sleep(
                REQUEST_SLEEP
            )

            if not m30:
                continue

            current_hook = (
                determine_active_hook(
                    m30
                )
            )

            if not current_hook:

                ACTIVE_HOOKS.pop(
                    symbol,
                    None,
                )

                mark_hook_inactive(
                    symbol
                )

                continue

            # ------------------------------------------------
            # If M30 Hook changed while M1 scanner is running,
            # update it.
            # ------------------------------------------------

            old_hook = ACTIVE_HOOKS.get(
                symbol
            )

            hook_changed = False

            if old_hook:

                hook_changed = (
                    old_hook.get("type")
                    !=
                    current_hook.get("type")
                    or
                    old_hook.get("hook_time")
                    !=
                    current_hook.get("hook_time")
                )

            hook = current_hook

            ACTIVE_HOOKS[
                symbol
            ] = hook

            # ------------------------------------------------
            # Send chart for a changed hook if needed.
            # ------------------------------------------------

            if hook_changed:

                event_key = hook_event_key(
                    symbol,
                    hook,
                )

                if not already_processed(
                    symbol,
                    event_key,
                ):

                    chart_path = (
                        save_active_hook_chart(
                            symbol,
                            hook,
                            m30,
                        )
                    )

                    msg = active_hook_message(
                        symbol,
                        hook,
                    )

                    telegram_send(
                        msg,
                        chart_path,
                    )

                    mark_processed(
                        symbol,
                        event_key,
                    )

            # ------------------------------------------------
            # M1
            # ------------------------------------------------

            m1 = fetch_candles(
                symbol,
                "1m",
                M1_CANDLES,
            )

            time.sleep(
                REQUEST_SLEEP
            )

            if not m1:
                continue

            # ------------------------------------------------
            # Positive -> SHORT
            # ------------------------------------------------

            if (
                hook["type"]
                == "POSITIVE"
            ):

                setup = (
                    detect_positive_m1_correction(
                        m1,
                        hook,
                    )
                )

            # ------------------------------------------------
            # Negative -> LONG
            # ------------------------------------------------

            else:

                setup = (
                    detect_negative_m1_correction(
                        m1,
                        hook,
                    )
                )

            if not setup:
                continue

            # ------------------------------------------------
            # Event key
            # ------------------------------------------------

            event_key = (
                f"{setup['side']}_"
                f"{setup['f_time']}"
            )

            if already_processed(
                symbol,
                event_key,
            ):

                continue

            if cooldown_active(
                symbol,
                setup["side"],
            ):

                print(
                    "[COOLDOWN]",
                    symbol,
                    setup["side"],
                )

                continue

            if (
                open_trade_count()
                >= MAX_OPEN_TRADES
            ):

                print(
                    "Max open trades reached."
                )

                break

            # ------------------------------------------------
            # Levels
            # ------------------------------------------------

            levels = (
                calculate_trade_levels(
                    setup["side"],
                    setup["f_price"],
                    setup,
                    hook,
                )
            )

            if not levels:

                print(
                    "[NO LEVELS]",
                    symbol,
                    setup["side"],
                )

                continue

            # ------------------------------------------------
            # Insert signal
            # ------------------------------------------------

            signal_id = insert_signal(
                symbol,
                hook,
                setup,
                levels,
            )

            if not signal_id:
                continue

            mark_processed(
                symbol,
                event_key,
            )

            signals_created += 1

            print(
                "\n🚨 NEW SIGNAL"
            )

            print(
                f"{symbol} "
                f"{setup['side']}"
            )

            print(
                f"Entry: "
                f"{levels['entry']}"
            )

            print(
                f"SL: "
                f"{levels['sl']}"
            )

            print(
                f"TP: "
                f"{levels['tp1']}"
            )

            chart_path = (
                save_signal_chart(
                    symbol,
                    hook,
                    setup,
                    levels,
                    closed_candles(
                        m1,
                        M1_TIMEFRAME_MS,
                    ),
                    signal_id,
                )
            )

            msg = signal_message(
                symbol,
                setup,
                levels,
                hook,
            )

            telegram_send(
                msg,
                chart_path,
            )

        except Exception as exc:

            print(
                f"[M1 ERROR] "
                f"{symbol}: {exc}"
            )

            traceback.print_exc()

    LAST_M1_SCAN = time.time()

    return signals_created


# ============================================================
# PNL
# ============================================================

def calculate_pnl(
    side,
    entry,
    current,
):

    if entry == 0:
        return 0

    if side == "LONG":

        return (
            (
                current
                - entry
            )
            /
            entry
            *
            100
        )

    return (
        (
            entry
            - current
        )
        /
        entry
        *
        100
    )


# ============================================================
# TRADE MANAGEMENT
# ============================================================

def manage_open_trades():

    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY created_at ASC
        """
    ).fetchall()

    conn.close()

    if not rows:
        return

    tickers = fetch_all_tickers()

    for row in rows:

        symbol = row[
            "symbol"
        ]

        ticker = tickers.get(
            symbol,
            {},
        )

        current = (
            ticker.get(
                "markPrice"
            )
            or ticker.get(
                "last"
            )
            or ticker.get(
                "bid"
            )
            or ticker.get(
                "ask"
            )
        )

        if current is None:
            continue

        try:

            current = float(
                current
            )

        except Exception:

            continue

        side = row[
            "side"
        ]

        entry = float(
            row["entry"]
        )

        sl = float(
            row["sl"]
        )

        tp1 = float(
            row["tp1"]
        )

        pnl = calculate_pnl(
            side,
            entry,
            current,
        )

        exit_reason = None

        if side == "LONG":

            if current <= sl:

                exit_reason = "SL"

            elif current >= tp1:

                exit_reason = "TP"

        else:

            if current >= sl:

                exit_reason = "SL"

            elif current <= tp1:

                exit_reason = "TP"

        if exit_reason:

            close_trade(
                row,
                current,
                exit_reason,
            )

        else:

            update_trade_price(
                row["id"],
                current,
                pnl,
            )


# ============================================================
# UPDATE TRADE
# ============================================================

def update_trade_price(
    signal_id,
    current,
    pnl,
):

    conn = db_connect()

    conn.execute(
        """
        UPDATE signals
        SET current_price = ?,
            pnl_pct = ?
        WHERE id = ?
        """,
        (
            current,
            pnl,
            signal_id,
        ),
    )

    conn.commit()

    conn.close()


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    row,
    exit_price,
    reason,
):

    pnl = calculate_pnl(
        row["side"],
        row["entry"],
        exit_price,
    )

    conn = db_connect()

    conn.execute(
        """
        UPDATE signals
        SET status = 'CLOSED',
            current_price = ?,
            pnl_pct = ?,
            result = ?,
            exit_price = ?,
            exit_time = ?,
            exit_reason = ?
        WHERE id = ?
        """,
        (
            exit_price,
            pnl,
            (
                "WIN"
                if pnl > 0
                else "LOSS"
            ),
            exit_price,
            now_ms(),
            reason,
            row["id"],
        ),
    )

    conn.commit()

    conn.close()

    emoji = (
        "🟢"
        if pnl >= 0
        else "🔴"
    )

    msg = (
        f"{emoji} "
        f"<b>NDS TRADE CLOSED</b>\n\n"

        f"{row['symbol']} "
        f"{row['side']}\n"

        f"Entry: "
        f"{float(row['entry']):.8g}\n"

        f"Exit: "
        f"{exit_price:.8g}\n"

        f"P/L: "
        f"<b>{pnl:+.2f}%</b>\n"

        f"Reason: "
        f"{reason}"
    )

    telegram_send(
        msg
    )

    print(
        "[CLOSED]",
        row["symbol"],
        row["side"],
        reason,
        f"{pnl:+.2f}%",
    )


# ============================================================
# PERFORMANCE
# ============================================================

def performance():

    conn = db_connect()

    total = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM signals
        WHERE status = 'CLOSED'
        """
    ).fetchone()["n"]

    wins = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM signals
        WHERE status = 'CLOSED'
          AND pnl_pct > 0
        """
    ).fetchone()["n"]

    losses = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM signals
        WHERE status = 'CLOSED'
          AND pnl_pct <= 0
        """
    ).fetchone()["n"]

    pnl = conn.execute(
        """
        SELECT COALESCE(
            SUM(pnl_pct),
            0
        ) AS p
        FROM signals
        WHERE status = 'CLOSED'
        """
    ).fetchone()["p"]

    open_count = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM signals
        WHERE status = 'OPEN'
        """
    ).fetchone()["n"]

    conn.close()

    return {
        "closed":
            int(total),

        "wins":
            int(wins),

        "losses":
            int(losses),

        "pnl":
            float(pnl),

        "open":
            int(open_count),
    }


# ============================================================
# ACTIVE HOOK REPORT
# ============================================================

def active_hooks_report():

    if not ACTIVE_HOOKS:

        return (
            "📌 "
            "<b>ACTIVE HOOKS</b>\n"
            "None"
        )

    lines = [
        "📌 <b>ACTIVE HOOKS</b>"
    ]

    for symbol, hook in sorted(
        ACTIVE_HOOKS.items()
    ):

        if hook["type"] == "POSITIVE":

            direction = "🔴 SHORT"

            key_point = (
                hook["h3"]["price"]
            )

            label = "H3"

        else:

            direction = "🟢 LONG"

            key_point = (
                hook["l3"]["price"]
            )

            label = "L3"

        lines.append(
            (
                f"{direction} "
                f"<b>{symbol}</b> "
                f"| {label}: "
                f"{key_point:.8g}"
            )
        )

    return "\n".join(
        lines
    )


# ============================================================
# OPEN TRADES REPORT
# ============================================================

def open_trades_report():

    rows = get_open_trades()

    if not rows:

        return (
            "📂 "
            "<b>OPEN TRADES</b>\n"
            "None"
        )

    lines = [
        "📂 <b>OPEN TRADES</b>"
    ]

    for row in rows:

        symbol = row[
            "symbol"
        ]

        side = row[
            "side"
        ]

        entry = float(
            row["entry"]
        )

        current = row[
            "current_price"
        ]

        pnl = row[
            "pnl_pct"
        ]

        if current is None:
            current = entry

        if pnl is None:
            pnl = calculate_pnl(
                side,
                entry,
                float(current),
            )

        emoji = (
            "🟢"
            if side == "LONG"
            else "🔴"
        )

        lines.append(
            (
                f"{emoji} "
                f"<b>{symbol}</b> "
                f"| {side}\n"
                f"Entry: "
                f"{entry:.8g} | "
                f"Current: "
                f"{float(current):.8g}\n"
                f"P/L: "
                f"<b>{float(pnl):+.2f}%</b>"
            )
        )

    return "\n".join(
        lines
    )


# ============================================================
# REPORT
# ============================================================

def send_report(
    new_signals=0,
):

    stats = performance()

    hooks_text = (
        active_hooks_report()
    )

    trades_text = (
        open_trades_report()
    )

    text = (
        "📊 "
        "<b>NDS M30 → M1 REPORT</b>\n\n"

        f"Version: "
        f"{VERSION}\n"

        f"Assets Scanned: "
        f"{len(ACTIVE_ASSETS)}\n"

        f"Active Hooks: "
        f"<b>{len(ACTIVE_HOOKS)}</b>\n"

        f"New Signals: "
        f"{new_signals}\n\n"

        f"{hooks_text}\n\n"

        f"{trades_text}\n\n"

        f"📈 <b>PERFORMANCE</b>\n"

        f"Open Trades: "
        f"{stats['open']}\n"

        f"Closed: "
        f"{stats['closed']}\n"

        f"Wins: "
        f"{stats['wins']}\n"

        f"Losses: "
        f"{stats['losses']}\n"

        f"PnL: "
        f"<b>{stats['pnl']:+.2f}%</b>\n\n"

        f"Mode: "
        f"PAPER ONLY"
    )

    telegram_send(
        text
    )

    print(
        "\n"
        + text.replace(
            "<b>",
            "",
        ).replace(
            "</b>",
            "",
        )
    )


# ============================================================
# STARTUP
# ============================================================

def send_startup():

    msg = (
        "🟢 "
        "<b>NDS M30 → M1 STARTED</b>\n\n"

        f"Version: "
        f"{VERSION}\n"

        f"Assets: "
        f"{len(ACTIVE_ASSETS)}\n\n"

        f"M30 Positive: "
        f"H1-L1-H2-L2-H3\n"

        f"M30 Negative: "
        f"L1-H1-L2-H2-L3\n\n"

        f"M1 Positive: "
        f"H1-L1-H2-L2-H3-L3-F\n"

        f"M1 Negative: "
        f"L1-H1-L2-H2-L3-H3-F\n\n"

        f"Active Hook Chart: "
        f"ON\n"

        f"Max Open: "
        f"{MAX_OPEN_TRADES}\n"

        f"F Max Age: "
        f"{M1_F_MAX_AGE_MINUTES} min\n"

        f"Mode: "
        f"PAPER ONLY"
    )

    telegram_send(
        msg
    )


# ============================================================
# MAIN
# ============================================================

def main():

    global LAST_ASSET_REFRESH
    global LAST_REPORT_TIME
    global LAST_M30_SCAN
    global LAST_M1_SCAN

    print(
        "=" * 70
    )

    print(
        "NDS M30 -> M1 LIVE SCANNER"
    )

    print(
        f"VERSION {VERSION}"
    )

    print(
        "PAPER ONLY"
    )

    print(
        "=" * 70
    )

    if not PAPER_ONLY:

        raise RuntimeError(
            "SAFETY ERROR: "
            "PAPER_ONLY must remain True."
        )

    init_db()

    # --------------------------------------------------------
    # Asset discovery
    # --------------------------------------------------------

    discover_assets()

    LAST_ASSET_REFRESH = (
        time.time()
    )

    send_startup()

    # --------------------------------------------------------
    # Initial M30 scan
    # --------------------------------------------------------

    refresh_active_hooks()

    LAST_M30_SCAN = (
        time.time()
    )

    # --------------------------------------------------------
    # Initial report
    # --------------------------------------------------------

    send_report(
        new_signals=0
    )

    LAST_REPORT_TIME = (
        time.time()
    )

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------

    while True:

        try:

            current_time = (
                time.time()
            )

            # =================================================
            # ASSET REFRESH
            # =================================================

            if (
                current_time
                -
                LAST_ASSET_REFRESH
                >=
                ASSET_REFRESH_SECONDS
            ):

                discover_assets()

                LAST_ASSET_REFRESH = (
                    current_time
                )

                refresh_active_hooks()

            # =================================================
            # M30
            # =================================================

            if (
                current_time
                -
                LAST_M30_SCAN
                >=
                M30_SCAN_SECONDS
            ):

                refresh_active_hooks()

            # =================================================
            # M1
            # =================================================

            if (
                current_time
                -
                LAST_M1_SCAN
                >=
                M1_SCAN_SECONDS
            ):

                new_signals = (
                    scan_active_hooks()
                )

                LAST_M1_SCAN = (
                    current_time
                )

            else:

                new_signals = 0

            # =================================================
            # OPEN TRADES
            # =================================================

            manage_open_trades()

            # =================================================
            # REPORT
            # =================================================

            if (
                current_time
                -
                LAST_REPORT_TIME
                >=
                REPORT_INTERVAL_SECONDS
            ):

                send_report(
                    new_signals=
                        new_signals,
                )

                LAST_REPORT_TIME = (
                    current_time
                )

            time.sleep(5)

        except KeyboardInterrupt:

            print(
                "Scanner stopped."
            )

            break

        except Exception as exc:

            print(
                "\nMAIN LOOP ERROR:",
                exc,
            )

            traceback.print_exc()

            time.sleep(
                ERROR_SLEEP
            )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    main()
