#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NDS M15 Long + Short Structure Scanner v1.6.0

Changes:
1. No maximum open-trade limit.
2. No one-open-trade-per-symbol restriction.
3. Every open trade chart is sent to Telegram on every successful run.
4. No chart-send cap.
5. Strict LONG/SHORT structure validation.
6. LONG: GREEN2 > GREEN1 and RED2 < RED1.
7. SHORT: RED2 < RED1 and GREEN2 > GREEN1, mirrored structure.
8. Closed candles only. Paper trading only.
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
    raise RuntimeError(
        "matplotlib is required; install it with: pip install matplotlib"
    ) from exc


# ============================= CONFIGURATION =============================

VERSION = "1.6.0"

FUTURES_REST = "https://futures.kraken.com/derivatives/api/v3"
CHARTS_REST = "https://futures.kraken.com/api/charts/v1"
TELEGRAM_API = "https://api.telegram.org"

UNIVERSE_SIZE = max(2, int(os.getenv("UNIVERSE_SIZE", "100")))
MAX_SIGNAL_AGE_HOURS = max(
    1, int(os.getenv("MAX_SIGNAL_AGE_HOURS", "6"))
)

M15_CANDLE_COUNT = 300
M1_CANDLE_COUNT = 1200

PIVOT_LEFT = 5
PIVOT_RIGHT = 5

M15_PIVOT_LEFT = 2
M15_PIVOT_RIGHT = 2

MAX_CONCURRENT_REQUESTS = 8

REQUIRED_SYMBOLS = ("PF_XBTUSD", "PF_XAUTUSD")

DB_PATH = Path(
    os.getenv("DB_PATH", "nds_m5_long_short_structure_v1.db")
)

CHART_DIR = Path(
    os.getenv("CHART_DIR", "nds_m5_long_short_structure_charts")
)

REPORT_TZ_NAME = os.getenv("REPORT_TIMEZONE", "Europe/Berlin")

try:
    REPORT_TZ = ZoneInfo(REPORT_TZ_NAME)
except Exception:
    REPORT_TZ = timezone.utc

M1_MS = 60 * 1000
M15_MS = 15 * 60 * 1000

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": f"NDS-M15-Long-Short-Scanner/{VERSION}",
    "Accept": "application/json",
})

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)

LOG = logging.getLogger("nds_m15_long_short")


# ============================= HELPERS =============================

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
        return f"{value:,.5f}".rstrip("0").rstrip(".")
    return f"{value:.8f}".rstrip("0").rstrip(".")


