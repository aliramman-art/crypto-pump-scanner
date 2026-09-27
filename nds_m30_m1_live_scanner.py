# ============================================================
# NDS M30 -> M1 LIVE SCANNER
# VERSION 3.1.0
# ============================================================
#
# PAPER ONLY
#
# STRATEGY
#
# M30:
#
# POSITIVE HOOK:
#   H1 -> L1 -> H2 -> L2 -> H3
#
#   H3 = confirmed "33" peak
#
#   After H3 confirmation:
#       M1 SHORT entry window opens
#
#   Window remains valid until the next confirmed L3.
#
# NEGATIVE HOOK:
#   L1 -> H1 -> L2 -> H2 -> L3
#
#   L3 = confirmed "33" trough
#
#   After L3 confirmation:
#       M1 LONG entry window opens
#
#   Window remains valid until the next confirmed H3.
#
# M1:
#
# POSITIVE HOOK -> SHORT:
#   L1 -> H2 -> L3
#   L3 > L1
#   F = closed candle breaks ABOVE H2
#   Entry = after F
#
# NEGATIVE HOOK -> LONG:
#   H1 -> L2 -> H3
#   H3 < H1
#   F = closed candle breaks BELOW L2
#   Entry = after F
#
# PAPER ONLY
# NO KRAKEN ORDER API IS USED
#
# ============================================================

import os
import time
import json
import math
import sqlite3
import traceback
from datetime import datetime, timezone

import requests

# Matplotlib is used only for signal charts.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "3.1.0"

PAPER_ONLY = True

DB_FILE = "nds_m30_m1_v31.db"
CHART_DIR = "nds_charts"

KRAKEN_CHART_BASE = "https://futures.kraken.com/api/charts/v1"
KRAKEN_REST_BASE = "https://futures.kraken.com/derivatives/api/v3"

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

# ------------------------------------------------------------
# Asset universe
# ------------------------------------------------------------

TARGET_ASSETS = 100

ASSET_REFRESH_SECONDS = 3600

# ------------------------------------------------------------
# M30 scanning
# ------------------------------------------------------------

M30_CANDLES = 320

M30_SCAN_SECONDS = 300

# ------------------------------------------------------------
# M1 scanning
# ------------------------------------------------------------

M1_CANDLES = 1500

M1_SCAN_SECONDS = 60

# ------------------------------------------------------------
# Pivot settings
# ------------------------------------------------------------

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

# ------------------------------------------------------------
# Hook filters
# ------------------------------------------------------------

HOOK_MIN_RANGE_PCT = 0.20

M1_MIN_SWING_PCT = 0.07

F_BREAK_BUFFER_PCT = 0.02

M1_MAX_STRUCTURE_DISTANCE_PCT = 3.0

# ------------------------------------------------------------
# Trade management
# ------------------------------------------------------------

MAX_OPEN_TRADES = 3

SIGNAL_COOLDOWN_MINUTES = 60

SL_BUFFER_PCT = 0.15

TP1_R = 1.0
TP2_R = 2.0
TP3_R = 3.0

# ------------------------------------------------------------
# Loop / network
# ------------------------------------------------------------

HTTP_TIMEOUT = 15

REQUEST_SLEEP = 0.05

ERROR_SLEEP = 10

# ------------------------------------------------------------
# Charts
# ------------------------------------------------------------

CHART_ENABLED = True

CHART_CANDLES = 180

# ============================================================
# GLOBALS
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent": "NDS-M30-M1-Scanner/3.1.0",
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
    conn = sqlite3.connect(DB_FILE, timeout=30)
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
    return int(time.time() * 1000)


