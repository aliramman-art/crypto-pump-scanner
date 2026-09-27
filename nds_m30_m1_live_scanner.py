# ============================================================
# NDS M30 -> M1 LIVE SCANNER
# VERSION 4.0.0
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
# After H3:
#   monitor M1 for an upward corrective structure.
#
# M1 POSITIVE CORRECTION:
#
#   H1 -> L1 -> H2 -> L2 -> H3 -> L3 -> F
#
# Simplified structural requirement:
#
#   H1 < H2 < H3 < F
#
#   and each high has a confirmed swing low between it.
#
# When F is confirmed:
#
#   SHORT
#
# SL:
#   slightly above the relevant M1 highs
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
# After L3:
#   monitor M1 for a downward corrective structure.
#
# M1 NEGATIVE CORRECTION:
#
#   L1 -> H1 -> L2 -> H2 -> L3 -> H3 -> F
#
# Simplified structural requirement:
#
#   L1 > L2 > L3 > F
#
# When F is confirmed:
#
#   LONG
#
# SL:
#   slightly below relevant M1 lows
#
# TP:
#   previous M30 high
#
# ============================================================
# IMPORTANT
# ============================================================
#
# F is a CONFIRMED M1 pivot.
#
# Therefore entry happens only after F is confirmed.
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

VERSION = "4.0.0"

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
# ENTRY
# ============================================================

# Entry happens at confirmed F.
#
# No additional breakout percentage is required because
# F itself is the final confirmed pivot of the M1 correction.
#
# ============================================================


# ============================================================
# TRADE MANAGEMENT
# ============================================================

MAX_OPEN_TRADES = 3

SIGNAL_COOLDOWN_MINUTES = 60

SL_BUFFER_PCT = 0.15


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


# ============================================================
# GLOBALS
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent": (
            "NDS-M30-M1-Scanner/4.0.0"
        ),
        "Accept": "application/json",
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

def get_json(url, params=None):

    try:

        r = session.get(
            url,
            params=params,
            timeout=HTTP_TIMEOUT,
        )

        r.raise_for_status()

        return r.json()

    except Exception:

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
                        "photo": photo,
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
        key=lambda x: x["time"]
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
        key=lambda x: x[0],
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
                    "kind": "H",
                    "index": i,
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
                    "kind": "L",
                    "index": i,
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
        key=lambda x: x["time"]
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

        # Same kind.
        #
        # Keep the more extreme one.

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
    Positive Hook:

        H1 -> L1 -> H2 -> L2 -> H3

    Conditions:

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

        # ----------------------------------------------------
        # H2 must be above H1
        # ----------------------------------------------------

        if (
            h2["price"]
            <= h1["price"]
        ):
            continue

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # The low of the second upward movement
        # must be below L1.
        # ----------------------------------------------------

        if (
            l2["price"]
            >= l1["price"]
        ):
            continue

        # ----------------------------------------------------
        # H3 above H2
        # ----------------------------------------------------

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

            # The previous M30 low.
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
    Negative Hook:

        L1 -> H1 -> L2 -> H2 -> L3

    Conditions:

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

            # Previous M30 high.
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
    # Positive:
    #
    # H3 opens the window.
    #
    # The window stays valid until the next confirmed L.
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
    # Negative:
    #
    # L3 opens the window.
    #
    # The window stays valid until the next confirmed H.
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
# M1 PIVOT HELPERS
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
# M1 POSITIVE CORRECTION
# ============================================================

