
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
NDS M5 TWO STRUCTURE LONG / SHORT SCANNER
Version 1.1.0

- Kraken Futures public market data
- Up to 100 PF_*USD symbols
- XBT and XAUT prioritized when available
- Closed M5 candles only
- START: 5-left / 5-right confirmed pivot
- Other structure points: 2-left / 2-right confirmed pivots
- Chart labels: START, 1 GREEN, 1 RED, 2 GREEN, 2 RED
- ENTRY, TP and SL are shown as horizontal levels
- Telegram alerts and SQLite duplicate protection
- Paper / signal only. No real orders are placed.
- Existing database is migrated without intentionally deleting
  historical signal records.
"""

import os
import time
import sqlite3
import logging
from datetime import datetime, timezone

import requests
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle


# ============================================================
# CONFIG
# ============================================================

VERSION = "1.1.0"

PAPER_ONLY = True

TARGET_SYMBOLS = 100
TIMEFRAME = "5m"
CANDLE_SECONDS = 300
CANDLE_LIMIT = 1500
CHART_CANDLES = 160

START_LEFT = 5
START_RIGHT = 5
PIVOT_LEFT = 2
PIVOT_RIGHT = 2

MAX_OPEN_SIGNALS = 3
REQUEST_TIMEOUT = 20
REQUEST_DELAY = 0.12

DB_FILE = "nds_m5_two_structure_ls.db"
CHART_DIR = "nds_m5_two_structure_ls_charts"

BASE_URL = "https://futures.kraken.com"
INSTRUMENTS_URL = BASE_URL + "/derivatives/api/v3/instruments"
TICKERS_URL = BASE_URL + "/derivatives/api/v3/tickers"
CHART_URL = BASE_URL + "/api/charts/v1/trade"

TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("TELEGRAM_TOKEN")
    or ""
)

TELEGRAM_CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or os.getenv("TELEGRAM_CHAT")
    or ""
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

log = logging.getLogger("NDS-M5")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": f"NDS-M5-Scanner/{VERSION}",
    "Accept": "application/json",
})


# ============================================================
# TIME AND HTTP
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_text():
    return utc_now().replace(microsecond=0).isoformat()


def get_json(url, params=None):
    response = SESSION.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    data = response.json()

    if not isinstance(data, dict):
        raise ValueError("Unexpected API response format")

    if data.get("result") == "error":
        raise RuntimeError(str(data))

    return data


def normalize_timestamp(value):
    value = float(value)

    # Convert milliseconds to seconds when necessary.
    if value > 10_000_000_000:
        value /= 1000.0

    return int(value)


# ============================================================
# DATABASE MIGRATION
# ============================================================

DB_COLUMNS = {
    "id",
    "symbol",
    "direction",
    "start_time",
    "entry_time",
    "start_price",
    "entry_price",
    "tp",
    "sl",
    "status",
    "chart_path",
    "created_at",
}


def table_exists(con, table_name):
    row = con.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table' AND name=?
        """,
        (table_name,),
    ).fetchone()

    return row is not None


def get_table_columns(con, table_name):
    return {
        row[1]
        for row in con.execute(
            f'PRAGMA table_info("{table_name}")'
        ).fetchall()
    }


def first_value(row, names, default=None):
    for name in names:
        if name in row and row[name] is not None:
            return row[name]

    return default


def create_signals_table(con, table_name="signals"):
    con.execute(f"""
        CREATE TABLE "{table_name}" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            start_time INTEGER NOT NULL DEFAULT 0,
            entry_time INTEGER NOT NULL DEFAULT 0,
            start_price REAL NOT NULL DEFAULT 0,
            entry_price REAL NOT NULL DEFAULT 0,
            tp REAL NOT NULL DEFAULT 0,
            sl REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'OPEN',
            chart_path TEXT,
            created_at TEXT NOT NULL DEFAULT '',
            UNIQUE(symbol, direction, entry_time)
        )
    """)


