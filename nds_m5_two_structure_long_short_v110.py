
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
NDS M5 TWO STRUCTURE LONG / SHORT SCANNER
Version: 1.1.0

Paper/signal detection only. No real orders are placed.

Chart labels are limited to:
START, 1 GREEN, 1 RED, 2 GREEN, 2 RED, ENTRY, TP, SL.
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
# CONFIGURATION
# ============================================================

VERSION = "1.1.0"

BASE_URL = "https://futures.kraken.com"
INSTRUMENTS_URL = BASE_URL + "/derivatives/api/v3/instruments"
TICKERS_URL = BASE_URL + "/derivatives/api/v3/tickers"
CHART_URL = BASE_URL + "/api/charts/v1/trade"

TIMEFRAME = "5m"
CANDLE_SECONDS = 300

TARGET_SYMBOLS = 100
CANDLE_LIMIT = 1500
CHART_CANDLES = 160

START_LEFT_RIGHT = 5
PIVOT_LEFT_RIGHT = 2

MAX_OPEN_SIGNALS = 3
PAPER_ONLY = True

DB_FILE = "nds_m5_two_structure_ls.db"
CHART_DIR = "nds_m5_two_structure_ls_charts"

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

REQUEST_TIMEOUT = 20

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("NDS-M5")


# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "NDS-M5-Structure-Scanner/1.1.0"
})


def get_json(url, params=None):
    response = SESSION.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()

    if not isinstance(data, dict):
        raise ValueError("Unexpected API response")

    if data.get("result") == "error":
        raise RuntimeError(str(data))

    return data


# ============================================================
# DATABASE
# ============================================================

def init_db():
    os.makedirs(CHART_DIR, exist_ok=True)

    with sqlite3.connect(DB_FILE) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                start_time INTEGER NOT NULL,
                entry_time INTEGER NOT NULL,
                start_price REAL NOT NULL,
                entry_price REAL NOT NULL,
                tp REAL NOT NULL,
                sl REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                chart_path TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(symbol, direction, entry_time)
            )
        """)
        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_status
            ON signals(status)
        """)
        con.commit()


def already_seen(symbol, direction, entry_time):
    with sqlite3.connect(DB_FILE) as con:
        row = con.execute("""
            SELECT id FROM signals
            WHERE symbol=? AND direction=? AND entry_time=?
        """, (symbol, direction, entry_time)).fetchone()
    return row is not None


def count_open_signals():
    with sqlite3.connect(DB_FILE) as con:
        row = con.execute("""
            SELECT COUNT(*) FROM signals WHERE status='OPEN'
        """).fetchone()
    return int(row[0] if row else 0)


def save_signal(signal, chart_path):
    created_at = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(DB_FILE) as con:
        cur = con.execute("""
            INSERT OR IGNORE INTO signals (
                symbol, direction, start_time, entry_time,
                start_price, entry_price, tp, sl,
                status, chart_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
        """, (
            signal["symbol"],
            signal["direction"],
            signal["start"]["time"],
            signal["entry"]["time"],
            signal["start"]["price"],
            signal["entry"]["price"],
            signal["tp"],
            signal["sl"],
            chart_path,
            created_at,
        ))
        con.commit()
        return cur.rowcount == 1


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.info("Telegram not configured; message skipped.")
        return False

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_TOKEN
        + "/sendMessage"
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
        log.error("Telegram message error: %s", exc)
        return False


def telegram_send_photo(path, caption):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_TOKEN
        + "/sendPhoto"
    )

    try:
        with open(path, "rb") as photo:
            response = SESSION.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption[:1000],
                },
                files={"photo": photo},
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

        status = str(
            item.get("tradeable", item.get("status", True))
        ).lower()

        if status in ("false", "inactive", "delisted", "0"):
            continue

        eligible.add(symbol)

    ticker_by_symbol = {}

    for ticker in tickers:
        if not isinstance(ticker, dict):
            continue

        symbol = str(ticker.get("symbol", ""))
        if symbol in eligible:
            ticker_by_symbol[symbol] = ticker

    def volume_of(symbol):
        item = ticker_by_symbol.get(symbol, {})
        for key in ("vol24h", "volume24h", "volume_24h"):
            try:
                return float(item.get(key, 0) or 0)
            except (ValueError, TypeError):
                pass
        return 0.0

    ranked = sorted(
        eligible,
        key=lambda symbol: volume_of(symbol),
        reverse=True,
    )

    # Keep BTC and tokenized gold near the front when available.
    priority = ["PF_XBTUSD", "PF_XAUTUSD"]
    result = []

    for symbol in priority:
        if symbol in eligible:
            result.append(symbol)

    for symbol in ranked:
        if symbol not in result:
            result.append(symbol)
        if len(result) >= TARGET_SYMBOLS:
            break

    return result[:TARGET_SYMBOLS]