def fmt_time(ms):
    if not ms:
        return "-"

    try:
        dt = datetime.fromtimestamp(
            ms / 1000,
            tz=timezone.utc,
        )

        return dt.strftime("%Y-%m-%d %H:%M UTC")

    except Exception:
        return "-"


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text, photo_path=None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    try:
        if photo_path and os.path.exists(photo_path):

            url = (
                f"https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            )

            with open(photo_path, "rb") as photo:

                response = requests.post(
                    url,
                    data={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "caption": text[:1024],
                    },
                    files={
                        "photo": photo,
                    },
                    timeout=20,
                )

        else:

            url = (
                f"https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            )

            response = requests.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=20,
            )

        return response.ok

    except Exception as exc:
        print("Telegram error:", exc)
        return False


# ============================================================
# KRAKEN MARKET DATA
# ============================================================

def fetch_candles(symbol, resolution, count):
    """
    Kraken Futures:
    /api/charts/v1/trade/{symbol}/{resolution}

    Official candle endpoint supports:
    1m, 5m, 15m, 30m, 1h, etc.
    """

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

    candles = data.get("candles")

    if not isinstance(candles, list):
        return []

    result = []

    for c in candles:

        try:

            result.append(
                {
                    "time": int(c["time"]),
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                    "volume": float(c.get("volume", 0)),
                }
            )

        except Exception:
            continue

    result.sort(
        key=lambda x: x["time"]
    )

    return result


def fetch_all_tickers():
    url = f"{KRAKEN_REST_BASE}/tickers"

    data = get_json(url)

    if not data:
        return {}

    tickers = data.get("tickers")

    if not isinstance(tickers, list):
        return {}

    result = {}

    for ticker in tickers:

        symbol = str(
            ticker.get("symbol", "")
        ).upper()

        if not symbol:
            continue

        result[symbol] = ticker

    return result


# ============================================================
# ASSET DISCOVERY
# ============================================================

def discover_assets():
    """
    Dynamically selects the top 100 tradeable
    PF_*USD perpetual crypto futures.

    No hard-coded 40-symbol list.
    """

    global ACTIVE_ASSETS

    print("Refreshing Kraken asset universe...")

    url = f"{KRAKEN_REST_BASE}/instruments"

    data = get_json(url)

    instruments = []

    if data:
        instruments = data.get("instruments", [])

    if not isinstance(instruments, list):
        instruments = []

    candidates = []

    for inst in instruments:

        try:

            symbol = str(
                inst.get("symbol", "")
            ).upper()

            inst_type = str(
                inst.get("type", "")
            ).lower()

            tradeable = bool(
                inst.get("tradeable", False)
            )

            if not tradeable:
                continue

            if not symbol.startswith("PF_"):
                continue

            if not symbol.endswith("USD"):
                continue

            # We only want perpetual-style PF contracts.
            if "perpetual" not in inst_type:
                # Some Kraken responses identify PF contracts
                # through symbol/tag rather than type.
                # Keep PF_*USD if no better classification exists.
                pass

            candidates.append(symbol)

        except Exception:
            continue

    # --------------------------------------------------------
    # Fallback: use all tickers if instruments response changes
    # --------------------------------------------------------

    if not candidates:

        print(
            "Instrument discovery returned no PF contracts. "
            "Using ticker fallback."
        )

        tickers = fetch_all_tickers()

        for symbol in tickers:

            if symbol.startswith("PF_") and symbol.endswith("USD"):
                candidates.append(symbol)

    candidates = sorted(
        set(candidates)
    )

    # --------------------------------------------------------
    # Rank by volume
    # --------------------------------------------------------

    tickers = fetch_all_tickers()

    ranked = []

    for symbol in candidates:

        ticker = tickers.get(symbol, {})

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

        # volumeQuote is generally more useful for ranking
        # contracts by USD trading activity.
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
        for _, symbol in ranked[:TARGET_ASSETS]
    ]

    # If volume information is unavailable,
    # fill up to TARGET_ASSETS.
    if len(selected) < TARGET_ASSETS:

        existing = set(selected)

        for symbol in candidates:

            if symbol not in existing:

                selected.append(symbol)

                existing.add(symbol)

                if len(selected) >= TARGET_ASSETS:
                    break

    ACTIVE_ASSETS = selected

    print(
        f"Active assets: {len(ACTIVE_ASSETS)}"
    )

    if ACTIVE_ASSETS:

        print(
            "Top assets:",
            ", ".join(ACTIVE_ASSETS[:20])
        )

    return ACTIVE_ASSETS