def migrate_old_signals_table(con):
    """
    Rebuild an older signals table if its schema is incompatible.

    Existing rows are copied using known legacy column names.
    The old table is dropped only after the copy has completed.
    """
    old_columns = get_table_columns(con, "signals")

    if DB_COLUMNS.issubset(old_columns):
        return

    log.warning(
        "Legacy database schema detected. Migrating signals table."
    )

    old_rows = con.execute(
        'SELECT * FROM "signals"'
    ).fetchall()

    old_names = [
        item[0]
        for item in con.execute(
            'SELECT * FROM "signals" LIMIT 0'
        ).description
    ]

    rows_as_dicts = [
        dict(zip(old_names, values))
        for values in old_rows
    ]

    # A unique temporary name avoids overwriting an earlier backup.
    legacy_name = "signals_legacy_migration"

    suffix = 1
    while table_exists(con, legacy_name):
        legacy_name = f"signals_legacy_migration_{suffix}"
        suffix += 1

    con.execute(
        f'ALTER TABLE "signals" RENAME TO "{legacy_name}"'
    )

    create_signals_table(con, "signals")

    for row in rows_as_dicts:
        symbol = first_value(row, ["symbol", "instrument"], "")
        direction = str(
            first_value(row, ["direction", "side"], "LONG")
        ).upper()

        if direction not in ("LONG", "SHORT"):
            direction = "LONG"

        start_time = first_value(
            row,
            ["start_time", "setup_time", "signal_time"],
            0,
        )

        entry_time = first_value(
            row,
            ["entry_time", "setup_time", "signal_time", "start_time"],
            start_time,
        )

        start_price = first_value(
            row,
            ["start_price", "start", "start_point"],
            0,
        )

        entry_price = first_value(
            row,
            ["entry_price", "entry", "entry_point"],
            0,
        )

        tp = first_value(
            row,
            ["tp", "take_profit", "target"],
            0,
        )

        sl = first_value(
            row,
            ["sl", "stop_loss", "stop"],
            0,
        )

        status = str(
            first_value(row, ["status"], "OPEN")
        ).upper()

        chart_path = first_value(
            row,
            ["chart_path", "chart", "image_path"],
            None,
        )

        created_at = str(
            first_value(row, ["created_at", "timestamp"], "")
        )

        try:
            con.execute("""
                INSERT OR IGNORE INTO signals (
                    symbol, direction, start_time, entry_time,
                    start_price, entry_price, tp, sl,
                    status, chart_path, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(symbol),
                direction,
                int(float(start_time or 0)),
                int(float(entry_time or 0)),
                float(start_price or 0),
                float(entry_price or 0),
                float(tp or 0),
                float(sl or 0),
                status,
                chart_path,
                created_at,
            ))
        except (ValueError, TypeError, sqlite3.Error) as exc:
            log.warning(
                "Could not migrate one legacy row: %s",
                exc,
            )

    # Keep the old table if migration encountered a database-level error.
    # This point is reached only after row migration has completed.
    con.execute(f'DROP TABLE "{legacy_name}"')


def init_db():
    os.makedirs(CHART_DIR, exist_ok=True)

    with sqlite3.connect(DB_FILE, timeout=30) as con:
        con.execute("PRAGMA busy_timeout=30000")

        if not table_exists(con, "signals"):
            create_signals_table(con, "signals")
        else:
            migrate_old_signals_table(con)

        # The index is created only AFTER schema migration.
        columns = get_table_columns(con, "signals")

        if "status" not in columns:
            con.execute("""
                ALTER TABLE signals
                ADD COLUMN status TEXT NOT NULL DEFAULT 'OPEN'
            """)

        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_status
            ON signals(status)
        """)

        con.commit()

    log.info("Database initialized: %s", DB_FILE)


def already_seen(symbol, direction, entry_time):
    with sqlite3.connect(DB_FILE, timeout=30) as con:
        row = con.execute("""
            SELECT 1
            FROM signals
            WHERE symbol=?
              AND direction=?
              AND entry_time=?
            LIMIT 1
        """, (symbol, direction, int(entry_time))).fetchone()

    return row is not None


def count_open_signals():
    with sqlite3.connect(DB_FILE, timeout=30) as con:
        row = con.execute("""
            SELECT COUNT(*)
            FROM signals
            WHERE status='OPEN'
        """).fetchone()

    return int(row[0] or 0)