# ============================================================
# CANDLE DATA
# ============================================================

def normalize_timestamp(value):
    timestamp = float(value)

    # API timestamps can be seconds or milliseconds.
    if timestamp > 10_000_000_000:
        timestamp /= 1000.0

    return int(timestamp)


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

    data = get_json(url, params={
        "from": start,
        "to": now,
    })

    raw_candles = (
        data.get("candles")
        or data.get("data")
        or []
    )

    candles = []
    for raw in raw_candles:
        candle = normalize_candle(raw)
        if candle:
            candles.append(candle)

    candles.sort(key=lambda candle: candle["time"])

    # Remove duplicate timestamps.
    unique = {}
    for candle in candles:
        unique[candle["time"]] = candle
    candles = [unique[t] for t in sorted(unique)]

    # Only fully closed M5 candles are eligible.
    closed_before = now - CANDLE_SECONDS
    candles = [
        candle for candle in candles
        if candle["time"] + CANDLE_SECONDS <= now
    ]

    if len(candles) > CANDLE_LIMIT:
        candles = candles[-CANDLE_LIMIT:]

    return candles


# ============================================================
# PIVOT DETECTION
# ============================================================

def find_pivots(candles, left_right):
    """
    Confirmed pivots only.
    A pivot is returned only after right-side candles have closed.
    """
    pivots = []
    n = len(candles)
    width = left_right

    if n < width * 2 + 1:
        return pivots

    for i in range(width, n - width):
        high = candles[i]["high"]
        low = candles[i]["low"]

        left = candles[i - width:i]
        right = candles[i + 1:i + width + 1]

        is_high = (
            all(high > c["high"] for c in left)
            and all(high >= c["high"] for c in right)
        )

        is_low = (
            all(low < c["low"] for c in left)
            and all(low <= c["low"] for c in right)
        )

        if is_high:
            pivots.append({
                "kind": "H",
                "index": i,
                "time": candles[i]["time"],
                "price": high,
            })

        if is_low:
            pivots.append({
                "kind": "L",
                "index": i,
                "time": candles[i]["time"],
                "price": low,
            })

    pivots.sort(key=lambda p: (p["index"], p["kind"]))

    # Do not allow two consecutive pivots of the same type.
    # Keep the more extreme one.
    cleaned = []

    for pivot in pivots:
        if not cleaned:
            cleaned.append(pivot)
            continue

        previous = cleaned[-1]

        if pivot["kind"] != previous["kind"]:
            cleaned.append(pivot)
            continue

        if pivot["kind"] == "H" and pivot["price"] > previous["price"]:
            cleaned[-1] = pivot
        elif pivot["kind"] == "L" and pivot["price"] < previous["price"]:
            cleaned[-1] = pivot

    return cleaned


def find_start_pivots(candles):
    return find_pivots(candles, START_LEFT_RIGHT)


def find_structure_pivots(candles):
    return find_pivots(candles, PIVOT_LEFT_RIGHT)


# ============================================================
# STRUCTURE DETECTION
# ============================================================