def detect_positive_m1_correction(
    candles,
    hook,
):

    """
    Positive M30 Hook -> SHORT.

    After H3 the market should temporarily move UP
    on M1.

    We search for:

        H1
         \
          L1
           \
            H2
             \
              L2
               \
                H3
                 \
                  L3
                   \
                    F

    Main condition:

        H1 < H2 < H3 < F

    F is the newest confirmed high.

    Entry = F.
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

    # --------------------------------------------------------
    # Search from newest possible F backward.
    # --------------------------------------------------------

    for end in range(
        len(pivots) - 1,
        6 - 1,
        -1,
    ):

        f = pivots[end]

        if f["kind"] != "H":
            continue

        # Need alternating:
        #
        # H L H L H L H
        #
        # = H1 L1 H2 L2 H3 L3 F

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

        # ----------------------------------------------------
        # Higher highs.
        # ----------------------------------------------------

        if not (
            h2["price"] > h1["price"]
            and
            h3["price"] > h2["price"]
            and
            fp["price"] > h3["price"]
        ):
            continue

        # ----------------------------------------------------
        # Higher lows.
        #
        # This confirms that the move is genuinely
        # an upward corrective structure.
        # ----------------------------------------------------

        if not (
            l2["price"] > l1["price"]
            and
            l3["price"] > l2["price"]
        ):
            continue

        # ----------------------------------------------------
        # Minimum movement.
        # ----------------------------------------------------

        swing_pct = pct_distance(
            l1["price"],
            fp["price"],
        )

        if (
            swing_pct
            < M1_MIN_SWING_PCT
        ):
            continue

        # ----------------------------------------------------
        # F must be reasonably close to the structure.
        # ----------------------------------------------------

        distance = pct_distance(
            h3["price"],
            fp["price"],
        )

        if (
            distance
            > M1_MAX_STRUCTURE_DISTANCE_PCT
        ):
            continue

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # F is already confirmed because it is a pivot.
        #
        # Therefore the entry is now valid.
        # ----------------------------------------------------

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

            # Relevant M1 highs for SL.
            "sl_highs": [
                h1,
                h2,
                h3,
                fp,
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
    Negative M30 Hook -> LONG.

    After L3 the market should temporarily move DOWN
    on M1.

    Search for:

        L1 H1 L2 H2 L3 H3 F

    Main condition:

        L1 > L2 > L3 > F

    F is the newest confirmed low.

    Entry = F.
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
        6 - 1,
        -1,
    ):

        f = pivots[end]

        if f["kind"] != "L":
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

        # ----------------------------------------------------
        # Lower lows.
        # ----------------------------------------------------

        if not (
            l2["price"] < l1["price"]
            and
            l3["price"] < l2["price"]
            and
            fp["price"] < l3["price"]
        ):
            continue

        # ----------------------------------------------------
        # Lower highs.
        # ----------------------------------------------------

        if not (
            h2["price"] < h1["price"]
            and
            h3["price"] < h2["price"]
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
                fp,
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

    """
    NEW NDS LEVEL LOGIC

    LONG:
        SL below M1 lows.
        TP = previous M30 high.

    SHORT:
        SL above M1 highs.
        TP = previous M30 low.
    """

    # ========================================================
    # SHORT
    # ========================================================

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
            * (
                1.0
                + SL_BUFFER_PCT / 100.0
            )
        )

        tp = float(
            hook["target_price"]
        )

        if (
            tp >= entry
            or sl <= entry
        ):
            return None

        return {
            "sl": sl,
            "tp1": tp,
            "tp2": tp,
            "tp3": tp,
        }

    # ========================================================
    # LONG
    # ========================================================

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
        * (
            1.0
            - SL_BUFFER_PCT / 100.0
        )
    )

    tp = float(
        hook["target_price"]
    )

    if (
        tp <= entry
        or sl >= entry
    ):
        return None

    return {
        "sl": sl,
        "tp1": tp,
        "tp2": tp,
        "tp3": tp,
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
        - int(row["created_at"])
    )

    return (
        age_ms
        <
        SIGNAL_COOLDOWN_MINUTES
        * 60
        * 1000
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
# SAVE HOOK
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

    side = setup["side"]

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
# CHART
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
                * M1_TIMEFRAME_MS
            )
        ]

        if len(data) < 20:

            data = candles[
                -CHART_CANDLES:
            ]

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

            if cl >= o:

                body_low = o
                body_high = cl

            else:

                body_low = cl
                body_high = o

            ax.plot(
                [x, x],
                [l, h],
                linewidth=0.8,
            )

            ax.bar(
                x,
                body_high - body_low,
                bottom=body_low,
                width=width,
                align="center",
                alpha=0.75,
            )

        # ----------------------------------------------------
        # M1 structure
        # ----------------------------------------------------

        point_names = [
            "h1",
            "l1",
            "h2",
            "l2",
            "h3",
            "l3",
        ]

        for key in point_names:

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
                textcoords="offset points",
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
            s=140,
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
            textcoords="offset points",
            fontsize=13,
            fontweight="bold",
        )

        # ----------------------------------------------------
        # M30 Hook levels
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

        for label, p in m30_points:

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
                textcoords="offset points",
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
            "Chart error:",
            exc,
        )

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

    return (
        f"{emoji} "
        f"<b>{setup['side']}</b>\n"
        f"<b>{symbol}</b>\n\n"
        f"Strategy: NDS M30 → M1\n"
        f"Hook: {hook['type']}\n"
        f"Pattern: "
        f"{setup['type']}\n\n"
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
        f"Mode: PAPER ONLY"
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

    # --------------------------------------------------------
    # Replace current active hooks.
    # --------------------------------------------------------

            else:

                print(
                    "[NO ACTIVE HOOK]",
                    symbol,
                )

        except Exception as exc:

            print(
                f"[M30 ERROR] "
                f"{symbol}: {exc}"
            )

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

                continue

            hook = current_hook

            ACTIVE_HOOKS[
                symbol
            ] = hook

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
            # Positive Hook -> SHORT
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
            # Negative Hook -> LONG
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

            levels["entry"] = (
                setup["f_price"]
            )

            # ------------------------------------------------
            # Insert
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
                current - entry
            )
            / entry
            * 100
        )

    return (
        (
            entry - current
        )
        / entry
        * 100
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
        f"Reason: {reason}"
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
# REPORT
# ============================================================

def send_report(
    new_signals=0,
):

    stats = performance()

    text = (
        "📊 "
        "<b>NDS M30 → M1 REPORT</b>\n\n"
        f"Version: {VERSION}\n"
        f"Assets: "
        f"{len(ACTIVE_ASSETS)}\n"
        f"Active Hooks: "
        f"{len(ACTIVE_HOOKS)}\n"
        f"New Signals: "
        f"{new_signals}\n\n"
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
        f"Mode: PAPER ONLY"
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
        f"Version: {VERSION}\n"
        f"Assets: "
        f"{len(ACTIVE_ASSETS)}\n\n"
        f"M30: "
        f"1 → 2 → 3 Hook\n"
        f"M1: "
        f"1 → 2 → 3 → F\n"
        f"Max Open: "
        f"{MAX_OPEN_TRADES}\n"
        f"Mode: PAPER ONLY"
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
    # Initial M30
    # --------------------------------------------------------

    refresh_active_hooks()

    LAST_M30_SCAN = (
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
                - LAST_ASSET_REFRESH
                >= ASSET_REFRESH_SECONDS
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
                - LAST_M30_SCAN
                >= M30_SCAN_SECONDS
            ):

                refresh_active_hooks()

            # =================================================
            # M1
            # =================================================

            if (
                current_time
                - LAST_M1_SCAN
                >= M1_SCAN_SECONDS
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
                - LAST_REPORT_TIME
                >= 900
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