# ============================================================
# CANDLE HELPERS
# ============================================================

def closed_candles(candles, timeframe_ms):
    """
    Removes the currently forming candle.

    A candle is considered closed when:
        candle_time + timeframe <= now
    """

    current = now_ms()

    result = []

    for candle in candles:

        if (
            candle["time"] + timeframe_ms
            <= current
        ):
            result.append(candle)

    return result


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(candles):
    """
    Confirmed pivot detection.

    PIVOT_LEFT = 2
    PIVOT_RIGHT = 2

    A pivot is only confirmed after the required
    right-side candles exist.
    """

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
            i + 1:i + 1 + PIVOT_RIGHT
        ]

        # ----------------------------------------------------
        # HIGH
        # ----------------------------------------------------

        is_high = True

        for c in left + right:

            if c["high"] >= center["high"]:
                is_high = False
                break

        if is_high:

            highs.append(
                {
                    "kind": "H",
                    "index": i,
                    "time": center["time"],
                    "price": center["high"],
                }
            )

        # ----------------------------------------------------
        # LOW
        # ----------------------------------------------------

        is_low = True

        for c in left + right:

            if c["low"] <= center["low"]:
                is_low = False
                break

        if is_low:

            lows.append(
                {
                    "kind": "L",
                    "index": i,
                    "time": center["time"],
                    "price": center["low"],
                }
            )

    return highs, lows


# ============================================================
# PIVOT ORDER
# ============================================================

def combined_pivots(highs, lows):
    pivots = highs + lows

    pivots.sort(
        key=lambda x: x["time"]
    )

    # Remove impossible consecutive pivots
    # of the same type.
    cleaned = []

    for p in pivots:

        if not cleaned:

            cleaned.append(p)

            continue

        last = cleaned[-1]

        if p["kind"] != last["kind"]:

            cleaned.append(p)

            continue

        # Same kind:
        # keep the more extreme pivot.
        if p["kind"] == "H":

            if p["price"] > last["price"]:

                cleaned[-1] = p

        else:

            if p["price"] < last["price"]:

                cleaned[-1] = p

    return cleaned


# ============================================================
# PERCENT DISTANCE
# ============================================================

def pct_distance(a, b):
    if a == 0:
        return 0

    return abs(
        (b - a) / a
    ) * 100.0


# ============================================================
# HOOK DETECTION
# ============================================================

def detect_positive_hook(pivots):
    """
    H1 -> L1 -> H2 -> L2 -> H3

    H3 is the newest confirmed high.
    """

    if len(pivots) < 5:
        return None

    # Search from newest backwards.
    for end in range(
        len(pivots) - 1,
        4 - 1,
        -1,
    ):

        seq = pivots[
            end - 4:end + 1
        ]

        if [
            p["kind"] for p in seq
        ] != [
            "H",
            "L",
            "H",
            "L",
            "H",
        ]:
            continue

        h1, l1, h2, l2, h3 = seq

        # Structural requirements.
        if h2["price"] <= h1["price"]:
            continue

        if l2["price"] >= l1["price"]:
            continue

        if h3["price"] <= h2["price"]:
            continue

        # H3 should be meaningfully separated
        # from the previous structure.
        hook_range = pct_distance(
            l2["price"],
            h3["price"],
        )

        if hook_range < HOOK_MIN_RANGE_PCT:
            continue

        return {
            "type": "POSITIVE",
            "h1": h1,
            "l1": l1,
            "h2": h2,
            "l2": l2,
            "h3": h3,
            "hook_time": h3["time"],
        }

    return None