def save_signal(signal, chart_path):
    with sqlite3.connect(DB_FILE, timeout=30) as con:
        cursor = con.execute("""
            INSERT OR IGNORE INTO signals (
                symbol, direction, start_time, entry_time,
                start_price, entry_price, tp, sl,
                status, chart_path, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
        """, (
            signal["symbol"],
            signal["direction"],
            int(signal["start"]["time"]),
            int(signal["entry"]["time"]),
            float(signal["start"]["price"]),
            float(signal["entry"]["price"]),
            float(signal["tp"]),
            float(signal["sl"]),
            chart_path,
            utc_text(),
        ))

        con.commit()
        return cursor.rowcount == 1


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.info("Telegram not configured; text alert skipped.")
        log.info("%s", message)
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_TOKEN}/sendMessage"
    )

    try:
        response = SESSION.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()
        return bool(response.json().get("ok"))

    except Exception as exc:
        log.error("Telegram text error: %s", exc)
        return False


def telegram_send_photo(path, caption):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_TOKEN}/sendPhoto"
    )

    try:
        with open(path, "rb") as image_file:
            response = SESSION.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption[:1000],
                },
                files={"photo": image_file},
                timeout=REQUEST_TIMEOUT,
            )

        response.raise_for_status()
        return bool(response.json().get("ok"))

    except Exception as exc:
        log.error("Telegram photo error: %s", exc)
        return False


# ============================================================
# SYMBOL UNIVERSE
# ============================================================

def get_symbol_universe():
    instruments_data = get_json(INSTRUMENTS_URL)
    tickers_data = get_json(TICKERS_URL)

    instruments = (
        instruments_data.get("instruments")
        or instruments_data.get("data")
        or []
    )

    tickers = (
        tickers_data.get("tickers")
        or tickers_data.get("data")
        or []
    )

    eligible = set()

    for item in instruments:
        if not isinstance(item, dict):
            continue

        symbol = str(item.get("symbol", ""))

        if not symbol.startswith("PF_"):
            continue

        if not symbol.endswith("USD"):
            continue

        tradeable = item.get("tradeable", True)

        if tradeable is False:
            continue

        eligible.add(symbol)

    ticker_map = {}

    for ticker in tickers:
        if not isinstance(ticker, dict):
            continue

        symbol = str(ticker.get("symbol", ""))

        if symbol in eligible:
            ticker_map[symbol] = ticker

    def volume_of(symbol):
        ticker = ticker_map.get(symbol, {})

        for field in ("vol24h", "volume24h", "volume"):
            try:
                return float(ticker.get(field) or 0)
            except (TypeError, ValueError):
                continue

        return 0.0

    ranked = sorted(
        eligible,
        key=volume_of,
        reverse=True,
    )

    priority = ["PF_XBTUSD", "PF_XAUTUSD"]

    selected = [
        symbol for symbol in priority
        if symbol in eligible
    ]

    for symbol in ranked:
        if symbol not in selected:
            selected.append(symbol)

        if len(selected) >= TARGET_SYMBOLS:
            break

    return selected[:TARGET_SYMBOLS]


# ============================================================
# CANDLES
# ============================================================

def normalize_candle(raw):
    if isinstance(raw, dict):
        timestamp = (
            raw.get("time")
            or raw.get("timestamp")
            or raw.get("t")
        )

        open_price = raw.get("open", raw.get("o"))
        high_price = raw.get("high", raw.get("h"))
        low_price = raw.get("low", raw.get("l"))
        close_price = raw.get("close", raw.get("c"))
        volume = raw.get("volume", raw.get("v", 0))

    elif isinstance(raw, (list, tuple)) and len(raw) >= 5:
        timestamp, open_price, high_price, low_price, close_price = raw[:5]
        volume = raw[5] if len(raw) > 5 else 0

    else:
        return None

    try:
        candle = {
            "time": normalize_timestamp(timestamp),
            "open": float(open_price),
            "high": float(high_price),
            "low": float(low_price),
            "close": float(close_price),
            "volume": float(volume or 0),
        }

        if candle["high"] < candle["low"]:
            return None

        return candle

    except (TypeError, ValueError, OverflowError):
        return None


def fetch_candles(symbol):
    now = int(time.time())
    start = now - CANDLE_LIMIT * CANDLE_SECONDS

    url = f"{CHART_URL}/{symbol}/{TIMEFRAME}"

    data = get_json(
        url,
        params={"from": start, "to": now},
    )

    raw_candles = (
        data.get("candles")
        or data.get("data")
        or []
    )

    candles = []

    for raw in raw_candles:
        candle = normalize_candle(raw)

        if candle is not None:
            candles.append(candle)

    candles.sort(key=lambda candle: candle["time"])

    # Deduplicate timestamps.
    by_time = {
        candle["time"]: candle
        for candle in candles
    }

    candles = [
        by_time[timestamp]
        for timestamp in sorted(by_time)
    ]

    # Ignore any candle that has not fully closed.
    now = int(time.time())

    candles = [
        candle for candle in candles
        if candle["time"] + CANDLE_SECONDS <= now
    ]

    return candles[-CANDLE_LIMIT:]