def fmt_pct(value: Optional[float], signed: bool = True) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2f}%" if signed else f"{value:.2f}%"


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
            response = HTTP.get(
                url, params=params, timeout=(8, 25)
            )

            if response.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(
                    f"HTTP {response.status_code}: "
                    f"{response.text[:200]}"
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

    raise RuntimeError(f"Request failed for {url}: {last_exc}")


# ============================= KRAKEN DATA =============================

def get_tickers() -> list[dict[str, Any]]:
    payload = request_json(f"{FUTURES_REST}/tickers")
    tickers = payload.get("tickers")

    if not isinstance(tickers, list):
        raise RuntimeError("Kraken response contains no ticker list")

    return [x for x in tickers if isinstance(x, dict)]


def extract_volume(ticker: dict[str, Any]) -> float:
    for key in (
        "vol24h", "volume24h", "volume", "volume24H",
        "turnover24h", "usdVolume24h"
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


def ticker_map(
    tickers: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:

    return {
        str(item.get("symbol", "")).upper(): item
        for item in tickers if item.get("symbol")
    }


def current_price_from_ticker(
    ticker: Optional[dict[str, Any]]
) -> Optional[float]:

    if not ticker:
        return None

    bid = (
        as_float(ticker.get("bid"))
        or as_float(ticker.get("bidPrice"))
    )

    ask = (
        as_float(ticker.get("ask"))
        or as_float(ticker.get("askPrice"))
    )

    if (
        bid is not None and ask is not None
        and bid > 0 and ask >= bid
    ):
        return (bid + ask) / 2.0

    for key in (
        "markPrice", "mark_price", "last", "lastPrice",
        "lastTradePrice", "last_price",
        "indexPrice", "index_price"
    ):
        value = as_float(ticker.get(key))
        if value is not None and value > 0:
            return value

    return None


# ============================= CANDLES =============================

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

    params = {
        "from": int(now - (timeframe_ms / 1000) * (limit + 5)),
        "to": int(now),
        "count": limit,
    }

    payload = request_json(
        f"{CHARTS_REST}/trade/{symbol}/{resolution}",
        params=params,
    )

    rows = payload.get("candles", [])

    if not isinstance(rows, list):
        raise RuntimeError(f"Invalid candles for {symbol}")

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

        ts_i = int(ts)

        if ts_i < 1_000_000_000_000:
            ts_i *= 1000

        # Do not use the currently open candle.
        if ts_i + timeframe_ms > now_ms:
            continue

        if low > high or min(opn, high, low, close) <= 0:
            continue

        by_time[ts_i] = {
            "time": ts_i,
            "open": float(opn),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": as_float(row.get("volume")) or 0.0,
        }

    return [by_time[k] for k in sorted(by_time)][-limit:]


# ============================= PIVOTS =============================

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
        value < min(candles[j]["low"]
                    for j in range(index - left, index))
        and
        value < min(candles[j]["low"]
                    for j in range(index + 1, index + right + 1))
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
        value > max(candles[j]["high"]
                    for j in range(index - left, index))
        and
        value > max(candles[j]["high"]
                    for j in range(index + 1, index + right + 1))
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


# ============================= STRICT STRATEGY =============================

def detect_m15_structure_pattern(
    symbol: str,
    candles: list[dict[str, Any]],
    direction: str,
    timeframe_ms: int = M15_MS,
) -> Optional[dict[str, Any]]:
    """
    LONG:
        START = absolute lowest low
        GREEN1 = H1
        RED1 = L1
        GREEN2 = H2, strictly higher than GREEN1
        RED2 = L2, strictly lower than RED1 but above START

    SHORT:
        START = absolute highest high
        RED1 = L1
        GREEN1 = H1
        RED2 = L2, strictly lower than RED1
        GREEN2 = H2, strictly higher than GREEN1 but below START

    Every pivot must be confirmed using closed candles.
    """

    if len(candles) < PIVOT_LEFT + PIVOT_RIGHT + 20:
        return None

    lows, highs = find_confirmed_pivots(
        candles, PIVOT_LEFT, PIVOT_RIGHT
    )

    if direction not in ("LONG", "SHORT"):
        return None

    # ----------------------------- LONG -----------------------------

    if direction == "LONG":

        for p4 in reversed(lows):
            l2 = candles[p4]["low"]

            for p3 in reversed([i for i in highs if i < p4]):
                h2 = candles[p3]["high"]

                for p2 in reversed([i for i in lows if i < p3]):
                    l1 = candles[p2]["low"]

                    # CRITICAL FIX:
                    # RED2 must be STRICTLY LOWER than RED1.
                    if l2 >= l1:
                        continue

                    # GREEN2 must be strictly higher than GREEN1.
                    # Check later when GREEN1 is identified.

                    # GREEN2 must be the highest high from RED1 to RED2.
                    if h2 != max(
                        c["high"] for c in candles[p2:p4 + 1]
                    ):
                        continue

                    # RED2 must be the lowest low from GREEN2 to RED2.
                    if l2 != min(
                        c["low"] for c in candles[p3:p4 + 1]
                    ):
                        continue

                    for p1 in reversed([i for i in highs if i < p2]):
                        h1 = candles[p1]["high"]

                        # GREEN2 strictly higher than GREEN1.
                        if h2 <= h1:
                            continue

                        starts = [
                            i for i in lows
                            if i < p1 and i >= max(0, p1 - 240)
                        ]

                        for st in reversed(starts):
                            start_price = candles[st]["low"]

                            # START must be lower than both red points.
                            if start_price >= l1 or start_price >= l2:
                                continue

                            # START is the absolute lowest low.
                            if start_price != min(
                                c["low"] for c in candles[st:p4 + 1]
                            ):
                                continue

                            # GREEN1 is the highest high from START to RED1.
                            if h1 != max(
                                c["high"] for c in candles[st:p2 + 1]
                            ):
                                continue

                            # GREEN2 is the highest high of the whole structure.
                            if h2 != max(
                                c["high"] for c in candles[st:p4 + 1]
                            ):
                                continue

                            # Explicit final validation.
                            if not (
                                st < p1 < p2 < p3 < p4
                                and h2 > h1
                                and l2 < l1
                                and start_price < l1
                                and start_price < l2
                            ):
                                continue

                            confirmation_i = p4 + PIVOT_RIGHT

                            if confirmation_i >= len(candles):
                                continue

                            pts = {
                                "start": st,
                                "p1": p1,
                                "p2": p2,
                                "p3": p3,
                                "p4": p4,
                            }

                            key = "LONG|" + symbol + "|" + "|".join(
                                str(candles[pts[k]]["time"])
                                for k in (
                                    "start", "p1", "p2", "p3", "p4"
                                )
                            )

                            return {
                                "symbol": symbol,
                                "direction": "LONG",
                                "points": pts,
                                "key": key,
                                "confirmation_time": (
                                    candles[confirmation_i]["time"]
                                    + timeframe_ms
                                ) / 1000.0,
                                "start_price": start_price,
                                "p1_price": h1,
                                "p2_price": l1,
                                "p3_price": h2,
                                "p4_price": l2,
                                "terminal_time": (
                                    candles[p4]["time"] / 1000.0
                                ),
                            }

    # ----------------------------- SHORT -----------------------------

    else:

        for p4 in reversed(highs):
            h2 = candles[p4]["high"]

            for p3 in reversed([i for i in lows if i < p4]):
                l2 = candles[p3]["low"]

                for p2 in reversed([i for i in highs if i < p3]):
                    h1 = candles[p2]["high"]

                    # GREEN2 must be strictly higher than GREEN1.
                    if h2 <= h1:
                        continue

                    for p1 in reversed([i for i in lows if i < p2]):
                        l1 = candles[p1]["low"]

                        # RED2 must be strictly lower than RED1.
                        if l2 >= l1:
                            continue

                        # RED2 is the lowest low between GREEN1 and GREEN2.
                        if l2 != min(
                            c["low"] for c in candles[p2:p4 + 1]
                        ):
                            continue

                        # GREEN2 is the highest high from RED2 to GREEN2.
                        if h2 != max(
                            c["high"] for c in candles[p3:p4 + 1]
                        ):
                            continue

                        # GREEN1 is the highest high between RED1 and RED2.
                        if h1 != max(
                            c["high"] for c in candles[p1:p3 + 1]
                        ):
                            continue

                        starts = [
                            i for i in highs
                            if i < p1 and i >= max(0, p1 - 240)
                        ]

                        for st in reversed(starts):
                            start_price = candles[st]["high"]

                            if start_price <= h1 or start_price <= h2:
                                continue

                            # START must be the absolute highest high.
                            if start_price != max(
                                c["high"] for c in candles[st:p4 + 1]
                            ):
                                continue

                            # RED1 must be the lowest low from START to GREEN1.
                            if l1 != min(
                                c["low"] for c in candles[st:p2 + 1]
                            ):
                                continue

                            # RED2 must be the absolute lowest low.
                            if l2 != min(
                                c["low"] for c in candles[st:p4 + 1]
                            ):
                                continue

                            # Explicit final validation.
                            if not (
                                st < p1 < p2 < p3 < p4
                                and l2 < l1
                                and h2 > h1
                                and start_price > h1
                                and start_price > h2
                            ):
                                continue

                            confirmation_i = p4 + PIVOT_RIGHT

                            if confirmation_i >= len(candles):
                                continue

                            pts = {
                                "start": st,
                                "p1": p1,
                                "p2": p2,
                                "p3": p3,
                                "p4": p4,
                            }

                            key = "SHORT|" + symbol + "|" + "|".join(
                                str(candles[pts[k]]["time"])
                                for k in (
                                    "start", "p1", "p2", "p3", "p4"
                                )
                            )

                            return {
                                "symbol": symbol,
                                "direction": "SHORT",
                                "points": pts,
                                "key": key,
                                "confirmation_time": (
                                    candles[confirmation_i]["time"]
                                    + timeframe_ms
                                ) / 1000.0,
                                "start_price": start_price,
                                "p1_price": l1,
                                "p2_price": h1,
                                "p3_price": l2,
                                "p4_price": h2,
                                "terminal_time": (
                                    candles[p4]["time"] / 1000.0
                                ),
                            }

    return None


def detect_latest_pattern(
    symbol: str,
    candles: list[dict[str, Any]],
    direction: str,
) -> Optional[dict[str, Any]]:

    return detect_m15_structure_pattern(
        symbol, candles, direction, timeframe_ms=M15_MS
    )


# ============================= STOP-LOSS PIVOTS =============================

def find_nearest_valid_m15_low(
    candles15: list[dict[str, Any]],
    entry: float,
) -> Optional[float]:

    lows, _ = find_confirmed_pivots(
        candles15, M15_PIVOT_LEFT, M15_PIVOT_RIGHT
    )

    eligible = []

    for i in lows:
        price = candles15[i]["low"]

        if price >= entry:
            continue

        if any(
            bar["low"] <= price for bar in candles15[i + 1:]
        ):
            continue

        eligible.append((i, price))

    if not eligible:
        return None

    eligible.sort(key=lambda x: x[0], reverse=True)
    return eligible[0][1]


def find_nearest_valid_m15_high(
    candles15: list[dict[str, Any]],
    entry: float,
) -> Optional[float]:

    _, highs = find_confirmed_pivots(
        candles15, M15_PIVOT_LEFT, M15_PIVOT_RIGHT
    )

    eligible = []

    for i in highs:
        price = candles15[i]["high"]

        if price <= entry:
            continue

        if any(
            bar["high"] >= price for bar in candles15[i + 1:]
        ):
            continue

        eligible.append((i, price))

    if not eligible:
        return None

    eligible.sort(key=lambda x: x[0], reverse=True)
    return eligible[0][1]


# ============================= DATABASE =============================

def connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA busy_timeout=30000")

    conn.executescript("""
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
            created_at REAL NOT NULL,
            FOREIGN KEY(pattern_key) REFERENCES patterns(pattern_key)
        );

        CREATE INDEX IF NOT EXISTS idx_trades_status
        ON trades(status);

        CREATE INDEX IF NOT EXISTS idx_trades_symbol_status
        ON trades(symbol, status);
    """)

    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(trades)")
    }

    if "direction" not in cols:
        conn.execute(
            "ALTER TABLE trades ADD COLUMN direction "
            "TEXT NOT NULL DEFAULT 'LONG'"
        )

    if "current_price" not in cols:
        conn.execute(
            "ALTER TABLE trades ADD COLUMN current_price REAL"
        )

    if "updated_at" not in cols:
        conn.execute(
            "ALTER TABLE trades ADD COLUMN updated_at REAL"
        )

    conn.commit()
    return conn


def pattern_seen(conn: sqlite3.Connection, key: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM patterns WHERE pattern_key=?", (key,)
    ).fetchone() is not None


def save_pattern_decision(
    conn: sqlite3.Connection,
    pattern: dict[str, Any],
    decision: str,
    reason: str = "",
) -> None:

    conn.execute(
        """INSERT OR IGNORE INTO patterns
        (pattern_key,symbol,detected_at,decision,reason)
        VALUES(?,?,?,?,?)""",
        (
            pattern["key"],
            pattern["symbol"],
            time.time(),
            decision,
            reason[:500],
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

    direction = str(pattern["direction"])
    entry_ms = int(entry_time * 1000)
    next_bar_time_ms = ((entry_ms // M15_MS) + 1) * M15_MS

    conn.execute(
        """INSERT INTO trades
        (pattern_key,symbol,direction,entry_time,next_bar_time_ms,
         entry_price,tp_price,sl_price,current_price,updated_at,
         status,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,'OPEN',?)""",
        (
            pattern["key"],
            pattern["symbol"],
            direction,
            entry_time,
            next_bar_time_ms,
            entry_price,
            tp,
            sl,
            entry_price,
            time.time(),
            time.time(),
        ),
    )

    conn.commit()


def trade_pnl_percent(
    entry: float,
    price: Optional[float],
    direction: str = "LONG",
) -> Optional[float]:

    if price is None or entry <= 0 or price <= 0:
        return None

    if direction == "LONG":
        return (price / entry - 1.0) * 100.0

    return (entry / price - 1.0) * 100.0


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

    pnl = trade_pnl_percent(
        float(row["entry_price"]),
        exit_price,
        str(row["direction"]),
    )

    conn.execute(
        """UPDATE trades
        SET status=?,exit_time=?,exit_price=?,pnl_pct=?
        WHERE id=? AND status='OPEN'""",
        (
            status,
            exit_time,
            exit_price,
            pnl,
            trade_id,
        ),
    )

    conn.commit()


# ============================= CHARTS =============================

def draw_candles(ax, candles: list[dict[str, Any]]) -> None:
    for x, c in enumerate(candles):
        color = (
            "#18a558"
            if c["close"] >= c["open"]
            else "#e74c3c"
        )

        ax.vlines(
            x, c["low"], c["high"],
            color=color, linewidth=1.0, zorder=2
        )

        body_low = min(c["open"], c["close"])
        body_height = max(
            abs(c["close"] - c["open"]),
            abs(c["close"]) * 0.000002,
        )

        ax.add_patch(Rectangle(
            (x - 0.31, body_low),
            0.62,
            body_height,
            facecolor=color,
            edgecolor=color,
            linewidth=0.5,
            zorder=3,
        ))


def format_chart_xaxis(ax, candles: list[dict[str, Any]]) -> None:
    n = min(8, len(candles))

    ticks = sorted(set(
        round(i * (len(candles) - 1) / max(1, n - 1))
        for i in range(n)
    ))

    ax.set_xticks(ticks)

    ax.set_xticklabels([
        datetime.fromtimestamp(
            candles[i]["time"] / 1000,
            timezone.utc,
        ).astimezone(REPORT_TZ).strftime("%m-%d %H:%M")
        for i in ticks
    ], rotation=25, ha="right", fontsize=8)


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

    left_edge = max(
        0, min(min(pts.values()) - 18, len(candles) - 175)
    )

    chart_candles = candles[left_edge:]

    if len(chart_candles) < 10:
        chart_candles = candles[-100:]
        left_edge = len(candles) - len(chart_candles)

    fig, ax = plt.subplots(figsize=(13, 7), dpi=135)

    draw_candles(ax, chart_candles)

    if direction == "LONG":
        styles = {
            "start": ("START", "#2457c5", "v", "low"),
            "p1": ("1 GREEN", "#149447", "^", "high"),
            "p2": ("1 RED", "#db2b39", "v", "low"),
            "p3": ("2 GREEN", "#149447", "^", "high"),
            "p4": ("2 RED", "#db2b39", "v", "low"),
        }
    else:
        styles = {
            "start": ("START", "#2457c5", "^", "high"),
            "p1": ("1 RED", "#db2b39", "v", "low"),
            "p2": ("1 GREEN", "#149447", "^", "high"),
            "p3": ("2 RED", "#db2b39", "v", "low"),
            "p4": ("2 GREEN", "#149447", "^", "high"),
        }

    for name, (label, color, marker, field) in styles.items():
        x = pts[name] - left_edge
        y = candles[pts[name]][field]

        if not 0 <= x < len(chart_candles):
            continue

        ax.scatter(
            [x], [y], s=75, marker=marker,
            color=color, edgecolors="white",
            linewidths=0.7, zorder=5,
        )

        offset = 9 if marker == "^" else -14

        ax.annotate(
            label, (x, y),
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

    last_x = len(chart_candles) - 1

    ax.axhline(
        tp, color="#149447", linestyle="--",
        linewidth=1.25, label=f"TP {fmt_price(tp)}"
    )

    if sl is not None:
        ax.axhline(
            sl, color="#db2b39", linestyle="--",
            linewidth=1.25, label=f"SL {fmt_price(sl)}"
        )

    ax.axhline(
        entry, color="#277da1", linestyle=":",
        linewidth=1.5, label=f"ENTRY {fmt_price(entry)}"
    )

    ax.scatter(
        [last_x], [entry], color="#277da1",
        marker="D", s=68, edgecolors="white", zorder=6
    )

    ax.annotate(
        "ENTRY", (last_x, entry),
        xytext=(-8, 8), textcoords="offset points",
        ha="right", fontsize=8, color="#277da1",
        fontweight="bold",
    )

    format_chart_xaxis(ax, chart_candles)

    ax.set_title(
        f"NDS {timeframe} {direction} | {symbol}\n"
        f"Confirmed structure | "
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
        f"{safe}_{timeframe}_{direction}_{int(time.time() * 1000)}.png"
    )

    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)

    return path


def create_open_trade_chart(
    symbol: str,
    candles: list[dict[str, Any]],
    trade: sqlite3.Row,
    current_price: Optional[float],
) -> Path:
    """
    Build a fresh chart for an already-open trade.
    Called on every successful scanner run, without a chart-count limit.
    """

    CHART_DIR.mkdir(parents=True, exist_ok=True)

    chart_candles = candles[-120:]

    fig, ax = plt.subplots(figsize=(13, 7), dpi=135)

    draw_candles(ax, chart_candles)

    direction = str(trade["direction"])
    entry = float(trade["entry_price"])
    tp = float(trade["tp_price"])
    sl = float(trade["sl_price"])

    ax.axhline(
        entry, color="#277da1", linestyle=":",
        linewidth=1.5, label=f"ENTRY {fmt_price(entry)}"
    )

    ax.axhline(
        tp, color="#149447", linestyle="--",
        linewidth=1.4, label=f"TP {fmt_price(tp)}"
    )

    ax.axhline(
        sl, color="#db2b39", linestyle="--",
        linewidth=1.4, label=f"SL {fmt_price(sl)}"
    )

    if current_price is not None and current_price > 0:
        ax.axhline(
            current_price, color="#f39c12",
            linestyle="-.", linewidth=1.4,
            label=f"NOW {fmt_price(current_price)}"
        )

    format_chart_xaxis(ax, chart_candles)

    floating = trade_pnl_percent(
        entry, current_price, direction
    )

    ax.set_title(
        f"NDS OPEN TRADE | {symbol} | {direction}\n"
        f"Trade #{trade['id']} | "
        f"Entry {fmt_price(entry)} | "
        f"Floating PnL {fmt_pct(floating)}",
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
        f"OPEN_{safe}_{direction}_{trade['id']}_"
        f"{int(time.time() * 1000)}.png"
    )

    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)

    return path


# ============================= TELEGRAM =============================

def telegram_credentials() -> tuple[Optional[str], Optional[str]]:
    return (
        os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN"),
        os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT"),
    )


def telegram_send_message(text: str) -> bool:
    token, chat_id = telegram_credentials()

    if not token or not chat_id:
        LOG.warning("Telegram credentials missing")
        return False

    url = f"{TELEGRAM_API}/bot{token}/sendMessage"

    chunks = []
    remaining = text

    while len(remaining) > 3800:
        split_at = remaining.rfind("\n", 0, 3800)

        if split_at < 1000:
            split_at = 3800

        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")

    if remaining:
        chunks.append(remaining)

    ok = True

    for chunk in chunks:
        try:
            response = HTTP.post(
                url,
                data={"chat_id": chat_id, "text": chunk},
                timeout=(8, 25),
            )

            if (
                response.status_code >= 400
                or not response.json().get("ok", False)
            ):
                LOG.error(
                    "Telegram message failed: HTTP %s %s",
                    response.status_code,
                    response.text[:300],
                )
                ok = False

        except Exception:
            LOG.exception("Telegram sendMessage error")
            ok = False

    return ok


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
                data={
                    "chat_id": chat_id,
                    "caption": caption[:1000],
                },
                files={
                    "photo": (path.name, photo_file, "image/png")
                },
                timeout=(10, 45),
            )

        if (
            response.status_code >= 400
            or not response.json().get("ok", False)
        ):
            LOG.error(
                "Telegram sendPhoto failed: HTTP %s %s",
                response.status_code,
                response.text[:300],
            )
            return False

        return True

    except Exception:
        LOG.exception("Telegram sendPhoto error")
        return False


# ============================= M1 CONFIRMATION =============================

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
        candles1 = get_candles(
            symbol, "1m", M1_CANDLE_COUNT
        )

        if len(candles1) < PIVOT_LEFT + PIVOT_RIGHT + 12:
            result["status"] = "INSUFFICIENT M1 DATA"
            result["detail"] = (
                f"Only {len(candles1)} closed M1 candles"
            )
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
            key=lambda x: x["confirmation_time"]
        )

        result["direction"] = pattern["direction"]

        if pattern["direction"] == m15_direction:
            result["status"] = "M1 CONFIRMS M15"
            result["detail"] = (
                f"M1 {pattern['direction']} agrees with M15 "
                f"{m15_direction}"
            )
        else:
            result["status"] = "M1 OPPOSITE - NOT CONFIRMED"
            result["detail"] = (
                f"M1 {pattern['direction']} opposes M15 "
                f"{m15_direction}"
            )

        result["pattern"] = pattern

        result["chart"] = create_signal_chart(
            symbol, candles1, pattern, entry, tp, sl,
            timeframe="M1",
        )

        return result

    except Exception as exc:
        LOG.warning("M1 analysis failed for %s: %s", symbol, exc)
        result["status"] = "M1 ANALYSIS ERROR"
        result["detail"] = str(exc)[:180]
        return result


# ============================= TRADE MANAGEMENT =============================

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
                bars = get_candles(
                    symbol, "15m", M15_CANDLE_COUNT
                )
                candles_by_symbol[symbol] = bars

            except Exception as exc:
                LOG.warning(
                    "Cannot refresh open trade %s: %s",
                    symbol, exc,
                )
                continue

        entry_ms = int(trade["next_bar_time_ms"])
        tp = float(trade["tp_price"])
        sl = float(trade["sl_price"])
        direction = str(trade["direction"])

        for bar in bars:
            if int(bar["time"]) < entry_ms:
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

            # Conservative rule if both levels are touched in one candle.
            if hit_sl:
                status, exit_price = "CLOSED_SL", sl
            else:
                status, exit_price = "CLOSED_TP", tp

            exit_time = (
                bar["time"] + M15_MS
            ) / 1000.0

            close_trade(
                conn, int(trade["id"]),
                status, exit_time, exit_price,
            )

            closed.append({
                "symbol": symbol,
                "status": status,
                "entry": float(trade["entry_price"]),
                "exit": exit_price,
                "pnl_pct": trade_pnl_percent(
                    float(trade["entry_price"]),
                    exit_price,
                    direction,
                ),
                "direction": direction,
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
                """UPDATE trades
                SET current_price=?,updated_at=?
                WHERE id=? AND status='OPEN'""",
                (float(price), now, int(row["id"])),
            )

    conn.commit()


def get_performance(
    conn: sqlite3.Connection,
    prices: dict[str, Optional[float]],
) -> dict[str, Any]:

    closed_rows = list(conn.execute(
        """SELECT status,pnl_pct FROM trades
        WHERE status IN ('CLOSED_TP','CLOSED_SL')"""
    ))

    open_rows = open_trades(conn)

    wins = sum(
        1 for row in closed_rows
        if row["status"] == "CLOSED_TP"
        and (row["pnl_pct"] or 0) > 0
    )

    losses = len(closed_rows) - wins

    win_rate = (
        100.0 * wins / len(closed_rows)
        if closed_rows else 0.0
    )

    realized = sum(
        float(row["pnl_pct"] or 0.0)
        for row in closed_rows
    )

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


def pct_from_entry(
    entry: float,
    level: float,
    direction: str = "LONG",
) -> float:

    if direction == "LONG":
        return (level / entry - 1.0) * 100.0

    return (entry / level - 1.0) * 100.0


# ============================= REPORT =============================

def make_report(
    scan_stats: dict[str, Any],
    new_signals: list[dict[str, Any]],
    closed_trades: list[dict[str, Any]],
    perf: dict[str, Any],
    prices: dict[str, Optional[float]],
) -> str:

    lines = [
        f"🔎 NDS M15 LONG + SHORT v{VERSION} | {dt_text(time.time())}",
        (
            f"Scan: {scan_stats['scanned']} symbols | "
            f"M15 OK {scan_stats['m15_ok']} | "
            f"errors {scan_stats['errors']} | "
            f"structures {scan_stats['patterns']}"
        ),
        (
            f"Universe: {scan_stats['universe_size']} | "
            f"Open trades: {perf['open_count']} | "
            f"Open-trade limit: NONE"
        ),
        "",
    ]

    for direction, emoji in (("LONG", "🟢"), ("SHORT", "🔴")):
        items = [
            x for x in new_signals
            if x["direction"] == direction
        ]

        lines.append(
            f"{emoji} NEW {direction} SIGNALS: {len(items)}"
        )

        for item in items:
            d = item["direction"]
            sl = item.get("sl")

            sl_text = (
                f"SL {fmt_price(sl)} "
                f"({fmt_pct(pct_from_entry(item['entry'], sl, d))})"
                if sl is not None else "SL N/A"
            )

            trade_text = (
                "TRADE OPENED"
                if item.get("trade_opened")
                else "NO TRADE"
            )

            lines.append(
                f"• {item['symbol']} | "
                f"Entry {fmt_price(item['entry'])} | "
                f"TP {fmt_price(item['tp'])} "
                f"({fmt_pct(pct_from_entry(item['entry'], item['tp'], d))}) | "
                f"{sl_text} | {trade_text} | "
                f"M1: {item.get('m1_status', 'NOT CHECKED')}"
            )

        if not items:
            lines.append("No new signals.")

    if closed_trades:
        lines.extend([
            "",
            f"✅ CLOSED THIS RUN: {len(closed_trades)}",
        ])

        for trade in closed_trades:
            emoji = (
                "✅" if trade["status"] == "CLOSED_TP"
                else "🛑"
            )

            lines.append(
                f"{emoji} {trade['symbol']} {trade['direction']} "
                f"{trade['status']} | "
                f"Entry {fmt_price(trade['entry'])} | "
                f"Exit {fmt_price(trade['exit'])} | "
                f"PnL {fmt_pct(trade['pnl_pct'])}"
            )

    lines.extend([
        "",
        f"📂 OPEN TRADES: {perf['open_count']}",
    ])

    for row in perf["open_rows"]:
        direction = str(row["direction"])
        entry = float(row["entry_price"])
        tp = float(row["tp_price"])
        sl = float(row["sl_price"])
        price = prices.get(str(row["symbol"]))

        floating = trade_pnl_percent(entry, price, direction)

        emoji = "🟢" if direction == "LONG" else "🔴"

        lines.append(
            f"{emoji} #{row['id']} {row['symbol']} {direction} | "
            f"Entry {fmt_price(entry)} | "
            f"Now {fmt_price(price)} ({fmt_pct(floating)}) | "
            f"TP {fmt_price(tp)} "
            f"({fmt_pct(pct_from_entry(entry, tp, direction))}) | "
            f"SL {fmt_price(sl)} "
            f"({fmt_pct(pct_from_entry(entry, sl, direction))})"
        )

    if not perf["open_rows"]:
        lines.append("No open paper trades.")

    lines.extend([
        "",
        "📊 PERFORMANCE",
        (
            f"Closed trades: {perf['closed_count']} | "
            f"W/L: {perf['wins']}/{perf['losses']} | "
            f"Win rate: {perf['win_rate']:.2f}%"
        ),
        f"Realized PnL: {fmt_pct(perf['realized_pct'])}",
        f"Floating PnL: {fmt_pct(perf['floating_pct'])}",
        f"TOTAL PnL: {fmt_pct(perf['total_pct'])}",
        "PnL is gross directional percentage-point PnL; fees/slippage excluded.",
        "Paper/signal mode only. No real orders are placed.",
    ])

    return "\n".join(lines)


# ============================= SCANNING =============================

def scan_symbol(
    symbol: str,
) -> tuple[str, Optional[list[dict[str, Any]]], Optional[str]]:

    try:
        return (
            symbol,
            get_candles(symbol, "15m", M15_CANDLE_COUNT),
            None,
        )
    except Exception as exc:
        return symbol, None, str(exc)


# ============================= MAIN =============================

def main() -> int:
    started = time.time()

    conn = connect_db()

    try:
        tickers = get_tickers()

    except Exception as exc:
        message = (
            "❌ NDS M15 LONG+SHORT scanner startup error.\n"
            f"{exc}"
        )

        LOG.exception("Ticker fetch failed")
        telegram_send_message(message)
        conn.close()
        return 1

    tickers_by_symbol = ticker_map(tickers)
    universe = get_universe(tickers)

    LOG.info("Selected %d symbols", len(universe))

    candles_by_symbol: dict[
        str, list[dict[str, Any]]
    ] = {}

    errors_by_symbol: dict[str, str] = {}

    with ThreadPoolExecutor(
        max_workers=MAX_CONCURRENT_REQUESTS
    ) as pool:

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
                LOG.warning(
                    "M15 data error %s: %s", symbol, error
                )

    try:
        fresh_tickers = get_tickers()
        fresh_map = ticker_map(fresh_tickers)

        if fresh_map:
            tickers_by_symbol = fresh_map

    except Exception as exc:
        LOG.warning("Ticker refresh failed: %s", exc)

    tracked_symbols = (
        set(universe)
        | {
            str(row["symbol"])
            for row in open_trades(conn)
        }
    )

    prices: dict[str, Optional[float]] = {}

    for symbol in tracked_symbols:
        price = current_price_from_ticker(
            tickers_by_symbol.get(symbol)
        )

        bars = candles_by_symbol.get(symbol)

        if price is None and bars:
            price = bars[-1]["close"]

        prices[symbol] = price

    # Update previously open paper trades.
    closed_trades = update_open_trades(
        conn, candles_by_symbol
    )

    new_signals = []
    patterns_count = 0
    candidates = []

    for symbol, bars in candles_by_symbol.items():
        for direction in ("LONG", "SHORT"):
            pattern = detect_latest_pattern(
                symbol, bars, direction
            )

            if not pattern:
                continue

            age = (
                time.time()
                - float(pattern["confirmation_time"])
            )

            if age < 0 or age > MAX_SIGNAL_AGE_HOURS * 3600:
                continue

            patterns_count += 1
            candidates.append(pattern)

    candidates.sort(
        key=lambda item: item["confirmation_time"],
        reverse=True,
    )

    for pattern in candidates:
        if pattern_seen(conn, pattern["key"]):
            continue

        symbol = pattern["symbol"]
        bars = candles_by_symbol[symbol]
        entry = prices.get(symbol)

        if entry is None or entry <= 0:
            save_pattern_decision(
                conn, pattern, "SKIPPED",
                "No current market price",
            )
            continue

        direction = str(pattern["direction"])
        tp = float(pattern["p3_price"])
        terminal = float(pattern["p4_price"])

        # Final structural price validation before considering entry.
        if direction == "LONG":
            if not (
                pattern["p3_price"] > pattern["p1_price"]
                and pattern["p4_price"] < pattern["p2_price"]
                and pattern["start_price"] < pattern["p4_price"]
            ):
                save_pattern_decision(
                    conn, pattern, "SKIPPED",
                    "Strict LONG structure validation failed",
                )
                continue

            if entry <= terminal:
                save_pattern_decision(
                    conn, pattern, "SKIPPED",
                    "Current price at/below RED2; LONG invalidated",
                )
                continue

            if tp <= entry:
                save_pattern_decision(
                    conn, pattern, "SKIPPED",
                    "LONG TP is not above entry",
                )
                continue

        else:
            if not (
                pattern["p4_price"] > pattern["p2_price"]
                and pattern["p3_price"] < pattern["p1_price"]
                and pattern["start_price"] > pattern["p4_price"]
            ):
                save_pattern_decision(
                    conn, pattern, "SKIPPED",
                    "Strict SHORT structure validation failed",
                )
                continue

            if entry >= terminal:
                save_pattern_decision(
                    conn, pattern, "SKIPPED",
                    "Current price at/above GREEN2; SHORT invalidated",
                )
                continue

            if tp >= entry:
                save_pattern_decision(
                    conn, pattern, "SKIPPED",
                    "SHORT TP is not below entry",
                )
                continue

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
                symbol, bars, pattern,
                entry, tp, None, timeframe="M15",
            )

        except Exception:
            LOG.exception(
                "M15 chart creation failed for %s", symbol
            )

        m1_result = analyze_m1_for_signal(
            symbol, direction, entry, tp, None
        )

        signal["m1_chart"] = m1_result.get("chart")
        signal["m1_status"] = m1_result.get(
            "status", "M1 ANALYSIS ERROR"
        )
        signal["m1_detail"] = m1_result.get("detail", "")

        m1_confirmed = (
            m1_result.get("direction") == direction
            and m1_result.get("status") == "M1 CONFIRMS M15"
            and m1_result.get("chart") is not None
        )

        if not m1_confirmed:
            new_signals.append(signal)

            save_pattern_decision(
                conn, pattern, "M15_ONLY_NO_TRADE",
                f"M1 did not confirm: {signal['m1_status']}",
            )
            continue

        # No maximum open-trade count.
        # No one-open-trade-per-symbol restriction.
        # Each unique, valid, M1-confirmed pattern can open a paper trade.

        try:
            bars15 = get_candles(
                symbol, "15m", M15_CANDLE_COUNT
            )

            candles_by_symbol[symbol] = bars15

        except Exception as exc:
            signal["m1_status"] += " | TRADE SKIPPED: M15 DATA ERROR"
            signal["m1_detail"] = str(exc)[:180]
            new_signals.append(signal)

            save_pattern_decision(
                conn, pattern, "M1_CONFIRMED_SKIPPED",
                f"M15 fetch failed: {exc}",
            )
            continue

        if direction == "LONG":
            sl = find_nearest_valid_m15_low(
                bars15, entry
            )
        else:
            sl = find_nearest_valid_m15_high(
                bars15, entry
            )

        if (
            sl is None
            or (
                direction == "LONG"
                and sl >= entry
            )
            or (
                direction == "SHORT"
                and sl <= entry
            )
        ):
            signal["m1_status"] += " | TRADE SKIPPED: NO VALID SL"
            new_signals.append(signal)

            save_pattern_decision(
                conn, pattern, "M1_CONFIRMED_SKIPPED",
                f"No valid M15 stop for {direction}",
            )
            continue

        signal["sl"] = sl

        try:
            insert_trade(
                conn, pattern, time.time(),
                entry, tp, sl,
            )

            signal["trade_opened"] = True

            save_pattern_decision(
                conn, pattern, "TRADE",
                "M1 confirmed same direction; paper trade opened",
            )

        except sqlite3.IntegrityError:
            LOG.warning(
                "Duplicate pattern trade prevented: %s",
                pattern["key"],
            )

            signal["m1_status"] += " | DUPLICATE PATTERN"
            signal["trade_opened"] = False

        new_signals.append(signal)

    # Refresh current prices for every open trade, including new trades.
    current_open = open_trades(conn)

    tracked_symbols |= {
        str(row["symbol"]) for row in current_open
    }

    for row in current_open:
        symbol = str(row["symbol"])

        if prices.get(symbol) is None:
            prices[symbol] = float(row["entry_price"])

    try:
        final_tickers = ticker_map(get_tickers())

        for symbol in tracked_symbols:
            live = current_price_from_ticker(
                final_tickers.get(symbol)
            )

            if live is not None and live > 0:
                prices[symbol] = live

    except Exception as exc:
        LOG.warning("Final price refresh failed: %s", exc)

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

    # Send every new signal chart.
    # There is deliberately no MAX_CHARTS_PER_RUN limit.
    for signal in new_signals:
        emoji = (
            "🟢" if signal["direction"] == "LONG"
            else "🔴"
        )

        direction = signal["direction"]
        entry = signal["entry"]
        tp = signal["tp"]
        sl = signal.get("sl")

        caption = (
            f"{emoji} M15 NEW {direction} | {signal['symbol']}\n"
            f"Entry: {fmt_price(entry)}\n"
            f"TP: {fmt_price(tp)} "
            f"({fmt_pct(pct_from_entry(entry, tp, direction))})\n"
        )

        if sl is not None:
            caption += (
                f"SL: {fmt_price(sl)} "
                f"({fmt_pct(pct_from_entry(entry, sl, direction))})\n"
            )
        else:
            caption += "SL: N/A; no trade opened.\n"

        caption += (
            f"M1 analysis: {signal.get('m1_status', 'UNKNOWN')}\n"
            f"Paper trade: "
            f"{'OPENED' if signal.get('trade_opened') else 'NOT OPENED'}"
        )

        chart_path = signal.get("chart")

        if chart_path:
            telegram_send_photo(chart_path, caption)

        m1_chart = signal.get("m1_chart")

        if m1_chart:
            m1_caption = (
                f"🔎 M1 STRUCTURE REVIEW | {signal['symbol']}\n"
                f"M15 direction: {direction}\n"
                f"M1 status: {signal.get('m1_status', 'UNKNOWN')}\n"
                f"{signal.get('m1_detail', '')}"
            )

            telegram_send_photo(m1_chart, m1_caption)

    # ==========================================================
    # IMPORTANT CHANGE:
    # Regenerate and send a fresh chart for EVERY open trade
    # on EVERY successful scanner run.
    # No chart count limit and no symbol-based filtering.
    # ==========================================================

    open_chart_count = 0

    for trade in open_trades(conn):
        symbol = str(trade["symbol"])

        bars = candles_by_symbol.get(symbol)

        if not bars:
            try:
                bars = get_candles(
                    symbol, "15m", M15_CANDLE_COUNT
                )
                candles_by_symbol[symbol] = bars

            except Exception as exc:
                LOG.warning(
                    "Open-trade chart candle fetch failed "
                    "for trade #%s (%s): %s",
                    trade["id"], symbol, exc,
                )
                continue

        current_price = prices.get(symbol)

        try:
            chart_path = create_open_trade_chart(
                symbol, bars, trade, current_price
            )

            direction = str(trade["direction"])
            emoji = "🟢" if direction == "LONG" else "🔴"

            entry = float(trade["entry_price"])
            tp = float(trade["tp_price"])
            sl = float(trade["sl_price"])

            floating = trade_pnl_percent(
                entry, current_price, direction
            )

            caption = (
                f"📊 OPEN TRADE UPDATE #{trade['id']}\n"
                f"{emoji} {symbol} | {direction}\n"
                f"Entry: {fmt_price(entry)}\n"
                f"Current: {fmt_price(current_price)}\n"
                f"Floating PnL: {fmt_pct(floating)}\n"
                f"TP: {fmt_price(tp)} "
                f"({fmt_pct(pct_from_entry(entry, tp, direction))})\n"
                f"SL: {fmt_price(sl)} "
                f"({fmt_pct(pct_from_entry(entry, sl, direction))})\n"
                f"Chart refreshed: {dt_text(time.time())}\n"
                "Paper trade only."
            )

            if telegram_send_photo(chart_path, caption):
                open_chart_count += 1

        except Exception:
            LOG.exception(
                "Open-trade chart failed for trade #%s",
                trade["id"],
            )

    LOG.info(
        "Finished in %.1fs | new signals=%d | closed=%d | "
        "open=%d | open charts sent=%d",
        time.time() - started,
        len(new_signals),
        len(closed_trades),
        len(open_trades(conn)),
        open_chart_count,
    )

    conn.commit()
    conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