def make_signal(symbol, direction, points):
    start = points[0]
    p1_green = points[1]
    p1_red = points[2]
    p2_green = points[3]
    p2_red = points[4]

    if direction == "LONG":
        # Bullish structure: higher second high and a lower
        # second retracement low, while entry stays above START.
        if not (
            p1_green["kind"] == "H"
            and p1_red["kind"] == "L"
            and p2_green["kind"] == "H"
            and p2_red["kind"] == "L"
        ):
            return None

        if p2_green["price"] <= p1_green["price"]:
            return None

        if p2_red["price"] >= p1_red["price"]:
            return None

        if p2_red["price"] <= start["price"]:
            return None

        tp = p2_green["price"]
        sl = p2_green["price"] - (
            0.864 * (p2_green["price"] - start["price"])
        )

        entry = p2_red

        if not (sl < entry["price"] < tp):
            return None

    else:
        # Mirrored bearish structure.
        # START is the highest pivot, followed by low/high/low/high.
        if not (
            p1_green["kind"] == "L"
            and p1_red["kind"] == "H"
            and p2_green["kind"] == "L"
            and p2_red["kind"] == "H"
        ):
            return None

        if p2_green["price"] >= p1_green["price"]:
            return None

        if p2_red["price"] >= p1_red["price"]:
            return None

        if p2_red["price"] >= start["price"]:
            return None

        tp = p2_green["price"]
        sl = p2_green["price"] + (
            0.864 * (start["price"] - p2_green["price"])
        )

        entry = p2_red

        if not (tp < entry["price"] < sl):
            return None

    return {
        "symbol": symbol,
        "direction": direction,
        "start": start,
        "p1_green": p1_green,
        "p1_red": p1_red,
        "p2_green": p2_green,
        "p2_red": p2_red,
        "entry": entry,
        "tp": float(tp),
        "sl": float(sl),
    }


def detect_latest_signal(symbol, candles):
    if len(candles) < 40:
        return None

    start_pivots = find_start_pivots(candles)
    regular_pivots = find_structure_pivots(candles)

    if not start_pivots or len(regular_pivots) < 4:
        return None

    regular_by_index = {
        (p["index"], p["kind"]): p
        for p in regular_pivots
    }

    candidates = []

    # LONG: START low, then H/L/H/L.
    for start in start_pivots:
        if start["kind"] != "L":
            continue

        after = [
            p for p in regular_pivots
            if p["index"] > start["index"]
        ]

        for i in range(len(after) - 3):
            seq = after[i:i + 4]

            if [p["kind"] for p in seq] != ["H", "L", "H", "L"]:
                continue

            points = [start] + seq
            signal = make_signal(symbol, "LONG", points)

            if signal:
                candidates.append(signal)

    # SHORT: START high, then L/H/L/H.
    for start in start_pivots:
        if start["kind"] != "H":
            continue

        after = [
            p for p in regular_pivots
            if p["index"] > start["index"]
        ]

        for i in range(len(after) - 3):
            seq = after[i:i + 4]

            if [p["kind"] for p in seq] != ["L", "H", "L", "H"]:
                continue

            points = [start] + seq
            signal = make_signal(symbol, "SHORT", points)

            if signal:
                candidates.append(signal)

    if not candidates:
        return None

    # Use the most recently confirmed entry point.
    candidates.sort(key=lambda s: s["entry"]["time"])
    return candidates[-1]


# ============================================================
# CHART
# ============================================================