# ============================================================
# CONFIRMED PIVOTS
# ============================================================

def find_pivots(candles, left, right):
    pivots = []

    if len(candles) < left + right + 1:
        return pivots

    for index in range(left, len(candles) - right):
        current = candles[index]

        left_candles = candles[index - left:index]
        right_candles = candles[index + 1:index + right + 1]

        is_high = (
            all(current["high"] > c["high"] for c in left_candles)
            and all(current["high"] >= c["high"] for c in right_candles)
        )

        is_low = (
            all(current["low"] < c["low"] for c in left_candles)
            and all(current["low"] <= c["low"] for c in right_candles)
        )

        if is_high:
            pivots.append({
                "kind": "H",
                "index": index,
                "time": current["time"],
                "price": current["high"],
            })

        if is_low:
            pivots.append({
                "kind": "L",
                "index": index,
                "time": current["time"],
                "price": current["low"],
            })

    pivots.sort(key=lambda p: (p["index"], p["kind"]))

    # Collapse adjacent same-type pivots to the more extreme point.
    cleaned = []

    for pivot in pivots:
        if not cleaned:
            cleaned.append(pivot)
            continue

        previous = cleaned[-1]

        if previous["kind"] != pivot["kind"]:
            cleaned.append(pivot)
            continue

        if pivot["kind"] == "H":
            if pivot["price"] > previous["price"]:
                cleaned[-1] = pivot
        else:
            if pivot["price"] < previous["price"]:
                cleaned[-1] = pivot

    return cleaned


def find_start_pivots(candles):
    return find_pivots(
        candles,
        START_LEFT,
        START_RIGHT,
    )


def find_entry_structure_pivots(candles):
    return find_pivots(
        candles,
        PIVOT_LEFT,
        PIVOT_RIGHT,
    )


# ============================================================
# STRUCTURE AND SIGNAL RULES
# ============================================================

def make_signal(symbol, direction, points):
    start, p1g, p1r, p2g, p2r = points

    if direction == "LONG":
        if [p["kind"] for p in points] != ["L", "H", "L", "H", "L"]:
            return None

        # Second green pivot must be higher than the first.
        if p2g["price"] <= p1g["price"]:
            return None

        # The second red pivot is below the first red pivot,
        # but remains above the START low.
        if not (start["price"] < p2r["price"] < p1r["price"]):
            return None

        entry = p2r
        tp = p2g["price"]

        # Preserve the existing 86.4% calculation.
        sl = p2g["price"] - (
            0.864 * (p2g["price"] - start["price"])
        )

        if not sl < entry["price"] < tp:
            return None

    else:
        if [p["kind"] for p in points] != ["H", "L", "H", "L", "H"]:
            return None

        # Mirrored bearish structure.
        if p2g["price"] >= p1g["price"]:
            return None

        if not (p2r["price"] < p1r["price"] < start["price"]):
            return None

        entry = p2r
        tp = p2g["price"]

        sl = p2g["price"] + (
            0.864 * (start["price"] - p2g["price"])
        )

        if not tp < entry["price"] < sl:
            return None

    return {
        "symbol": symbol,
        "direction": direction,
        "start": start,
        "p1_green": p1g,
        "p1_red": p1r,
        "p2_green": p2g,
        "p2_red": p2r,
        "entry": entry,
        "tp": float(tp),
        "sl": float(sl),
    }


def detect_latest_signal(symbol, candles):
    if len(candles) < 40:
        return None

    starts = find_start_pivots(candles)
    pivots = find_entry_structure_pivots(candles)

    if not starts or len(pivots) < 4:
        return None

    candidates = []

    for start in starts:
        later = [
            pivot for pivot in pivots
            if pivot["index"] > start["index"]
        ]

        if len(later) < 4:
            continue

        for i in range(len(later) - 3):
            seq = later[i:i + 4]

            # LONG structure:
            # START low -> H1 -> L1 -> H2 -> L2
            if start["kind"] == "L":
                if [p["kind"] for p in seq] != ["H", "L", "H", "L"]:
                    continue

                signal = make_signal(
                    symbol,
                    "LONG",
                    [start] + seq,
                )

                if signal:
                    candidates.append(signal)

            # SHORT structure:
            # START high -> L1 -> H1 -> L2 -> H2
            elif start["kind"] == "H":
                if [p["kind"] for p in seq] != ["L", "H", "L", "H"]:
                    continue

                signal = make_signal(
                    symbol,
                    "SHORT",
                    [start] + seq,
                )

                if signal:
                    candidates.append(signal)

    if not candidates:
        return None

    # Only return the most recently confirmed entry pivot.
    candidates.sort(key=lambda item: item["entry"]["time"])
    return candidates[-1]