def detect_negative_hook(pivots):
    """
    L1 -> H1 -> L2 -> H2 -> L3

    L3 is the newest confirmed low.
    """

    if len(pivots) < 5:
        return None

    for end in range(
        len(pivots) - 1,
        4 - 1,
        -1,
    ):

        seq = pivots[
            end - 4:end + 1
        ]

        if [
            p["kind"] for p in seq
        ] != [
            "L",
            "H",
            "L",
            "H",
            "L",
        ]:
            continue

        l1, h1, l2, h2, l3 = seq

        if l2["price"] >= l1["price"]:
            continue

        if h2["price"] <= h1["price"]:
            continue

        if l3["price"] >= l2["price"]:
            continue

        hook_range = pct_distance(
            h2["price"],
            l3["price"],
        )

        if hook_range < HOOK_MIN_RANGE_PCT:
            continue

        return {
            "type": "NEGATIVE",
            "l1": l1,
            "h1": h1,
            "l2": l2,
            "h2": h2,
            "l3": l3,
            "hook_time": l3["time"],
        }

    return None


# ============================================================
# ACTIVE HOOK WINDOW
# ============================================================

def determine_active_hook(candles):
    """
    Determines the newest confirmed Hook.

    Positive Hook:
        starts at H3
        ends at next L3

    Negative Hook:
        starts at L3
        ends at next H3
    """

    candles = closed_candles(
        candles,
        30 * 60 * 1000,
    )

    if len(candles) < 20:
        return None

    highs, lows = find_pivots(candles)

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
        key=lambda x: x["hook_time"],
        reverse=True,
    )

    hook = candidates[0]

    # --------------------------------------------------------
    # Check that opposite pivot has not already appeared
    # after the hook pivot.
    # --------------------------------------------------------

    if hook["type"] == "POSITIVE":

        h3_time = hook["h3"]["time"]

        for p in pivots:

            if (
                p["kind"] == "L"
                and p["time"] > h3_time
            ):

                # This L pivot is the first opposing
                # confirmed pivot after H3.
                hook["window_end"] = p["time"]
                return None

        hook["window_end"] = None

    else:

        l3_time = hook["l3"]["time"]

        for p in pivots:

            if (
                p["kind"] == "H"
                and p["time"] > l3_time
            ):

                hook["window_end"] = p["time"]
                return None

        hook["window_end"] = None

    return hook


# ============================================================
# M1 123F
# ============================================================

def detect_positive_123f(candles, hook):
    """
    Positive Hook -> SHORT

    M1:
        L1 -> H2 -> L3
        L3 > L1

    F:
        closed candle breaks above H2.
    """

    if not hook:
        return None

    hook_time = hook["hook_time"]

    # Only candles after Hook confirmation.
    work = [
        c for c in candles
        if c["time"] > hook_time
    ]

    work = closed_candles(
        work,
        60 * 1000,
    )

    if len(work) < 10:
        return None

    highs, lows = find_pivots(work)

    pivots = combined_pivots(
        highs,
        lows,
    )

    if len(pivots) < 3:
        return None

    # --------------------------------------------------------
    # Search newest 123F
    # --------------------------------------------------------

    for end in range(
        len(pivots) - 1,
        2 - 1,
        -1,
    ):

        seq = pivots[
            end - 2:end + 1
        ]

        if [
            p["kind"] for p in seq
        ] != [
            "L",
            "H",
            "L",
        ]:
            continue

        l1, h2, l3 = seq

        # 123 structure.
        if l3["price"] <= l1["price"]:
            continue

        if h2["price"] <= l1["price"]:
            continue

        swing_pct = pct_distance(
            l1["price"],
            h2["price"],
        )

        if swing_pct < M1_MIN_SWING_PCT:
            continue

        # ----------------------------------------------------
        # F must happen after L3.
        # ----------------------------------------------------

        f_candidates = [
            c for c in work
            if c["time"] > l3["time"]
        ]

        for candle in f_candidates:

            buffer_price = (
                h2["price"]
                * (
                    1.0
                    + F_BREAK_BUFFER_PCT / 100.0
                )
            )

            if candle["close"] > buffer_price:

                # Make sure F is not absurdly far
                # from the 123 structure.
                distance = pct_distance(
                    h2["price"],
                    candle["close"],
                )

                if (
                    distance
                    > M1_MAX_STRUCTURE_DISTANCE_PCT
                ):
                    continue

                return {
                    "type": "POSITIVE_123F",
                    "side": "SHORT",
                    "l1": l1,
                    "h2": h2,
                    "l3": l3,
                    "f": candle,
                    "f_time": candle["time"],
                    "f_price": candle["close"],
                }

    return None


