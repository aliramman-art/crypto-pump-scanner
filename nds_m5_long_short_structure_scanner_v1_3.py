
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NDS M15 Long + Short Structure Scanner v1.5.0

LONG structure:
START = lowest point of the complete structure.
1 GREEN = high above START.
1 RED = low above START.
2 GREEN = high above 1 GREEN.
2 RED = low below 1 RED but above START.

SHORT is the exact mirror:
START = highest point of the complete structure.
1 RED = low below START.
1 GREEN = high below START.
2 RED = low below 1 RED.
2 GREEN = high above 1 GREEN but below START.

M15 structure is analyzed first. M1 must confirm in the same direction
before a paper trade is opened. The M15 chart is sent even when M1 does
not confirm. No real exchange orders are placed.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
except Exception as exc:
    raise RuntimeError("matplotlib is required; install it with: pip install matplotlib") from exc


# ----------------------------- Configuration -----------------------------

VERSION = "1.5.0"
FUTURES_REST = "https://futures.kraken.com/derivatives/api/v3"
CHARTS_REST = "https://futures.kraken.com/api/charts/v1"
TELEGRAM_API = "https://api.telegram.org"

UNIVERSE_SIZE = max(2, int(os.getenv("UNIVERSE_SIZE", "100")))
MAX_OPEN_TRADES = max(1, int(os.getenv("MAX_OPEN_TRADES", "3")))
MAX_SIGNAL_AGE_HOURS = max(1, int(os.getenv("MAX_SIGNAL_AGE_HOURS", "6")))

M15_CANDLE_COUNT = 300
M1_CANDLE_COUNT = 1200

PIVOT_LEFT = 5
PIVOT_RIGHT = 5
M15_PIVOT_LEFT = 2
M15_PIVOT_RIGHT = 2

MAX_CONCURRENT_REQUESTS = 8
MAX_CHARTS_PER_RUN = 8
REQUIRED_SYMBOLS = ("PF_XBTUSD", "PF_XAUTUSD")

M1_MS = 60 * 1000
M15_MS = 15 * 60 * 1000

DB_PATH = Path(os.getenv("DB_PATH", "nds_m5_long_short_structure_v1.db"))
CHART_DIR = Path(os.getenv("CHART_DIR", "nds_m5_long_short_structure_charts"))
REPORT_TZ_NAME = os.getenv("REPORT_TIMEZONE", "Europe/Berlin")

try:
    REPORT_TZ = ZoneInfo(REPORT_TZ_NAME)
except Exception:
    REPORT_TZ = timezone.utc

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": f"NDS-M15-Long-Short-Scanner/{VERSION}",
    "Accept": "application/json",
})

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
LOG = logging.getLogger("nds_scanner")


# ----------------------------- Helpers -----------------------------