# ============================================================
# CHART: ONLY FIVE STRUCTURE POINTS + ENTRY/TP/SL
# ============================================================

def draw_chart(candles, signal):
    symbol = signal["symbol"]
    direction = signal["direction"]

    view = candles[-CHART_CANDLES:]

    if not view:
        raise ValueError("No candles available for chart")

    fig, ax = plt.subplots(figsize=(15, 8))
    fig.subplots_adjust(
        left=0.08,
        right=0.82,
        top=0.88,
        bottom=0.18,
    )

    candle_width = 5 / (24 * 60) * 0.65

    for candle in view:
        x = mdates.date2num(
            datetime.fromtimestamp(
                candle["time"],
                tz=timezone.utc,
            )
        )

        color = (
            "#159447"
            if candle["close"] >= candle["open"]
            else "#d62728"
        )

        ax.vlines(
            x,
            candle["low"],
            candle["high"],
            color=color,
            linewidth=1,
            zorder=2,
        )

        body_low = min(candle["open"], candle["close"])
        body_height = abs(candle["close"] - candle["open"])

        if body_height == 0:
            body_height = max(abs(candle["close"]) * 0.00001, 1e-8)

        ax.add_patch(Rectangle(
            (x - candle_width / 2, body_low),
            candle_width,
            body_height,
            facecolor=color,
            edgecolor=color,
            linewidth=0.7,
            zorder=3,
        ))

    point_specs = [
        ("START", signal["start"], "#1565c0", -18),
        ("1 GREEN", signal["p1_green"], "#159447", 12),
        ("1 RED", signal["p1_red"], "#d62728", -18),
        ("2 GREEN", signal["p2_green"], "#159447", 12),
        ("2 RED", signal["p2_red"], "#d62728", -18),
    ]

    # No other pivots are labeled on the chart.
    for label, point, color, offset in point_specs:
        x = mdates.date2num(
            datetime.fromtimestamp(
                point["time"],
                tz=timezone.utc,
            )
        )

        y = point["price"]

        ax.scatter(
            [x],
            [y],
            s=65,
            color=color,
            edgecolors="white",
            linewidths=0.8,
            zorder=6,
        )

        ax.annotate(
            f"{label}\n{y:.8g}",
            (x, y),
            xytext=(0, offset),
            textcoords="offset points",
            ha="center",
            va="bottom" if offset > 0 else "top",
            fontsize=8,
            fontweight="bold",
            color=color,
            bbox={
                "boxstyle": "round,pad=0.25",
                "fc": "white",
                "ec": color,
                "alpha": 0.9,
            },
            zorder=7,
        )

    for label, price, color, style in [
        ("ENTRY", signal["entry"]["price"], "#1565c0", "--"),
        ("TP", signal["tp"], "#159447", "-."),
        ("SL", signal["sl"], "#d62728", "-."),
    ]:
        ax.axhline(
            price,
            color=color,
            linestyle=style,
            linewidth=1.5,
            alpha=0.95,
        )

        ax.text(
            1.01,
            price,
            f"{label}: {price:.8g}",
            transform=ax.get_yaxis_transform(),
            color=color,
            fontsize=9,
            fontweight="bold",
            va="center",
            clip_on=False,
        )

    ax.set_title(
        f"NDS M5 | {symbol} | {direction} | v{VERSION}",
        fontsize=13,
        fontweight="bold",
    )

    ax.set_xlabel("Time (UTC)")
    ax.set_ylabel("Price")
    ax.grid(True, alpha=0.2)
    ax.xaxis.set_major_formatter(
        mdates.DateFormatter("%m-%d %H:%M")
    )
    fig.autofmt_xdate()

    path = os.path.join(
        CHART_DIR,
        f"{symbol}_{direction}_{signal['entry']['time']}.png",
    )

    fig.savefig(
        path,
        dpi=140,
        bbox_inches="tight",
    )
    plt.close(fig)

    return path