def detect_negative_123f(candles, hook):
    """
    Negative Hook -> LONG

    M1:
        H1 -> L2 -> H3
        H3 < H1

    F:
        closed candle breaks below L2.
    """

    if not hook:
        return None

    hook_time = hook["hook_time"]

    work = [
        c for c in candles
        if c["time"] > hook_time
    ]

    work = closed_candles(
        work,
        60 * 1000,
    )

    if len(work) < 10:
        return None

    highs, lows = find_pivots(work)

    pivots = combined_pivots(
        highs,
        lows,
    )

    if len(pivots) < 3:
        return None

    for end in range(
        len(pivots) - 1,
        2 - 1,
        -1,
    ):

        seq = pivots[
            end - 2:end + 1
        ]

        if [
            p["kind"] for p in seq
        ] != [
            "H",
            "L",
            "H",
        ]:
            continue

        h1, l2, h3 = seq

        if h3["price"] >= h1["price"]:
            continue

        if l2["price"] >= h1["price"]:
            continue

        swing_pct = pct_distance(
            h1["price"],
            l2["price"],
        )

        if swing_pct < M1_MIN_SWING_PCT:
            continue

        f_candidates = [
            c for c in work
            if c["time"] > h3["time"]
        ]

        for candle in f_candidates:

            buffer_price = (
                l2["price"]
                * (
                    1.0
                    - F_BREAK_BUFFER_PCT / 100.0
                )
            )

            if candle["close"] < buffer_price:

                distance = pct_distance(
                    l2["price"],
                    candle["close"],
                )

                if (
                    distance
                    > M1_MAX_STRUCTURE_DISTANCE_PCT
                ):
                    continue

                return {
                    "type": "NEGATIVE_123F",
                    "side": "LONG",
                    "h1": h1,
                    "l2": l2,
                    "h3": h3,
                    "f": candle,
                    "f_time": candle["time"],
                    "f_price": candle["close"],
                }

    return None


# ============================================================
# STRUCTURAL SL / TP
# ============================================================

def get_m1_structure(candles):
    highs, lows = find_pivots(candles)

    return (
        combined_pivots(
            highs,
            lows,
        )
    )


def calculate_trade_levels(
    side,
    entry,
    m1_pivots,
):
    """
    Structural SL and TP.

    LONG:
        SL below nearest valid previous swing low.
        TP = nearest valid previous swing high above entry.

    SHORT:
        SL above nearest valid previous swing high.
        TP = nearest valid previous swing low below entry.
    """

    if side == "LONG":

        valid_lows = [
            p["price"]
            for p in m1_pivots
            if (
                p["kind"] == "L"
                and p["price"] < entry
            )
        ]

        valid_highs = [
            p["price"]
            for p in m1_pivots
            if (
                p["kind"] == "H"
                and p["price"] > entry
            )
        ]

        if not valid_lows:
            return None

        if not valid_highs:
            return None

        nearest_low = max(
            valid_lows
        )

        nearest_high = min(
            valid_highs
        )

        sl = (
            nearest_low
            * (
                1.0
                - SL_BUFFER_PCT / 100.0
            )
        )

        risk = entry - sl

        if risk <= 0:
            return None

        tp1 = entry + risk * TP1_R
        tp2 = entry + risk * TP2_R
        tp3 = entry + risk * TP3_R

        # Prefer structural target when it is beyond entry.
        if nearest_high > entry:
            tp1 = nearest_high

        return {
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
        }

    else:

        valid_highs = [
            p["price"]
            for p in m1_pivots
            if (
                p["kind"] == "H"
                and p["price"] > entry
            )
        ]

        valid_lows = [
            p["price"]
            for p in m1_pivots
            if (
                p["kind"] == "L"
                and p["price"] < entry
            )
        ]

        if not valid_highs:
            return None

        if not valid_lows:
            return None

        nearest_high = min(
            valid_highs
        )

        nearest_low = max(
            valid_lows
        )

        sl = (
            nearest_high
            * (
                1.0
                + SL_BUFFER_PCT / 100.0
            )
        )

        risk = sl - entry

        if risk <= 0:
            return None

        tp1 = entry - risk * TP1_R
        tp2 = entry - risk * TP2_R
        tp3 = entry - risk * TP3_R

        if nearest_low < entry:
            tp1 = nearest_low

        return {
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
        }