def as_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def fmt_price(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    if abs(value) >= 1000:
        return f"{value:,.2f}"
    if abs(value) >= 1:
        return f"{value:.5f}".rstrip("0").rstrip(".")
    return f"{value:.8f}".rstrip("0").rstrip(".")


def fmt_pct(value: Optional[float]) -> str:
    return "N/A" if value is None else f"{value:+.2f}%"


def dt_text(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(
        epoch_seconds, tz=timezone.utc
    ).astimezone(REPORT_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")


def request_json(
    url: str,
    params: Optional[dict[str, Any]] = None,
    retries: int = 3,
) -> dict[str, Any]:
    last_exc = None

    for attempt in range(retries):
        try:
            response = HTTP.get(url, params=params, timeout=(8, 25))

            if response.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(
                    f"HTTP {response.status_code}: {response.text[:200]}"
                )

            response.raise_for_status()
            payload = response.json()

            if not isinstance(payload, dict):
                raise ValueError("Unexpected JSON response")

            return payload

        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(0.7 * (attempt + 1))

    raise RuntimeError(f"Request failed: {last_exc}")


def get_tickers() -> list[dict[str, Any]]:
    payload = request_json(f"{FUTURES_REST}/tickers")
    tickers = payload.get("tickers")

    if not isinstance(tickers, list):
        raise RuntimeError("Kraken tickers response did not contain a list")

    return [x for x in tickers if isinstance(x, dict)]


def ticker_map(
    tickers: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        str(x.get("symbol", "")).upper(): x
        for x in tickers if x.get("symbol")
    }


def current_price_from_ticker(
    ticker: Optional[dict[str, Any]],
) -> Optional[float]:
    if not ticker:
        return None

    bid = as_float(ticker.get("bid")) or as_float(ticker.get("bidPrice"))
    ask = as_float(ticker.get("ask")) or as_float(ticker.get("askPrice"))

    if bid is not None and ask is not None and bid > 0 and ask >= bid:
        return (bid + ask) / 2

    for key in (
        "markPrice", "mark_price", "last", "lastPrice",
        "lastTradePrice", "last_price", "indexPrice",
    ):
        value = as_float(ticker.get(key))
        if value is not None and value > 0:
            return value

    return None


def extract_volume(ticker: dict[str, Any]) -> float:
    for key in (
        "vol24h", "volume24h", "volume", "volume24H",
        "turnover24h", "usdVolume24h",
    ):
        value = as_float(ticker.get(key))
        if value is not None and value >= 0:
            return value
    return 0.0


def get_universe(tickers: list[dict[str, Any]]) -> list[str]:
    rows = []

    for ticker in tickers:
        symbol = str(ticker.get("symbol", "")).upper()

        if symbol.startswith("PF_") and symbol.endswith("USD"):
            rows.append((symbol, extract_volume(ticker)))

    rows.sort(key=lambda x: (x[1], x[0]), reverse=True)

    selected = list(REQUIRED_SYMBOLS)

    for symbol, _volume in rows:
        if symbol not in selected:
            selected.append(symbol)
        if len(selected) >= UNIVERSE_SIZE:
            break

    return selected[:UNIVERSE_SIZE]


# ----------------------------- Candle data -----------------------------

def get_candles(
    symbol: str,
    resolution: str,
    limit: int,
) -> list[dict[str, Any]]:
    timeframe_ms = {
        "1m": M1_MS,
        "15m": M15_MS,
    }.get(resolution)

    if timeframe_ms is None:
        raise ValueError(f"Unsupported resolution: {resolution}")

    now = time.time()
    timeframe_seconds = timeframe_ms / 1000

    params = {
        "from": int(now - timeframe_seconds * (limit + 5)),
        "to": int(now),
        "count": limit,
    }

    payload = request_json(
        f"{CHARTS_REST}/trade/{symbol}/{resolution}",
        params=params,
    )

    rows = payload.get("candles", [])

    if not isinstance(rows, list):
        raise RuntimeError(f"Invalid candles response for {symbol} {resolution}")

    by_time = {}
    now_ms = int(time.time() * 1000)

    for row in rows:
        if not isinstance(row, dict):
            continue

        ts = as_float(row.get("time"))
        opn = as_float(row.get("open"))
        high = as_float(row.get("high"))
        low = as_float(row.get("low"))
        close = as_float(row.get("close"))

        if None in (ts, opn, high, low, close):
            continue

        ts = int(ts)
        if ts < 1_000_000_000_000:
            ts *= 1000

        # Use closed candles only.
        if ts + timeframe_ms > now_ms:
            continue

        if low <= 0 or high < low:
            continue

        by_time[ts] = {
            "time": ts,
            "open": float(opn),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": as_float(row.get("volume")) or 0.0,
        }

    return [by_time[k] for k in sorted(by_time)][-limit:]


# ----------------------------- Pivot detection -----------------------------

def is_pivot_low(
    candles: list[dict[str, Any]],
    index: int,
    left: int,
    right: int,
) -> bool:
    if index < left or index + right >= len(candles):
        return False

    value = candles[index]["low"]

    return (
        value < min(candles[j]["low"] for j in range(index - left, index))
        and value < min(candles[j]["low"] for j in range(index + 1, index + right + 1))
    )


def is_pivot_high(
    candles: list[dict[str, Any]],
    index: int,
    left: int,
    right: int,
) -> bool:
    if index < left or index + right >= len(candles):
        return False

    value = candles[index]["high"]

    return (
        value > max(candles[j]["high"] for j in range(index - left, index))
        and value > max(candles[j]["high"] for j in range(index + 1, index + right + 1))
    )


def find_confirmed_pivots(
    candles: list[dict[str, Any]],
    left: int,
    right: int,
) -> tuple[list[int], list[int]]:
    lows = []
    highs = []

    for i in range(left, len(candles) - right):
        if is_pivot_low(candles, i, left, right):
            lows.append(i)
        if is_pivot_high(candles, i, left, right):
            highs.append(i)

    return lows, highs


# ----------------------------- Strategy detection -----------------------------

def detect_m15_structure_pattern(
    symbol: str,
    candles: list[dict[str, Any]],
    direction: str,
    timeframe_ms: int = M15_MS,
) -> Optional[dict[str, Any]]:
    """
    Detect confirmed START + 1 GREEN + 1 RED + 2 GREEN + 2 RED.

    LONG:
      START is the lowest point of the structure.
      1 GREEN > START.
      1 RED > START.
      2 GREEN > 1 GREEN.
      2 RED < 1 RED and > START.

    SHORT:
      START is the highest point of the structure.
      1 RED < START.
      1 GREEN < START.
      2 RED < 1 RED.
      2 GREEN > 1 GREEN and < START.
    """
    if len(candles) < PIVOT_LEFT + PIVOT_RIGHT + 20:
        return None

    lows, highs = find_confirmed_pivots(
        candles, PIVOT_LEFT, PIVOT_RIGHT
    )

    if direction == "LONG":
        for p4 in reversed(lows):
            for p3 in reversed([i for i in highs if i < p4]):
                for p2 in reversed([i for i in lows if i < p3]):
                    if candles[p4]["low"] >= candles[p2]["low"]:
                        continue

                    for p1 in reversed([i for i in highs if i < p2]):
                        if candles[p3]["high"] <= candles[p1]["high"]:
                            continue

                        # Start must precede 1 GREEN and be the lowest point
                        # between Start and 2 RED, inclusive.
                        starts = [
                            i for i in lows
                            if i < p1 and i >= max(0, p1 - 240)
                        ]

                        valid_starts = [
                            st for st in starts
                            if candles[st]["low"] < candles[p2]["low"]
                            and candles[st]["low"] < candles[p4]["low"]
                            and candles[p2]["low"] > candles[st]["low"]
                            and candles[p4]["low"] > candles[st]["low"]
                            and min(c["low"] for c in candles[st:p4 + 1])
                            == candles[st]["low"]
                        ]

                        if not valid_starts:
                            continue

                        st = min(
                            valid_starts,
                            key=lambda i: candles[i]["low"],
                        )

                        confirmation_i = p4 + PIVOT_RIGHT
                        if confirmation_i >= len(candles):
                            continue

                        points = {
                            "start": st,
                            "p1": p1,
                            "p2": p2,
                            "p3": p3,
                            "p4": p4,
                        }

                        key = "LONG|" + symbol + "|" + "|".join(
                            str(candles[points[k]]["time"])
                            for k in ("start", "p1", "p2", "p3", "p4")
                        )

                        return {
                            "symbol": symbol,
                            "direction": "LONG",
                            "points": points,
                            "key": key,
                            "confirmation_time": (
                                candles[confirmation_i]["time"] + timeframe_ms
                            ) / 1000,
                            "start_price": candles[st]["low"],
                            "p1_price": candles[p1]["high"],
                            "p2_price": candles[p2]["low"],
                            "p3_price": candles[p3]["high"],
                            "p4_price": candles[p4]["low"],
                            "terminal_time": candles[p4]["time"] / 1000,
                        }

    else:
        for p4 in reversed(highs):
            for p3 in reversed([i for i in lows if i < p4]):
                for p2 in reversed([i for i in highs if i < p3]):
                    if candles[p4]["high"] <= candles[p2]["high"]:
                        continue

                    for p1 in reversed([i for i in lows if i < p2]):
                        if candles[p3]["low"] >= candles[p1]["low"]:
                            continue

                        starts = [
                            i for i in highs
                            if i < p1 and i >= max(0, p1 - 240)
                        ]

                        valid_starts = [
                            st for st in starts
                            if candles[st]["high"] > candles[p2]["high"]
                            and candles[st]["high"] > candles[p4]["high"]
                            and candles[p2]["high"] < candles[st]["high"]
                            and candles[p4]["high"] < candles[st]["high"]
                            and max(c["high"] for c in candles[st:p4 + 1])
                            == candles[st]["high"]
                        ]

                        if not valid_starts:
                            continue

                        st = max(
                            valid_starts,
                            key=lambda i: candles[i]["high"],
                        )

                        confirmation_i = p4 + PIVOT_RIGHT
                        if confirmation_i >= len(candles):
                            continue

                        points = {
                            "start": st,
                            "p1": p1,
                            "p2": p2,
                            "p3": p3,
                            "p4": p4,
                        }

                        key = "SHORT|" + symbol + "|" + "|".join(
                            str(candles[points[k]]["time"])
                            for k in ("start", "p1", "p2", "p3", "p4")
                        )

                        return {
                            "symbol": symbol,
                            "direction": "SHORT",
                            "points": points,
                            "key": key,
                            "confirmation_time": (
                                candles[confirmation_i]["time"] + timeframe_ms
                            ) / 1000,
                            "start_price": candles[st]["high"],
                            "p1_price": candles[p1]["low"],
                            "p2_price": candles[p2]["high"],
                            "p3_price": candles[p3]["low"],
                            "p4_price": candles[p4]["high"],
                            "terminal_time": candles[p4]["time"] / 1000,
                        }

    return None


def detect_latest_pattern(
    symbol: str,
    candles: list[dict[str, Any]],
    direction: str,
) -> Optional[dict[str, Any]]:
    return detect_m15_structure_pattern(symbol, candles, direction)


# ----------------------------- Stop loss -----------------------------

def find_nearest_valid_m15_high(
    candles15: list[dict[str, Any]],
    entry: float,
) -> Optional[float]:
    _lows, highs = find_confirmed_pivots(
        candles15, M15_PIVOT_LEFT, M15_PIVOT_RIGHT
    )

    eligible = []

    for i in highs:
        price = candles15[i]["high"]

        if price <= entry:
            continue

        if any(bar["high"] >= price for bar in candles15[i + 1:]):
            continue

        eligible.append((i, price))

    eligible.sort(key=lambda x: x[0], reverse=True)
    return eligible[0][1] if eligible else None


def find_nearest_valid_m15_low(
    candles15: list[dict[str, Any]],
    entry: float,
) -> Optional[float]:
    lows, _highs = find_confirmed_pivots(
        candles15, M15_PIVOT_LEFT, M15_PIVOT_RIGHT
    )

    eligible = []

    for i in lows:
        price = candles15[i]["low"]

        if price >= entry:
            continue

        if any(bar["low"] <= price for bar in candles15[i + 1:]):
            continue

        eligible.append((i, price))

    eligible.sort(key=lambda x: x[0], reverse=True)
    return eligible[0][1] if eligible else None


# ----------------------------- Database -----------------------------

def connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS patterns (
            pattern_key TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            detected_at REAL NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_key TEXT NOT NULL UNIQUE,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT 'LONG',
            entry_time REAL NOT NULL,
            next_bar_time_ms INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            tp_price REAL NOT NULL,
            sl_price REAL NOT NULL,
            current_price REAL,
            updated_at REAL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            exit_time REAL,
            exit_price REAL,
            pnl_pct REAL,
            created_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_trades_status
        ON trades(status);

        CREATE INDEX IF NOT EXISTS idx_trades_symbol_status
        ON trades(symbol, status);
        """
    )

    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(trades)")
    }

    if "direction" not in cols:
        conn.execute(
            "ALTER TABLE trades ADD COLUMN direction TEXT NOT NULL DEFAULT 'LONG'"
        )

    if "current_price" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN current_price REAL")

    if "updated_at" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN updated_at REAL")

    conn.commit()
    return conn


def pattern_seen(conn: sqlite3.Connection, key: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM patterns WHERE pattern_key=?",
        (key,),
    ).fetchone() is not None


def save_pattern_decision(
    conn: sqlite3.Connection,
    pattern: dict[str, Any],
    decision: str,
    reason: str = "",
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO patterns
        (pattern_key,symbol,detected_at,decision,reason)
        VALUES(?,?,?,?,?)
        """,
        (
            pattern["key"], pattern["symbol"], time.time(),
            decision, reason[:500],
        ),
    )
    conn.commit()


def open_trades(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM trades WHERE status='OPEN' ORDER BY entry_time ASC"
    ))


def insert_trade(
    conn: sqlite3.Connection,
    pattern: dict[str, Any],
    entry_time: float,
    entry_price: float,
    tp: float,
    sl: float,
) -> None:
    entry_ms = int(entry_time * 1000)
    next_bar_time_ms = ((entry_ms // M15_MS) + 1) * M15_MS

    conn.execute(
        """
        INSERT INTO trades
        (pattern_key,symbol,direction,entry_time,next_bar_time_ms,
         entry_price,tp_price,sl_price,current_price,updated_at,status,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,'OPEN',?)
        """,
        (
            pattern["key"], pattern["symbol"], pattern["direction"],
            entry_time, next_bar_time_ms, entry_price, tp, sl,
            entry_price, time.time(), time.time(),
        ),
    )
    conn.commit()


def close_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    status: str,
    exit_time: float,
    exit_price: float,
) -> None:
    row = conn.execute(
        "SELECT entry_price,direction FROM trades WHERE id=?",
        (trade_id,),
    ).fetchone()

    if not row:
        return

    entry = float(row["entry_price"])
    direction = str(row["direction"])

    pnl = (
        (exit_price / entry - 1)
        if direction == "LONG"
        else (entry / exit_price - 1)
    ) * 100

    conn.execute(
        """
        UPDATE trades
        SET status=?,exit_time=?,exit_price=?,pnl_pct=?
        WHERE id=? AND status='OPEN'
        """,
        (status, exit_time, exit_price, pnl, trade_id),
    )
    conn.commit()


def trade_pnl_percent(
    entry: float,
    price: Optional[float],
    direction: str,
) -> Optional[float]:
    if price is None or entry <= 0 or price <= 0:
        return None

    return (
        (price / entry - 1)
        if direction == "LONG"
        else (entry / price - 1)
    ) * 100


# ----------------------------- Charting -----------------------------

def create_signal_chart(
    symbol: str,
    candles: list[dict[str, Any]],
    pattern: dict[str, Any],
    entry: float,
    tp: float,
    sl: Optional[float],
    timeframe: str = "M15",
) -> Path:
    CHART_DIR.mkdir(parents=True, exist_ok=True)

    pts = pattern["points"]
    direction = pattern["direction"]

    left_edge = max(0, min(min(pts.values()) - 18, len(candles) - 175))
    chart_candles = candles[left_edge:]

    if len(chart_candles) < 10:
        chart_candles = candles[-100:]
        left_edge = len(candles) - len(chart_candles)

    fig, ax = plt.subplots(figsize=(13, 7), dpi=135)

    for x, candle in enumerate(chart_candles):
        color = "#18a558" if candle["close"] >= candle["open"] else "#e74c3c"

        ax.vlines(
            x, candle["low"], candle["high"],
            color=color, linewidth=1.1, zorder=2,
        )

        body_low = min(candle["open"], candle["close"])
        body_height = max(
            abs(candle["close"] - candle["open"]),
            abs(candle["close"]) * 0.000002,
        )

        ax.add_patch(
            Rectangle(
                (x - 0.31, body_low),
                0.62,
                body_height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.6,
                zorder=3,
            )
        )

    if direction == "LONG":
        labels = {
            "start": ("START", "#2457c5", "o", candles[pts["start"]]["low"]),
            "p1": ("1 GREEN", "#149447", "^", candles[pts["p1"]]["high"]),
            "p2": ("1 RED", "#db2b39", "v", candles[pts["p2"]]["low"]),
            "p3": ("2 GREEN", "#149447", "^", candles[pts["p3"]]["high"]),
            "p4": ("2 RED", "#db2b39", "v", candles[pts["p4"]]["low"]),
        }
    else:
        labels = {
            "start": ("START", "#2457c5", "o", candles[pts["start"]]["high"]),
            "p1": ("1 RED", "#db2b39", "v", candles[pts["p1"]]["low"]),
            "p2": ("1 GREEN", "#149447", "^", candles[pts["p2"]]["high"]),
            "p3": ("2 RED", "#db2b39", "v", candles[pts["p3"]]["low"]),
            "p4": ("2 GREEN", "#149447", "^", candles[pts["p4"]]["high"]),
        }

    for name, (label, color, marker, y) in labels.items():
        x = pts[name] - left_edge

        if not (0 <= x < len(chart_candles)):
            continue

        ax.scatter(
            [x], [y], s=75, marker=marker, color=color,
            edgecolors="white", linewidths=0.7, zorder=5,
        )

        offset = 10 if marker in ("^", "o") else -14

        ax.annotate(
            label,
            (x, y),
            xytext=(0, offset),
            textcoords="offset points",
            fontsize=8,
            color=color,
            fontweight="bold",
            ha="center",
            va="bottom" if offset > 0 else "top",
            bbox={
                "boxstyle": "round,pad=0.18",
                "fc": "white",
                "ec": color,
                "alpha": 0.86,
                "lw": 0.65,
            },
        )

    ax.axhline(
        tp, color="#149447", linestyle="--",
        linewidth=1.25, label=f"TP {fmt_price(tp)}",
    )

    if sl is not None:
        ax.axhline(
            sl, color="#db2b39", linestyle="--",
            linewidth=1.25, label=f"SL {fmt_price(sl)}",
        )

    ax.axhline(
        entry, color="#277da1", linestyle=":",
        linewidth=1.5, label=f"ENTRY {fmt_price(entry)}",
    )

    n_ticks = min(8, len(chart_candles))
    ticks = sorted(set(
        round(i * (len(chart_candles) - 1) / max(1, n_ticks - 1))
        for i in range(n_ticks)
    ))

    ax.set_xticks(ticks)
    ax.set_xticklabels(
        [
            datetime.fromtimestamp(
                chart_candles[i]["time"] / 1000,
                timezone.utc,
            ).astimezone(REPORT_TZ).strftime("%m-%d %H:%M")
            for i in ticks
        ],
        rotation=25,
        ha="right",
        fontsize=8,
    )

    ax.set_title(
        f"NDS {timeframe} {direction} | {symbol}\n"
        f"Confirmed START + 4-pivot structure | "
        f"{dt_text(pattern['confirmation_time'])}",
        fontsize=12,
        fontweight="bold",
    )

    ax.set_ylabel("Price")
    ax.grid(True, alpha=0.2)
    ax.legend(loc="best", fontsize=8, framealpha=0.9)
    ax.margins(x=0.03, y=0.1)
    fig.tight_layout()

    safe = "".join(
        ch if ch.isalnum() or ch in "-_" else "_"
        for ch in symbol
    )

    path = CHART_DIR / (
        f"{safe}_{timeframe}_{direction}_{int(time.time())}.png"
    )

    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


# ----------------------------- M1 confirmation -----------------------------

def analyze_m1_for_signal(
    symbol: str,
    m15_direction: str,
    entry: float,
    tp: float,
    sl: Optional[float],
) -> dict[str, Any]:
    result = {
        "status": "NO M1 STRUCTURE",
        "direction": None,
        "chart": None,
        "detail": "No confirmed M1 structure",
    }

    try:
        candles1 = get_candles(symbol, "1m", M1_CANDLE_COUNT)

        if len(candles1) < PIVOT_LEFT + PIVOT_RIGHT + 20:
            result["status"] = "INSUFFICIENT M1 DATA"
            result["detail"] = f"Only {len(candles1)} closed M1 candles"
            return result

        candidates = []

        for direction in ("LONG", "SHORT"):
            pattern = detect_m15_structure_pattern(
                symbol, candles1, direction, timeframe_ms=M1_MS
            )
            if pattern:
                candidates.append(pattern)

        if not candidates:
            return result

        pattern = max(
            candidates,
            key=lambda item: item["confirmation_time"],
        )

        result["direction"] = pattern["direction"]
        result["pattern"] = pattern

        if pattern["direction"] == m15_direction:
            result["status"] = "M1 CONFIRMS M15"
            result["detail"] = (
                f"M1 {pattern['direction']} agrees with M15 {m15_direction}"
            )
        else:
            result["status"] = "M1 OPPOSITE - NOT CONFIRMED"
            result["detail"] = (
                f"M1 {pattern['direction']} opposes M15 {m15_direction}"
            )

        # Only a same-direction M1 chart is needed for trade confirmation.
        if pattern["direction"] == m15_direction:
            result["chart"] = create_signal_chart(
                symbol,
                candles1,
                pattern,
                entry,
                tp,
                sl,
                timeframe="M1",
            )

        return result

    except Exception as exc:
        LOG.warning("M1 analysis failed for %s: %s", symbol, exc)
        result["status"] = "M1 ANALYSIS ERROR"
        result["detail"] = str(exc)[:180]
        return result


# ----------------------------- Telegram -----------------------------

def telegram_credentials() -> tuple[Optional[str], Optional[str]]:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT")
    return token, chat_id


def telegram_send_message(message: str) -> bool:
    token, chat_id = telegram_credentials()

    if not token or not chat_id:
        LOG.warning("Telegram credentials missing; message not sent")
        return False

    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    chunks = []
    remaining = message

    while len(remaining) > 3800:
        split_at = remaining.rfind("\n", 0, 3800)
        if split_at < 1000:
            split_at = 3800
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")

    if remaining:
        chunks.append(remaining)

    success = True

    for chunk in chunks:
        try:
            response = HTTP.post(
                url,
                data={"chat_id": chat_id, "text": chunk},
                timeout=(8, 25),
            )

            if response.status_code >= 400 or not response.json().get("ok", False):
                LOG.error(
                    "Telegram sendMessage failed: %s %s",
                    response.status_code,
                    response.text[:300],
                )
                success = False

        except Exception:
            LOG.exception("Telegram sendMessage error")
            success = False

    return success


def telegram_send_photo(path: Path, caption: str) -> bool:
    token, chat_id = telegram_credentials()

    if not token or not chat_id:
        LOG.warning("Telegram credentials missing; photo not sent")
        return False

    url = f"{TELEGRAM_API}/bot{token}/sendPhoto"

    try:
        with path.open("rb") as photo_file:
            response = HTTP.post(
                url,
                data={"chat_id": chat_id, "caption": caption[:1000]},
                files={"photo": (path.name, photo_file, "image/png")},
                timeout=(10, 45),
            )

        if response.status_code >= 400 or not response.json().get("ok", False):
            LOG.error(
                "Telegram sendPhoto failed: %s %s",
                response.status_code,
                response.text[:300],
            )
            return False

        return True

    except Exception:
        LOG.exception("Telegram sendPhoto error")
        return False


# ----------------------------- Trade management -----------------------------

def update_open_trades(
    conn: sqlite3.Connection,
    candles_by_symbol: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    closed = []

    for trade in open_trades(conn):
        symbol = str(trade["symbol"])
        bars = candles_by_symbol.get(symbol)

        if bars is None:
            try:
                bars = get_candles(symbol, "15m", M15_CANDLE_COUNT)
                candles_by_symbol[symbol] = bars
            except Exception as exc:
                LOG.warning("Cannot refresh open trade %s: %s", symbol, exc)
                continue

        next_bar_time = int(trade["next_bar_time_ms"])
        direction = str(trade["direction"])
        tp = float(trade["tp_price"])
        sl = float(trade["sl_price"])

        for bar in bars:
            if int(bar["time"]) < next_bar_time:
                continue

            hit_sl = (
                bar["low"] <= sl
                if direction == "LONG"
                else bar["high"] >= sl
            )
            hit_tp = (
                bar["high"] >= tp
                if direction == "LONG"
                else bar["low"] <= tp
            )

            if not hit_sl and not hit_tp:
                continue

            # If both levels were touched in one candle, assume SL first.
            status, exit_price = (
                ("CLOSED_SL", sl) if hit_sl else ("CLOSED_TP", tp)
            )

            exit_time = (bar["time"] + M15_MS) / 1000

            close_trade(
                conn,
                int(trade["id"]),
                status,
                exit_time,
                exit_price,
            )

            closed.append({
                "symbol": symbol,
                "direction": direction,
                "status": status,
                "entry": float(trade["entry_price"]),
                "exit": exit_price,
                "pnl_pct": trade_pnl_percent(
                    float(trade["entry_price"]),
                    exit_price,
                    direction,
                ),
                "exit_time": exit_time,
            })

            break

    return closed


def persist_current_prices(
    conn: sqlite3.Connection,
    prices: dict[str, Optional[float]],
) -> None:
    now = time.time()

    for row in open_trades(conn):
        price = prices.get(str(row["symbol"]))

        if price is not None and price > 0:
            conn.execute(
                """
                UPDATE trades SET current_price=?,updated_at=?
                WHERE id=? AND status='OPEN'
                """,
                (float(price), now, int(row["id"])),
            )

    conn.commit()


def get_performance(
    conn: sqlite3.Connection,
    prices: dict[str, Optional[float]],
) -> dict[str, Any]:
    closed_rows = list(conn.execute(
        """
        SELECT status,pnl_pct FROM trades
        WHERE status IN ('CLOSED_TP','CLOSED_SL')
        """
    ))

    open_rows = open_trades(conn)
    wins = sum(
        1 for row in closed_rows
        if row["status"] == "CLOSED_TP" and (row["pnl_pct"] or 0) > 0
    )
    losses = len(closed_rows) - wins
    win_rate = 100 * wins / len(closed_rows) if closed_rows else 0.0
    realized = sum(float(row["pnl_pct"] or 0) for row in closed_rows)
    floating = 0.0

    for row in open_rows:
        pnl = trade_pnl_percent(
            float(row["entry_price"]),
            prices.get(str(row["symbol"])),
            str(row["direction"]),
        )
        if pnl is not None:
            floating += pnl

    return {
        "closed_count": len(closed_rows),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "open_count": len(open_rows),
        "realized_pct": realized,
        "floating_pct": floating,
        "total_pct": realized + floating,
        "open_rows": open_rows,
    }


def pct_from_entry(entry: float, level: float, direction: str) -> float:
    if direction == "LONG":
        return (level / entry - 1) * 100
    return (entry / level - 1) * 100


def make_report(
    stats: dict[str, Any],
    new_signals: list[dict[str, Any]],
    closed_trades: list[dict[str, Any]],
    perf: dict[str, Any],
    prices: dict[str, Optional[float]],
) -> str:
    lines = [
        f"🔎 NDS M15 LONG + SHORT v{VERSION} | {dt_text(time.time())}",
        f"Scan: {stats['scanned']} symbols | "
        f"M15 OK {stats['m15_ok']} | errors {stats['errors']} | "
        f"structures {stats['patterns']}",
        f"Universe: {stats['universe_size']} | "
        f"Open slots: {perf['open_count']}/{MAX_OPEN_TRADES}",
        "",
    ]

    for direction, emoji in (("LONG", "🟢"), ("SHORT", "🔴")):
        items = [x for x in new_signals if x["direction"] == direction]
        lines.append(f"{emoji} NEW M15 {direction} SIGNALS: {len(items)}")

        for item in items:
            lines.append(
                f"• {item['symbol']} | Entry {fmt_price(item['entry'])} | "
                f"TP {fmt_price(item['tp'])} "
                f"({fmt_pct(pct_from_entry(item['entry'], item['tp'], direction))}) | "
                f"SL {fmt_price(item['sl']) if item.get('sl') is not None else 'N/A'} | "
                f"M1: {item.get('m1_status', 'NOT CHECKED')} | "
                f"Trade: {'OPENED' if item.get('trade_opened') else 'NOT OPENED'}"
            )

        if not items:
            lines.append("No new signals.")

    if closed_trades:
        lines.extend(["", f"✅ CLOSED THIS RUN: {len(closed_trades)}"])

        for trade in closed_trades:
            emoji = "✅" if trade["status"] == "CLOSED_TP" else "🛑"
            lines.append(
                f"{emoji} {trade['symbol']} {trade['direction']} "
                f"{trade['status']} | Entry {fmt_price(trade['entry'])} | "
                f"Exit {fmt_price(trade['exit'])} | "
                f"PnL {fmt_pct(trade['pnl_pct'])}"
            )

    lines.extend(["", f"📂 OPEN TRADES: {perf['open_count']}"])

    for row in perf["open_rows"]:
        direction = str(row["direction"])
        entry = float(row["entry_price"])
        tp = float(row["tp_price"])
        sl = float(row["sl_price"])
        price = prices.get(str(row["symbol"]))
        pnl = trade_pnl_percent(entry, price, direction)
        emoji = "🟢" if direction == "LONG" else "🔴"

        lines.append(
            f"{emoji} {row['symbol']} {direction} | "
            f"Entry {fmt_price(entry)} | Now {fmt_price(price)} "
            f"({fmt_pct(pnl)}) | TP {fmt_price(tp)} | SL {fmt_price(sl)}"
        )

    if not perf["open_rows"]:
        lines.append("No open paper trades.")

    lines.extend([
        "",
        "📊 PERFORMANCE",
        f"Closed trades: {perf['closed_count']} | "
        f"W/L: {perf['wins']}/{perf['losses']} | "
        f"Win rate: {perf['win_rate']:.2f}%",
        f"Realized PnL: {fmt_pct(perf['realized_pct'])}",
        f"Floating PnL: {fmt_pct(perf['floating_pct'])}",
        f"TOTAL PnL: {fmt_pct(perf['total_pct'])}",
        "PnL is gross equal-notional percentage-point PnL, excluding fees/slippage.",
        "Paper/signal mode only. No real orders are placed.",
    ])

    return "\n".join(lines)


# ----------------------------- Main -----------------------------

def scan_symbol(
    symbol: str,
) -> tuple[str, Optional[list[dict[str, Any]]], Optional[str]]:
    try:
        return symbol, get_candles(symbol, "15m", M15_CANDLE_COUNT), None
    except Exception as exc:
        return symbol, None, str(exc)


def main() -> int:
    started = time.time()
    conn = connect_db()

    try:
        tickers = get_tickers()
    except Exception as exc:
        LOG.exception("Could not load Kraken Futures tickers")
        telegram_send_message(
            "❌ NDS M15 scanner startup error: could not load Kraken tickers.\n"
            + str(exc)
        )
        conn.close()
        return 1

    tickers_by_symbol = ticker_map(tickers)
    universe = get_universe(tickers)

    candles_by_symbol = {}
    errors_by_symbol = {}

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_REQUESTS) as pool:
        futures = {
            pool.submit(scan_symbol, symbol): symbol
            for symbol in universe
        }

        for future in as_completed(futures):
            symbol, candles, error = future.result()

            if candles is not None:
                candles_by_symbol[symbol] = candles
            elif error:
                errors_by_symbol[symbol] = error
                LOG.warning("M15 data error %s: %s", symbol, error)

    try:
        fresh_tickers = ticker_map(get_tickers())
        if fresh_tickers:
            tickers_by_symbol = fresh_tickers
    except Exception as exc:
        LOG.warning("Ticker refresh failed: %s", exc)

    tracked_symbols = (
        set(universe)
        | {str(row["symbol"]) for row in open_trades(conn)}
    )

    prices = {}

    for symbol in tracked_symbols:
        price = current_price_from_ticker(tickers_by_symbol.get(symbol))
        bars = candles_by_symbol.get(symbol)

        if price is None and bars:
            price = bars[-1]["close"]

        prices[symbol] = price

    closed_trades = update_open_trades(conn, candles_by_symbol)

    patterns_count = 0
    candidates = []

    for symbol, bars in candles_by_symbol.items():
        for direction in ("LONG", "SHORT"):
            pattern = detect_latest_pattern(symbol, bars, direction)

            if not pattern:
                continue

            age = time.time() - pattern["confirmation_time"]

            if 0 <= age <= MAX_SIGNAL_AGE_HOURS * 3600:
                patterns_count += 1
                candidates.append(pattern)

    candidates.sort(
        key=lambda item: item["confirmation_time"],
        reverse=True,
    )

    new_signals = []

    for pattern in candidates:
        if pattern_seen(conn, pattern["key"]):
            continue

        symbol = pattern["symbol"]
        direction = pattern["direction"]
        bars = candles_by_symbol[symbol]
        entry = prices.get(symbol)

        if entry is None or entry <= 0:
            save_pattern_decision(conn, pattern, "SKIPPED", "No current price")
            continue

        tp = float(pattern["p3_price"])
        terminal = float(pattern["p4_price"])

        if direction == "LONG":
            if entry <= terminal:
                save_pattern_decision(
                    conn, pattern, "SKIPPED",
                    "Price at/below 2 RED; LONG setup invalidated",
                )
                continue
            if tp <= entry:
                save_pattern_decision(
                    conn, pattern, "SKIPPED", "LONG TP is not above entry"
                )
                continue
        else:
            if entry >= terminal:
                save_pattern_decision(
                    conn, pattern, "SKIPPED",
                    "Price at/above 2 GREEN; SHORT setup invalidated",
                )
                continue
            if tp >= entry:
                save_pattern_decision(
                    conn, pattern, "SKIPPED", "SHORT TP is not below entry"
                )
                continue

        # M15 chart is created before checking M1, so M1 cannot block it.
        signal = {
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "tp": tp,
            "sl": None,
            "pattern": pattern,
            "chart": None,
            "m1_chart": None,
            "m1_status": "NOT CHECKED",
            "m1_detail": "",
            "trade_opened": False,
        }

        try:
            signal["chart"] = create_signal_chart(
                symbol, bars, pattern, entry, tp, None, timeframe="M15"
            )
        except Exception:
            LOG.exception("M15 chart creation failed for %s", symbol)

        m1_result = analyze_m1_for_signal(
            symbol, direction, entry, tp, None
        )

        signal["m1_chart"] = m1_result.get("chart")
        signal["m1_status"] = m1_result.get("status", "M1 ANALYSIS ERROR")
        signal["m1_detail"] = m1_result.get("detail", "")

        # No same-direction M1 structure means no trade.
        m1_confirmed = (
            m1_result.get("direction") == direction
            and m1_result.get("status") == "M1 CONFIRMS M15"
            and m1_result.get("chart") is not None
        )

        if not m1_confirmed:
            new_signals.append(signal)
            save_pattern_decision(
                conn,
                pattern,
                "M15_ONLY_NO_TRADE",
                f"M1 did not confirm: {signal['m1_status']}",
            )
            continue

        if any(row["symbol"] == symbol for row in open_trades(conn)):
            signal["m1_status"] += " | TRADE SKIPPED: SYMBOL ALREADY OPEN"
            new_signals.append(signal)
            save_pattern_decision(
                conn, pattern, "M1_CONFIRMED_SKIPPED",
                "Another open trade exists for this symbol",
            )
            continue

        if len(open_trades(conn)) >= MAX_OPEN_TRADES:
            signal["m1_status"] += " | TRADE SKIPPED: MAX OPEN TRADES"
            new_signals.append(signal)
            save_pattern_decision(
                conn, pattern, "M1_CONFIRMED_SKIPPED",
                "Maximum open trades reached",
            )
            continue

        try:
            bars15 = get_candles(symbol, "15m", M15_CANDLE_COUNT)
        except Exception as exc:
            signal["m1_status"] += " | TRADE SKIPPED: M15 DATA ERROR"
            signal["m1_detail"] = str(exc)[:180]
            new_signals.append(signal)
            save_pattern_decision(
                conn, pattern, "M1_CONFIRMED_SKIPPED",
                f"M15 candle fetch failed: {exc}",
            )
            continue

        sl = (
            find_nearest_valid_m15_low(bars15, entry)
            if direction == "LONG"
            else find_nearest_valid_m15_high(bars15, entry)
        )

        if (
            sl is None
            or (direction == "LONG" and sl >= entry)
            or (direction == "SHORT" and sl <= entry)
        ):
            signal["m1_status"] += " | TRADE SKIPPED: NO VALID SL"
            new_signals.append(signal)
            save_pattern_decision(
                conn, pattern, "M1_CONFIRMED_SKIPPED",
                f"No valid M15 stop loss for {direction}",
            )
            continue

        signal["sl"] = sl
        signal["trade_opened"] = True

        save_pattern_decision(
            conn, pattern, "TRADE",
            "M1 confirmed same direction; paper trade opened",
        )

        insert_trade(conn, pattern, time.time(), entry, tp, sl)
        new_signals.append(signal)

    # Refresh latest prices for open trades.
    for row in open_trades(conn):
        symbol = str(row["symbol"])
        if prices.get(symbol) is None:
            prices[symbol] = float(row["entry_price"])

    try:
        final_tickers = ticker_map(get_tickers())

        for symbol in tracked_symbols | {
            str(row["symbol"]) for row in open_trades(conn)
        }:
            live = current_price_from_ticker(final_tickers.get(symbol))
            if live is not None and live > 0:
                prices[symbol] = live

    except Exception as exc:
        LOG.warning("Final ticker refresh failed: %s", exc)

    persist_current_prices(conn, prices)
    perf = get_performance(conn, prices)

    stats = {
        "scanned": len(universe),
        "m15_ok": len(candles_by_symbol),
        "errors": len(errors_by_symbol),
        "patterns": patterns_count,
        "universe_size": len(universe),
    }

    report = make_report(
        stats, new_signals, closed_trades, perf, prices
    )

    LOG.info("\n%s", report)
    telegram_send_message(report)

    # Always attempt M15 chart first. If an M1 confirmation chart exists,
    # send that as a separate image afterward.
    sent_charts = 0

    for signal in new_signals:
        emoji = "🟢" if signal["direction"] == "LONG" else "🔴"
        direction = signal["direction"]

        tp_pct = pct_from_entry(signal["entry"], signal["tp"], direction)

        sl = signal.get("sl")
        sl_pct = (
            pct_from_entry(signal["entry"], sl, direction)
            if sl is not None else None
        )

        chart_path = signal.get("chart")

        if chart_path and sent_charts < MAX_CHARTS_PER_RUN:
            caption = (
                f"{emoji} M15 {direction} | {signal['symbol']}\n"
                f"Entry: {fmt_price(signal['entry'])}\n"
                f"TP: {fmt_price(signal['tp'])} ({fmt_pct(tp_pct)})\n"
                f"SL: {fmt_price(sl) if sl is not None else 'N/A'}"
                f"{' (' + fmt_pct(sl_pct) + ')' if sl_pct is not None else ''}\n"
                f"M1: {signal.get('m1_status', 'NOT CHECKED')}\n"
                f"Paper trade: {'OPENED' if signal.get('trade_opened') else 'NOT OPENED'}\n"
                "M15 chart is sent regardless of the M1 result."
            )

            if telegram_send_photo(chart_path, caption):
                sent_charts += 1

        m1_chart = signal.get("m1_chart")

        if m1_chart and sent_charts < MAX_CHARTS_PER_RUN:
            caption = (
                f"🔎 M1 CONFIRMATION | {signal['symbol']}\n"
                f"M15 direction: {direction}\n"
                f"M1: {signal.get('m1_status', 'UNKNOWN')}\n"
                "Same-direction structure confirmed."
            )

            if telegram_send_photo(m1_chart, caption):
                sent_charts += 1

    LOG.info(
        "Finished in %.1fs | signals=%d | closed=%d | open=%d | charts=%d",
        time.time() - started,
        len(new_signals),
        len(closed_trades),
        perf["open_count"],
        sent_charts,
    )

    conn.commit()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