# ============================================================
# ALERT AND PROCESSING
# ============================================================

def format_price(value):
    return f"{float(value):.10g}"


def make_caption(signal):
    emoji = "🟢" if signal["direction"] == "LONG" else "🔴"

    return (
        f"{emoji} NDS M5 {signal['direction']}\n"
        f"Symbol: {signal['symbol']}\n"
        f"Entry: {format_price(signal['entry']['price'])}\n"
        f"TP: {format_price(signal['tp'])}\n"
        f"SL: {format_price(signal['sl'])}\n"
        f"START: {format_price(signal['start']['price'])}\n"
        f"Mode: PAPER / SIGNAL ONLY\n"
        f"Confirmation: closed M5 candles"
    )


def process_symbol(symbol):
    candles = fetch_candles(symbol)

    if len(candles) < 40:
        return "insufficient candles"

    signal = detect_latest_signal(symbol, candles)

    if signal is None:
        return "no structure"

    entry_time = signal["entry"]["time"]

    if already_seen(
        symbol,
        signal["direction"],
        entry_time,
    ):
        return "duplicate"

    if count_open_signals() >= MAX_OPEN_SIGNALS:
        return "open signal limit"

    chart_path = draw_chart(candles, signal)
    caption = make_caption(signal)

    # Save first to prevent repeat alerts on workflow reruns.
    if not save_signal(signal, chart_path):
        return "duplicate"

    if not telegram_send_photo(chart_path, caption):
        telegram_send_message(caption)

    log.info(
        "NEW %s %s | Entry=%s TP=%s SL=%s",
        signal["direction"],
        symbol,
        signal["entry"]["price"],
        signal["tp"],
        signal["sl"],
    )

    return f"NEW {signal['direction']}"


# ============================================================
# MAIN
# ============================================================

def main():
    init_db()

    started = time.time()

    log.info(
        "NDS M5 scanner v%s | PAPER_ONLY=%s",
        VERSION,
        PAPER_ONLY,
    )

    try:
        symbols = get_symbol_universe()
    except Exception as exc:
        log.exception("Could not retrieve symbols")

        telegram_send_message(
            f"⚠️ NDS M5 v{VERSION}\n"
            f"Could not retrieve Kraken Futures symbols.\n"
            f"{str(exc)[:300]}"
        )
        raise

    stats = {
        "scanned": 0,
        "errors": 0,
        "no_structure": 0,
        "duplicates": 0,
        "new_long": 0,
        "new_short": 0,
        "limited": 0,
        "insufficient": 0,
    }

    log.info("Symbols selected: %d", len(symbols))

    for number, symbol in enumerate(symbols, start=1):
        try:
            result = process_symbol(symbol)
            stats["scanned"] += 1

            if result == "NEW LONG":
                stats["new_long"] += 1
            elif result == "NEW SHORT":
                stats["new_short"] += 1
            elif result == "no structure":
                stats["no_structure"] += 1
            elif result == "duplicate":
                stats["duplicates"] += 1
            elif result == "open signal limit":
                stats["limited"] += 1
            elif result == "insufficient candles":
                stats["insufficient"] += 1

            log.info(
                "[%d/%d] %s: %s",
                number,
                len(symbols),
                symbol,
                result,
            )

        except Exception as exc:
            stats["errors"] += 1
            log.exception("Scan failed for %s: %s", symbol, exc)

        time.sleep(REQUEST_DELAY)

    elapsed = time.time() - started

    report = (
        f"🔎 NDS Scanner v{VERSION}\n"
        f"Time: {utc_text()}\n"
        f"Scanned: {stats['scanned']}/{len(symbols)}\n"
        f"Errors: {stats['errors']}\n"
        f"No structure: {stats['no_structure']}\n"
        f"Insufficient candles: {stats['insufficient']}\n"
        f"Duplicates: {stats['duplicates']}\n"
        f"New LONG: {stats['new_long']}\n"
        f"New SHORT: {stats['new_short']}\n"
        f"Open-slot limit hits: {stats['limited']}\n"
        f"Duration: {elapsed:.1f}s\n"
        f"Mode: PAPER / NO REAL ORDERS"
    )

    log.info("\n%s", report)
    telegram_send_message(report)


if __name__ == "__main__":
    main()