def candle_datetime(timestamp):
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def draw_chart(candles, signal):
    symbol = signal["symbol"]
    direction = signal["direction"]

    view = candles[-CHART_CANDLES:]
    if not view:
        raise ValueError("No candles available for chart")

    dates = [
        mdates.date2num(candle_datetime(c["time"]))
        for c in view
    ]

    fig, ax = plt.subplots(figsize=(15, 8))
    fig.subplots_adjust(bottom=0.16, top=0.88)

    candle_width = (5 / (24 * 60)) * 0.65

    # Draw candlesticks without adding labels for unrelated pivots.
    for x, candle in zip(dates, view):
        up = candle["close"] >= candle["open"]
        color = "#159447" if up else "#d62728"

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
            body_height = max(candle["close"] * 0.00001, 1e-8)

        rect = Rectangle(
            (x - candle_width / 2, body_low),
            candle_width,
            body_height,
            facecolor=color,
            edgecolor=color,
            linewidth=0.7,
            zorder=3,
        )
        ax.add_patch(rect)

    point_specs = [
        ("START", signal["start"], "#1565c0", "bottom"),
        ("1 GREEN", signal["p1_green"], "#159447", "top"),
        ("1 RED", signal["p1_red"], "#d62728", "bottom"),
        ("2 GREEN", signal["p2_green"], "#159447", "top"),
        ("2 RED", signal["p2_red"], "#d62728", "bottom"),
    ]

    # Exactly five structure-point labels.
    for label, point, color, placement in point_specs:
        x = mdates.date2num(candle_datetime(point["time"]))
        y = point["price"]

        ax.scatter(
            [x], [y],
            color=color,
            s=65,
            edgecolors="white",
            linewidths=0.7,
            zorder=6,
        )

        offset = 12 if placement == "top" else -18
        ax.annotate(
            f"{label}\n{y:.8g}",
            (x, y),
            xytext=(0, offset),
            textcoords="offset points",
            ha="center",
            va="bottom" if placement == "top" else "top",
            fontsize=8,
            color=color,
            fontweight="bold",
            bbox={
                "boxstyle": "round,pad=0.25",
                "fc": "white",
                "ec": color,
                "alpha": 0.88,
            },
            zorder=7,
        )

    entry = signal["entry"]["price"]
    tp = signal["tp"]
    sl = signal["sl"]

    levels = [
        ("ENTRY", entry, "#1565c0", "--"),
        ("TP", tp, "#159447", "-."),
        ("SL", sl, "#d62728", "-."),
    ]

    for label, price, color, linestyle in levels:
        ax.axhline(
            price,
            color=color,
            linestyle=linestyle,
            linewidth=1.5,
            alpha=0.95,
        )
        ax.text(
            1.005,
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
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate()

    path = os.path.join(
        CHART_DIR,
        f"{symbol}_{direction}_{signal['entry']['time']}.png",
    )

    fig.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return path


# ============================================================
# SIGNAL REPORT
# ============================================================

def format_price(value):
    return f"{value:.10g}"


def signal_caption(signal):
    emoji = "🟢" if signal["direction"] == "LONG" else "🔴"

    return (
        f"{emoji} NDS M5 {signal['direction']}\n"
        f"Symbol: {signal['symbol']}\n"
        f"Entry: {format_price(signal['entry']['price'])}\n"
        f"TP: {format_price(signal['tp'])}\n"
        f"SL: {format_price(signal['sl'])}\n"
        f"Start: {format_price(signal['start']['price'])}\n"
        f"Mode: PAPER / SIGNAL ONLY\n"
        f"Structure confirmed on closed M5 candles"
    )


def process_symbol(symbol):
    candles = fetch_candles(symbol)

    if len(candles) < 40:
        return "insufficient candles"

    signal = detect_latest_signal(symbol, candles)

    if not signal:
        return "no structure"

    entry_time = signal["entry"]["time"]

    if already_seen(symbol, signal["direction"], entry_time):
        return "duplicate"

    if count_open_signals() >= MAX_OPEN_SIGNALS:
        return "open signal limit"

    chart_path = draw_chart(candles, signal)

    # Insert before Telegram send to prevent repeated alerts on reruns.
    inserted = save_signal(signal, chart_path)

    if not inserted:
        return "duplicate"

    caption = signal_caption(signal)

    photo_sent = telegram_send_photo(chart_path, caption)

    if not photo_sent:
        # Send the text even if the photo upload failed.
        telegram_send_message(caption)

    log.info(
        "NEW SIGNAL %s %s entry=%s TP=%s SL=%s",
        symbol,
        signal["direction"],
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

    started = datetime.now(timezone.utc)
    log.info(
        "NDS M5 Two Structure LONG SHORT v%s | PAPER_ONLY=%s",
        VERSION,
        PAPER_ONLY,
    )

    try:
        symbols = get_symbol_universe()
    except Exception as exc:
        log.exception("Could not retrieve symbol universe: %s", exc)
        telegram_send_message(
            f"⚠️ NDS M5 v{VERSION}\n"
            f"Could not retrieve Kraken Futures symbols.\n"
            f"Error: {str(exc)[:300]}"
        )
        raise

    log.info("Universe: %d symbols", len(symbols))

    stats = {
        "scanned": 0,
        "errors": 0,
        "no_structure": 0,
        "duplicates": 0,
        "new_long": 0,
        "new_short": 0,
        "limited": 0,
    }

    for index, symbol in enumerate(symbols, start=1):
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

            log.info(
                "[%d/%d] %s: %s",
                index,
                len(symbols),
                symbol,
                result,
            )

        except Exception as exc:
            stats["errors"] += 1
            log.warning("%s scan error: %s", symbol, exc)

        # Avoid hammering the public API.
        time.sleep(0.12)

    elapsed = (
        datetime.now(timezone.utc) - started
    ).total_seconds()

    report = (
        f"🔎 NDS Scanner v{VERSION}\n"
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"Scanned: {stats['scanned']}/{len(symbols)}\n"
        f"Errors: {stats['errors']}\n"
        f"No structure: {stats['no_structure']}\n"
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