# ============================================================
# DUPLICATE / COOLDOWN
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
        < SIGNAL_COOLDOWN_MINUTES
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

        hook_time = hook["h3"]["time"]
        h3_time = hook["h3"]["time"]
        l3_time = None

    else:

        hook_time = hook["l3"]["time"]
        h3_time = None
        l3_time = hook["l3"]["time"]

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
                if hook["type"] == "POSITIVE"
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
# SIGNAL INSERT
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

    hook_time = hook["hook_time"]

    f_time = setup["f_time"]

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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)
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
            c for c in candles
            if c["time"]
            >= setup["f_time"]
            - (
                CHART_CANDLES
                * 60
                * 1000
            )
        ]

        if len(data) < 20:
            data = candles[-CHART_CANDLES:]

        fig, ax = plt.subplots(
            figsize=(15, 8)
        )

        width = 0.0006

        # Use a dynamic width based on candle spacing.
        if len(data) >= 2:

            spacing = (
                data[-1]["time"]
                - data[-2]["time"]
            )

            width = (
                spacing
                / 1000
                / 60
                * 0.65
            )

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

            # Wick
            ax.plot(
                [x, x],
                [l, h],
                linewidth=0.8,
            )

            # Body
            ax.bar(
                x,
                body_high - body_low,
                bottom=body_low,
                width=width,
                align="center",
                alpha=0.75,
            )

        # ----------------------------------------------------
        # M1 structure points
        # ----------------------------------------------------

        for key in (
            "l1",
            "h2",
            "l3",
            "h1",
            "l2",
            "h3",
        ):

            p = setup.get(key)

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
                s=70,
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

        fy = f["close"]

        ax.scatter(
            [fx],
            [fy],
            s=110,
            marker="*",
            zorder=6,
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
            fontsize=12,
            fontweight="bold",
        )

        # ----------------------------------------------------
        # Entry
        # ----------------------------------------------------

        ax.axhline(
            levels["entry"],
            linestyle="--",
            linewidth=1.2,
            label=f"Entry {levels['entry']:.8g}",
        )

        # ----------------------------------------------------
        # SL
        # ----------------------------------------------------

        ax.axhline(
            levels["sl"],
            linestyle="--",
            linewidth=1.0,
            label=f"SL {levels['sl']:.8g}",
        )

        # ----------------------------------------------------
        # TP
        # ----------------------------------------------------

        ax.axhline(
            levels["tp1"],
            linestyle=":",
            linewidth=1.0,
            label=f"TP1 {levels['tp1']:.8g}",
        )

        ax.axhline(
            levels["tp2"],
            linestyle=":",
            linewidth=1.0,
            label=f"TP2 {levels['tp2']:.8g}",
        )

        ax.axhline(
            levels["tp3"],
            linestyle=":",
            linewidth=1.0,
            label=f"TP3 {levels['tp3']:.8g}",
        )

        ax.set_title(
            (
                f"NDS M30 → M1 | "
                f"{symbol} | "
                f"{setup['side']} | "
                f"Signal #{signal_id}"
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
        f"{emoji} <b>{setup['side']}</b>\n"
        f"<b>{symbol}</b>\n\n"
        f"Strategy: NDS M30 → M1\n"
        f"Hook: {hook['type']}\n"
        f"Pattern: {setup['type']}\n\n"
        f"Entry: <b>{levels['entry']:.8g}</b>\n"
        f"SL: {levels['sl']:.8g}\n"
        f"TP1: {levels['tp1']:.8g}\n"
        f"TP2: {levels['tp2']:.8g}\n"
        f"TP3: {levels['tp3']:.8g}\n\n"
        f"F: {setup['f_time']}\n"
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

                new_hooks[symbol] = hook

                save_active_hook(
                    symbol,
                    hook,
                )

                active_count += 1

                print(
                    f"[HOOK] "
                    f"{symbol} "
                    f"{hook['type']} "
                    f"{fmt_time(hook['hook_time'])}"
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
        f"M30 scanned: {assets_scanned}"
    )

    print(
        f"Active hooks: {active_count}"
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

    if open_trade_count() >= MAX_OPEN_TRADES:

        print(
            "Max open trades reached."
        )

        return 0

    for symbol, hook in list(
        ACTIVE_HOOKS.items()
    ):

        try:

            # ------------------------------------------------
            # Re-check M30 window before scanning M1.
            # ------------------------------------------------

            m30 = fetch_candles(
                symbol,
                "30m",
                80,
            )

            time.sleep(
                REQUEST_SLEEP
            )

            if not m30:
                continue

            current_hook = determine_active_hook(
                m30
            )

            if not current_hook:
                ACTIVE_HOOKS.pop(
                    symbol,
                    None,
                )
                continue

            # If the hook changed, use newest one.
            hook = current_hook

            ACTIVE_HOOKS[symbol] = hook

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

            if hook["type"] == "POSITIVE":

                setup = detect_positive_123f(
                    m1,
                    hook,
                )

            else:

                setup = detect_negative_123f(
                    m1,
                    hook,
                )

            if not setup:
                continue

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
                    f"[COOLDOWN] "
                    f"{symbol} "
                    f"{setup['side']}"
                )
                continue

            if open_trade_count() >= MAX_OPEN_TRADES:
                break

            # ------------------------------------------------
            # Structure for SL / TP
            # ------------------------------------------------

            m1_closed = closed_candles(
                m1,
                60 * 1000,
            )

            m1_pivots = get_m1_structure(
                m1_closed
            )

            levels = calculate_trade_levels(
                setup["side"],
                setup["f_price"],
                m1_pivots,
            )

            if not levels:
                print(
                    f"[NO LEVELS] {symbol}"
                )
                continue

            levels["entry"] = setup[
                "f_price"
            ]

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
                f"\n🚨 NEW SIGNAL "
                f"{symbol} "
                f"{setup['side']} "
                f"Entry={levels['entry']}"
            )

            chart_path = save_signal_chart(
                symbol,
                hook,
                setup,
                levels,
                m1_closed,
                signal_id,
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

        symbol = row["symbol"]

        ticker = tickers.get(
            symbol,
            {},
        )

        current = (
            ticker.get("markPrice")
            or ticker.get("last")
            or ticker.get("bid")
            or ticker.get("ask")
        )

        if current is None:
            continue

        try:
            current = float(current)
        except Exception:
            continue

        side = row["side"]

        entry = float(
            row["entry"]
        )

        sl = float(
            row["sl"]
        )

        tp1 = float(
            row["tp1"]
        )

        tp2 = float(
            row["tp2"]
        )

        tp3 = float(
            row["tp3"]
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

            elif current >= tp3:
                exit_reason = "TP3"

            elif current >= tp2:
                exit_reason = "TP2"

            elif current >= tp1:
                exit_reason = "TP1"

        else:

            if current >= sl:
                exit_reason = "SL"

            elif current <= tp3:
                exit_reason = "TP3"

            elif current <= tp2:
                exit_reason = "TP2"

            elif current <= tp1:
                exit_reason = "TP1"

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
        f"{emoji} <b>NDS TRADE CLOSED</b>\n\n"
        f"{row['symbol']} "
        f"{row['side']}\n"
        f"Entry: {float(row['entry']):.8g}\n"
        f"Exit: {exit_price:.8g}\n"
        f"P/L: <b>{pnl:+.2f}%</b>\n"
        f"Reason: {reason}"
    )

    telegram_send(
        msg
    )

    print(
        f"[CLOSED] "
        f"{row['symbol']} "
        f"{row['side']} "
        f"{reason} "
        f"{pnl:+.2f}%"
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
        "closed": int(total),
        "wins": int(wins),
        "losses": int(losses),
        "pnl": float(pnl),
        "open": int(open_count),
    }


# ============================================================
# REPORT
# ============================================================

def send_report(
    assets_scanned=None,
    new_signals=0,
):
    stats = performance()

    text = (
        "📊 <b>NDS M30 → M1 REPORT</b>\n\n"
        f"Version: {VERSION}\n"
        f"Assets: {len(ACTIVE_ASSETS)}\n"
        f"Active Hooks: {len(ACTIVE_HOOKS)}\n"
        f"New Signals: {new_signals}\n\n"
        f"Open Trades: {stats['open']}\n"
        f"Closed: {stats['closed']}\n"
        f"Wins: {stats['wins']}\n"
        f"Losses: {stats['losses']}\n"
        f"PnL: <b>{stats['pnl']:+.2f}%</b>\n\n"
        f"Mode: PAPER ONLY"
    )

    telegram_send(
        text
    )

    print(
        "\n" + text.replace(
            "<b>",
            ""
        ).replace(
            "</b>",
            ""
        )
    )


# ============================================================
# STARTUP MESSAGE
# ============================================================

def send_startup():
    msg = (
        "🟢 <b>NDS M30 → M1 STARTED</b>\n\n"
        f"Version: {VERSION}\n"
        f"Assets: {len(ACTIVE_ASSETS)}\n"
        f"M30: Hook detection\n"
        f"M1: 123F + F entry\n"
        f"Max Open: {MAX_OPEN_TRADES}\n"
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

    print("=" * 70)
    print(
        f"NDS M30 -> M1 LIVE SCANNER "
        f"VERSION {VERSION}"
    )
    print(
        "PAPER ONLY"
    )
    print("=" * 70)

    if not PAPER_ONLY:

        raise RuntimeError(
            "SAFETY ERROR: "
            "PAPER_ONLY must remain True."
        )

    init_db()

    # --------------------------------------------------------
    # Initial asset discovery
    # --------------------------------------------------------

    discover_assets()

    LAST_ASSET_REFRESH = time.time()

    send_startup()

    # --------------------------------------------------------
    # Initial M30 scan
    # --------------------------------------------------------

    refresh_active_hooks()

    LAST_M30_SCAN = time.time()

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------

    while True:

        try:

            current_time = time.time()

            # ------------------------------------------------
            # Refresh 100 assets every hour
            # ------------------------------------------------

            if (
                current_time
                - LAST_ASSET_REFRESH
                >= ASSET_REFRESH_SECONDS
            ):

                discover_assets()

                LAST_ASSET_REFRESH = current_time

                # Rebuild Hook universe.
                refresh_active_hooks()

            # ------------------------------------------------
            # M30 scan every 5 minutes
            # ------------------------------------------------

            if (
                current_time
                - LAST_M30_SCAN
                >= M30_SCAN_SECONDS
            ):

                refresh_active_hooks()

            # ------------------------------------------------
            # M1 scan every minute
            # ------------------------------------------------

            if (
                current_time
                - LAST_M1_SCAN
                >= M1_SCAN_SECONDS
            ):

                new_signals = scan_active_hooks()

                LAST_M1_SCAN = current_time

            else:

                new_signals = 0

            # ------------------------------------------------
            # Manage open trades
            # ------------------------------------------------

            manage_open_trades()

            # ------------------------------------------------
            # Periodic report every 15 minutes
            # ------------------------------------------------

            if (
                current_time
                - LAST_REPORT_TIME
                >= 900
            ):

                send_report(
                    new_signals=new_signals,
                )

                LAST_REPORT_TIME = current_time

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
