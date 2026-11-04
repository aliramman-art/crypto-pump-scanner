#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NDS M5 Long + Short Structure Scanner v1.0.0

Paper-trading scanner only. NEVER places real exchange orders.

- M5 closed candles only.
- No M1 confirmation and no READY stage.
- Direct entry after a complete six-pivot M5 structure.
- TP and SL are calculated from M5 structure and remain fixed.
- Unlimited open paper trades.
- Existing SQLite trade history is preserved.
- Legacy M15 open trades continue to be managed.
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


# ---------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------

VERSION = "1.0.0"

FUTURES_REST = "https://futures.kraken.com/derivatives/api/v3"
CHARTS_REST = "https://futures.kraken.com/api/charts/v1"
TELEGRAM_API = "https://api.telegram.org"

UNIVERSE_SIZE = max(2, int(os.getenv("UNIVERSE_SIZE", "100")))
MAX_SIGNAL_AGE_HOURS = max(1, int(os.getenv("MAX_SIGNAL_AGE_HOURS", "72")))
STOP_BUFFER_PCT = max(0.0, float(os.getenv("STOP_BUFFER_PCT", "0.10")))

M5_CANDLE_COUNT = 1500
M15_CANDLE_COUNT = 1500

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

M5_MS = 5 * 60 * 1000
M15_MS = 15 * 60 * 1000

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": f"NDS-M5-Structure-Scanner/{VERSION}",
    "Accept": "application/json",
})

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)

LOG = logging.getLogger("nds_m5_long_short")


# ---------------------------------------------------------------------
# GENERAL HELPERS
# ---------------------------------------------------------------------

def as_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None

        result = float(value)

        if not math.isfinite(result):
            return None

        return result

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

    if signed:
        return f"{value:+.2f}%"

    return f"{value:.2f}%"


def dt_text(epoch_seconds: float) -> str:
    return (
        datetime.fromtimestamp(
            epoch_seconds,
            tz=timezone.utc
        )
        .astimezone(REPORT_TZ)
        .strftime("%Y-%m-%d %H:%M:%S %Z")
    )


def request_json(
    url: str,
    params: Optional[dict[str, Any]] = None,
    retries: int = 3,
) -> dict[str, Any]:

    last_exc = None

    for attempt in range(retries):
        try:
            response = HTTP.get(
                url,
                params=params,
                timeout=(8, 25),
            )

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


# ---------------------------------------------------------------------
# KRAKEN FUTURES
# ---------------------------------------------------------------------

def get_tickers() -> list[dict[str, Any]]:

    payload = request_json(f"{FUTURES_REST}/tickers")

    tickers = payload.get("tickers")

    if not isinstance(tickers, list):
        raise RuntimeError("Kraken response has no ticker list")

    return [
        item for item in tickers
        if isinstance(item, dict)
    ]


def extract_volume(ticker: dict[str, Any]) -> float:

    keys = (
        "vol24h",
        "volume24h",
        "volume",
        "volume24H",
        "turnover24h",
        "usdVolume24h",
    )

    for key in keys:
        value = as_float(ticker.get(key))

        if value is not None and value >= 0:
            return value

    return 0.0


def get_universe(
    tickers: list[dict[str, Any]]
) -> list[str]:

    rows = []

    for ticker in tickers:

        symbol = str(
            ticker.get("symbol", "")
        ).upper()

        if symbol.startswith("PF_") and symbol.endswith("USD"):
            rows.append((symbol, extract_volume(ticker)))

    rows.sort(
        key=lambda row: (row[1], row[0]),
        reverse=True,
    )

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
        for item in tickers
        if item.get("symbol")
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
        bid is not None
        and ask is not None
        and bid > 0
        and ask >= bid
    ):
        return (bid + ask) / 2.0

    for key in (
        "markPrice",
        "mark_price",
        "last",
        "lastPrice",
        "lastTradePrice",
        "indexPrice",
        "index_price",
    ):

        value = as_float(ticker.get(key))

        if value is not None and value > 0:
            return value

    return None


# ---------------------------------------------------------------------
# CANDLE DATA
# ---------------------------------------------------------------------

def get_candles(
    symbol: str,
    resolution: str,
    limit: int,
) -> list[dict[str, Any]]:

    timeframe_ms = {
        "5m": M5_MS,
        "15m": M15_MS,
    }.get(resolution)

    if timeframe_ms is None:
        raise ValueError(f"Unsupported resolution: {resolution}")

    now = time.time()

    params = {
        "from": int(
            now - (timeframe_ms / 1000) * (limit + 5)
        ),
        "to": int(now),
        "count": limit,
    }

    payload = request_json(
        f"{CHARTS_REST}/trade/{symbol}/{resolution}",
        params=params,
    )

    rows = payload.get("candles", [])

    if not isinstance(rows, list):
        raise RuntimeError(
            f"Invalid candles response for {symbol}"
        )

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

        # Only closed candles are allowed.
        if ts_i + timeframe_ms > now_ms:
            continue

        if low > high:
            continue

        if min(opn, high, low, close) <= 0:
            continue

        by_time[ts_i] = {
            "time": ts_i,
            "open": float(opn),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": as_float(row.get("volume")) or 0.0,
        }

    return [
        by_time[key]
        for key in sorted(by_time)
    ][-limit:]


def scan_symbol(
    symbol: str
) -> tuple[str, Optional[list[dict[str, Any]]], Optional[str]]:

    try:
        return (
            symbol,
            get_candles(symbol, "5m", M5_CANDLE_COUNT),
            None,
        )

    except Exception as exc:
        return symbol, None, str(exc)


# ---------------------------------------------------------------------
# HEIKIN-ASHI
# ---------------------------------------------------------------------

def heiken_ashi_candles(
    candles: list[dict[str, Any]]
) -> list[dict[str, float]]:

    result = []

    previous_open = None
    previous_close = None

    for candle in candles:

        ha_close = (
            float(candle["open"])
            + float(candle["high"])
            + float(candle["low"])
            + float(candle["close"])
        ) / 4.0

        if previous_open is None:

            ha_open = (
                float(candle["open"])
                + float(candle["close"])
            ) / 2.0

        else:

            ha_open = (
                previous_open + previous_close
            ) / 2.0

        result.append({
            "open": ha_open,
            "close": ha_close,
            "high": max(
                float(candle["high"]),
                ha_open,
                ha_close,
            ),
            "low": min(
                float(candle["low"]),
                ha_open,
                ha_close,
            ),
        })

        previous_open = ha_open
        previous_close = ha_close

    return result


def heiken_ashi_colors_from_values(
    ha: list[dict[str, float]]
) -> list[int]:

    colors = []

    for candle in ha:

        if candle["close"] > candle["open"]:
            colors.append(1)

        elif candle["close"] < candle["open"]:
            colors.append(-1)

        else:
            colors.append(0)

    return colors


def find_confirmed_pivot_records(
    candles: list[dict[str, Any]]
) -> list[dict[str, Any]]:

    """
    HA reversal-run pivots.

    Green -> Red:
        Confirm the highest HA high of the completed green run.

    Red -> Green:
        Confirm the lowest HA low of the completed red run.

    No fixed left/right candle count is used.
    """

    if len(candles) < 3:
        return []

    ha = heiken_ashi_candles(candles)

    colors = heiken_ashi_colors_from_values(ha)

    records = []

    active_direction = 0
    run_indices = []

    for i, color in enumerate(colors):

        if color == 0:

            if active_direction != 0:
                run_indices.append(i)

            continue

        if active_direction == 0:

            active_direction = color
            run_indices = [i]

            continue

        if color == active_direction:

            run_indices.append(i)

            continue

        if run_indices:

            if active_direction == 1 and color == -1:

                pivot_i = max(
                    run_indices,
                    key=lambda k: ha[k]["high"],
                )

                records.append({
                    "index": pivot_i,
                    "kind": "H",
                    "price": float(ha[pivot_i]["high"]),
                    "confirmed_index": i,
                })

            elif active_direction == -1 and color == 1:

                pivot_i = min(
                    run_indices,
                    key=lambda k: ha[k]["low"],
                )

                records.append({
                    "index": pivot_i,
                    "kind": "L",
                    "price": float(ha[pivot_i]["low"]),
                    "confirmed_index": i,
                })

        active_direction = color
        run_indices = [i]

    return records


def same_price(a: float, b: float) -> bool:

    return math.isclose(
        a,
        b,
        rel_tol=1e-10,
        abs_tol=1e-12,
    )


# ---------------------------------------------------------------------
# STRUCTURE DETECTION
# ---------------------------------------------------------------------

def pattern_key(
    symbol: str,
    candles: list[dict[str, Any]],
    points: dict[str, int],
    direction: str,
) -> str:

    timestamps = [
        str(candles[points[name]]["time"])
        for name in (
            "start", "p1", "p2", "p3", "p4", "p5"
        )
    ]

    return (
        f"{direction}|{symbol}|M5|"
        + "|".join(timestamps)
    )


def detect_patterns(
    symbol: str,
    candles: list[dict[str, Any]],
    direction: str,
) -> list[dict[str, Any]]:

    """
    SHORT:
        START LOW
        1 GREEN HIGH
        1 RED LOW
        2 GREEN HIGH
        2 RED LOW
        3 GREEN HIGH

    LONG:
        START HIGH
        1 RED LOW
        1 GREEN HIGH
        2 RED LOW
        2 GREEN HIGH
        3 RED LOW

    All pivot prices and structure comparisons use HA values.
    """

    if len(candles) < 10:
        return []

    ha = heiken_ashi_candles(candles)

    records = find_confirmed_pivot_records(candles)

    if len(records) < 6:
        return []

    wanted = (
        ["L", "H", "L", "H", "L", "H"]
        if direction == "SHORT"
        else ["H", "L", "H", "L", "H", "L"]
    )

    patterns = []

    for j in range(len(records) - 5):

        seq = records[j:j + 6]

        if [p["kind"] for p in seq] != wanted:
            continue

        idx = [
            int(p["index"])
            for p in seq
        ]

        (
            start_i,
            p1_i,
            p2_i,
            p3_i,
            p4_i,
            p5_i,
        ) = idx

        prices = [
            float(p["price"])
            for p in seq
        ]

        (
            start_price,
            p1,
            p2,
            p3,
            p4,
            p5,
        ) = prices

        if direction == "SHORT":

            valid = (
                p1 > start_price
                and p2 > start_price
                and p3 > p1
                and p4 > start_price
                and p4 < p2
                and p5 > p1
                and p5 > p3
            )

            valid = valid and same_price(
                p1,
                max(
                    c["high"]
                    for c in ha[start_i:p2_i + 1]
                ),
            )

            valid = valid and same_price(
                p2,
                min(
                    c["low"]
                    for c in ha[p1_i:p3_i + 1]
                ),
            )

            valid = valid and same_price(
                p3,
                max(
                    c["high"]
                    for c in ha[p2_i:p4_i + 1]
                ),
            )

            valid = valid and same_price(
                p4,
                min(
                    c["low"]
                    for c in ha[p3_i:p5_i + 1]
                ),
            )

            valid = valid and same_price(
                start_price,
                min(
                    c["low"]
                    for c in ha[start_i:p5_i + 1]
                ),
            )

        else:

            valid = (
                p1 < start_price
                and p2 < start_price
                and p3 < p1
                and p4 < start_price
                and p4 > p2
                and p5 < p1
                and p5 < p3
            )

            valid = valid and same_price(
                p1,
                min(
                    c["low"]
                    for c in ha[start_i:p2_i + 1]
                ),
            )

            valid = valid and same_price(
                p2,
                max(
                    c["high"]
                    for c in ha[p1_i:p3_i + 1]
                ),
            )

            valid = valid and same_price(
                p3,
                min(
                    c["low"]
                    for c in ha[p2_i:p4_i + 1]
                ),
            )

            valid = valid and same_price(
                p4,
                max(
                    c["high"]
                    for c in ha[p3_i:p5_i + 1]
                ),
            )

            valid = valid and same_price(
                start_price,
                max(
                    c["high"]
                    for c in ha[start_i:p5_i + 1]
                ),
            )

        if not valid:
            continue

        confirmation_index = int(
            seq[-1]["confirmed_index"]
        )

        if confirmation_index >= len(candles):
            continue

        points = {
            "start": start_i,
            "p1": p1_i,
            "p2": p2_i,
            "p3": p3_i,
            "p4": p4_i,
            "p5": p5_i,
        }

        pivot_prices = {
            "start": start_price,
            "p1": p1,
            "p2": p2,
            "p3": p3,
            "p4": p4,
            "p5": p5,
        }

        patterns.append({
            "symbol": symbol,
            "direction": direction,
            "timeframe": "M5",
            "points": points,
            "pivot_prices": pivot_prices,
            "key": pattern_key(
                symbol,
                candles,
                points,
                direction,
            ),
            "confirmation_time": (
                float(candles[confirmation_index]["time"])
                + M5_MS
            ) / 1000.0,
            "start_price": start_price,
            "p1_price": p1,
            "p2_price": p2,
            "p3_price": p3,
            "p4_price": p4,
            "p5_price": p5,
            "terminal_time": (
                float(candles[p5_i]["time"]) / 1000.0
            ),
        })

    patterns.sort(
        key=lambda item: item["confirmation_time"],
        reverse=True,
    )

    return patterns


# ---------------------------------------------------------------------
# M5 STOP-LOSS STRUCTURE
# ---------------------------------------------------------------------

def find_nearest_valid_m5_low(
    candles: list[dict[str, Any]],
    entry: float,
) -> Optional[float]:

    ha = heiken_ashi_candles(candles)

    records = find_confirmed_pivot_records(candles)

    eligible = []

    for pivot in records:

        if pivot["kind"] != "L":
            continue

        i = int(pivot["index"])
        price = float(pivot["price"])

        if price >= entry:
            continue

        # A swing low is invalid once a later HA low reaches it.
        broken = any(
            float(bar["low"]) <= price
            for bar in ha[i + 1:]
        )

        if not broken:
            eligible.append((price, i))

    if not eligible:
        return None

    return max(
        eligible,
        key=lambda item: item[0],
    )[0]


def find_nearest_valid_m5_high(
    candles: list[dict[str, Any]],
    entry: float,
) -> Optional[float]:

    ha = heiken_ashi_candles(candles)

    records = find_confirmed_pivot_records(candles)

    eligible = []

    for pivot in records:

        if pivot["kind"] != "H":
            continue

        i = int(pivot["index"])
        price = float(pivot["price"])

        if price <= entry:
            continue

        broken = any(
            float(bar["high"]) >= price
            for bar in ha[i + 1:]
        )

        if not broken:
            eligible.append((price, i))

    if not eligible:
        return None

    return min(
        eligible,
        key=lambda item: item[0],
    )[0]


# ---------------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------------

def connect_db() -> sqlite3.Connection:

    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )

    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
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
            status TEXT NOT NULL DEFAULT 'OPEN',
            exit_time REAL,
            exit_price REAL,
            pnl_pct REAL,
            created_at REAL NOT NULL,
            FOREIGN KEY(pattern_key)
                REFERENCES patterns(pattern_key)
        );

        CREATE INDEX IF NOT EXISTS idx_trades_status
        ON trades(status);

        CREATE INDEX IF NOT EXISTS idx_trades_symbol_status
        ON trades(symbol, status);

        CREATE TABLE IF NOT EXISTS ready_setups (
            symbol TEXT PRIMARY KEY,
            direction TEXT NOT NULL,
            pattern_key TEXT NOT NULL UNIQUE,
            pattern_json TEXT NOT NULL,
            tp_price REAL NOT NULL,
            sl_price REAL NOT NULL,
            created_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'WAITING_M1',
            updated_at REAL NOT NULL
        );
        """
    )

    cols = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(trades)"
        )
    }

    if "direction" not in cols:
        conn.execute(
            "ALTER TABLE trades ADD COLUMN "
            "direction TEXT NOT NULL DEFAULT 'LONG'"
        )

    conn.commit()

    return conn


def pattern_seen(
    conn: sqlite3.Connection,
    key: str,
) -> bool:

    row = conn.execute(
        """
        SELECT 1
        FROM patterns p
        LEFT JOIN trades t
            ON t.pattern_key = p.pattern_key
        WHERE p.pattern_key = ?
          AND (
              p.decision = 'TRADE'
              OR t.pattern_key IS NOT NULL
          )
        LIMIT 1
        """,
        (key,),
    ).fetchone()

    return row is not None


def save_pattern_decision(
    conn: sqlite3.Connection,
    pattern: dict[str, Any],
    decision: str,
    reason: str = "",
) -> None:

    conn.execute(
        """
        INSERT INTO patterns(
            pattern_key,
            symbol,
            detected_at,
            decision,
            reason
        )
        VALUES(?,?,?,?,?)

        ON CONFLICT(pattern_key)
        DO UPDATE SET
            decision = excluded.decision,
            reason = excluded.reason
        WHERE patterns.decision != 'TRADE'
        """,
        (
            pattern["key"],
            pattern["symbol"],
            time.time(),
            decision,
            reason[:500],
        ),
    )

    conn.commit()


def open_trades(
    conn: sqlite3.Connection,
) -> list[sqlite3.Row]:

    return list(
        conn.execute(
            """
            SELECT *
            FROM trades
            WHERE status = 'OPEN'
            ORDER BY entry_time ASC
            """
        )
    )


def trade_candle_settings(
    trade: sqlite3.Row,
) -> tuple[str, int, str]:

    """
    New trades use M5.
    Legacy M15 trades continue to use M15.
    """

    parts = str(
        trade["pattern_key"]
    ).split("|")

    if len(parts) == 9 and parts[2] == "M5":
        return "5m", M5_MS, "M5"

    return "15m", M15_MS, "M15"


def insert_trade(
    conn: sqlite3.Connection,
    pattern: dict[str, Any],
    entry_time: float,
    entry_price: float,
    tp: float,
    sl: float,
) -> None:

    entry_ms = int(entry_time * 1000)

    next_bar_time_ms = (
        (entry_ms // M5_MS) + 1
    ) * M5_MS

    conn.execute(
        """
        INSERT INTO trades(
            pattern_key,
            symbol,
            direction,
            entry_time,
            next_bar_time_ms,
            entry_price,
            tp_price,
            sl_price,
            status,
            created_at
        )
        VALUES(?,?,?,?,?,?,?,?, 'OPEN', ?)
        """,
        (
            pattern["key"],
            pattern["symbol"],
            pattern["direction"],
            entry_time,
            next_bar_time_ms,
            entry_price,
            tp,
            sl,
            time.time(),
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
        """
        SELECT entry_price, direction
        FROM trades
        WHERE id = ?
        """,
        (trade_id,),
    ).fetchone()

    if not row:
        return

    entry = float(row["entry_price"])
    direction = str(row["direction"])

    pnl = (
        (
            exit_price / entry - 1.0
        )
        if direction == "LONG"
        else (
            entry / exit_price - 1.0
        )
    ) * 100.0

    conn.execute(
        """
        UPDATE trades
        SET status = ?,
            exit_time = ?,
            exit_price = ?,
            pnl_pct = ?
        WHERE id = ?
          AND status = 'OPEN'
        """,
        (
            status,
            exit_time,
            exit_price,
            pnl,
            trade_id,
        ),
    )

    conn.commit()


def trade_pnl_percent(
    entry: float,
    price: Optional[float],
    direction: str = "LONG",
) -> Optional[float]:

    if (
        price is None
        or entry <= 0
        or price <= 0
    ):
        return None

    if direction == "LONG":
        return (price / entry - 1.0) * 100.0

    return (entry / price - 1.0) * 100.0


def pct_from_entry(
    entry: float,
    level: float,
    direction: str = "LONG",
) -> float:

    if direction == "LONG":
        return (level / entry - 1.0) * 100.0

    return (entry / level - 1.0) * 100.0


# ---------------------------------------------------------------------
# CHARTS
# ---------------------------------------------------------------------

def create_signal_chart(
    symbol: str,
    candles: list[dict[str, Any]],
    pattern: dict[str, Any],
    entry: Optional[float],
    tp: Optional[float],
    sl: Optional[float],
    current_price: Optional[float] = None,
    chart_title: str = "NEW SIGNAL",
) -> Path:

    CHART_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    points = pattern["points"]
    direction = pattern["direction"]

    left_edge = max(
        0,
        min(
            min(points.values()) - 12,
            len(candles) - 175,
        ),
    )

    chart_candles = candles[left_edge:]

    if len(chart_candles) < 10:
        chart_candles = candles[-100:]
        left_edge = len(candles) - len(chart_candles)

    ha_all = heiken_ashi_candles(candles)

    fig, ax = plt.subplots(
        figsize=(13, 7),
        dpi=135,
    )

    for x, _source in enumerate(chart_candles):

        c = ha_all[left_edge + x]

        ha_direction = (
            1 if c["close"] > c["open"]
            else -1 if c["close"] < c["open"]
            else 0
        )

        color = (
            "#18a558"
            if ha_direction >= 0
            else "#e74c3c"
        )

        ax.vlines(
            x,
            c["low"],
            c["high"],
            color=color,
            linewidth=1.1,
            zorder=2,
        )

        body_low = min(
            c["open"],
            c["close"],
        )

        body_height = max(
            abs(c["close"] - c["open"]),
            abs(c["close"]) * 0.000002,
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

    pivot_prices = (
        pattern.get("pivot_prices") or {}
    )

    if direction == "LONG":

        style = {
            "start": (
                "START", "#277da1", "o",
                pivot_prices.get(
                    "start",
                    candles[points["start"]]["high"],
                ),
            ),
            "p1": (
                "1 RED", "#db2b39", "v",
                pivot_prices.get(
                    "p1",
                    candles[points["p1"]]["low"],
                ),
            ),
            "p2": (
                "1 GREEN", "#149447", "^",
                pivot_prices.get(
                    "p2",
                    candles[points["p2"]]["high"],
                ),
            ),
            "p3": (
                "2 RED", "#db2b39", "v",
                pivot_prices.get(
                    "p3",
                    candles[points["p3"]]["low"],
                ),
            ),
            "p4": (
                "2 GREEN / TP", "#149447", "^",
                pivot_prices.get(
                    "p4",
                    candles[points["p4"]]["high"],
                ),
            ),
            "p5": (
                "3 RED / TRIGGER", "#db2b39", "v",
                pivot_prices.get(
                    "p5",
                    candles[points["p5"]]["low"],
                ),
            ),
        }

    else:

        style = {
            "start": (
                "START", "#277da1", "o",
                pivot_prices.get(
                    "start",
                    candles[points["start"]]["low"],
                ),
            ),
            "p1": (
                "1 GREEN", "#149447", "^",
                pivot_prices.get(
                    "p1",
                    candles[points["p1"]]["high"],
                ),
            ),
            "p2": (
                "1 RED", "#db2b39", "v",
                pivot_prices.get(
                    "p2",
                    candles[points["p2"]]["low"],
                ),
            ),
            "p3": (
                "2 GREEN", "#149447", "^",
                pivot_prices.get(
                    "p3",
                    candles[points["p3"]]["high"],
                ),
            ),
            "p4": (
                "2 RED / TP", "#db2b39", "v",
                pivot_prices.get(
                    "p4",
                    candles[points["p4"]]["low"],
                ),
            ),
            "p5": (
                "3 GREEN / TRIGGER", "#149447", "^",
                pivot_prices.get(
                    "p5",
                    candles[points["p5"]]["high"],
                ),
            ),
        }

    for name, item in style.items():

        label, color, marker, y = item

        x = points[name] - left_edge

        if not 0 <= x < len(chart_candles):
            continue

        ax.scatter(
            [x],
            [y],
            s=75,
            marker=marker,
            color=color,
            edgecolors="white",
            linewidths=0.7,
            zorder=5,
        )

        offset = (
            9 if marker in ("^", "o")
            else -14
        )

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

    last_x = len(chart_candles) - 1

    if tp is not None:
        ax.axhline(
            tp,
            color="#149447",
            linestyle="--",
            linewidth=1.25,
            label=f"TP {fmt_price(tp)}",
        )

    if sl is not None:
        ax.axhline(
            sl,
            color="#db2b39",
            linestyle="--",
            linewidth=1.25,
            label=f"SL {fmt_price(sl)}",
        )

    if entry is not None:

        ax.axhline(
            entry,
            color="#277da1",
            linestyle=":",
            linewidth=1.5,
            label=f"ENTRY {fmt_price(entry)}",
        )

        ax.scatter(
            [last_x],
            [entry],
            color="#277da1",
            marker="D",
            s=68,
            edgecolors="white",
            linewidths=0.7,
            zorder=6,
        )

        ax.annotate(
            "ENTRY",
            (last_x, entry),
            xytext=(-8, 8),
            textcoords="offset points",
            ha="right",
            fontsize=8,
            color="#277da1",
            fontweight="bold",
        )

    if current_price is not None and current_price > 0:

        ax.axhline(
            current_price,
            color="#8e44ad",
            linestyle="-.",
            linewidth=1.35,
            label=f"NOW {fmt_price(current_price)}",
        )

        ax.scatter(
            [last_x],
            [current_price],
            color="#8e44ad",
            marker="o",
            s=55,
            edgecolors="white",
            linewidths=0.7,
            zorder=7,
        )

    n = min(8, len(chart_candles))

    ticks = sorted(set(
        round(
            i * (len(chart_candles) - 1)
            / max(1, n - 1)
        )
        for i in range(n)
    ))

    ax.set_xticks(ticks)

    ax.set_xticklabels([
        datetime.fromtimestamp(
            chart_candles[i]["time"] / 1000,
            timezone.utc,
        )
        .astimezone(REPORT_TZ)
        .strftime("%m-%d %H:%M")
        for i in ticks
    ], rotation=25, ha="right", fontsize=8)

    ax.set_title(
        f"NDS M5 {direction} | {symbol} | {chart_title}\n"
        f"M5 Heikin-Ashi candles | HA reversal-run pivots | "
        f"{dt_text(pattern['confirmation_time'])}",
        fontsize=12,
        fontweight="bold",
    )

    ax.set_ylabel("Price")
    ax.grid(True, alpha=0.2)
    ax.legend(
        loc="best",
        fontsize=8,
        framealpha=0.9,
    )

    ax.margins(x=0.03, y=0.1)

    fig.tight_layout()

    safe = "".join(
        ch if ch.isalnum() or ch in "-_" else "_"
        for ch in symbol
    )

    path = CHART_DIR / (
        f"{safe}_{direction}_{int(time.time())}.png"
    )

    fig.savefig(
        path,
        bbox_inches="tight",
    )

    plt.close(fig)

    return path


def restore_trade_pattern(
    trade: sqlite3.Row,
    candles: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:

    parts = str(
        trade["pattern_key"]
    ).split("|")

    if (
        len(parts) != 9
        or parts[0] not in ("LONG", "SHORT")
    ):
        LOG.warning(
            "Cannot restore structure for trade %s",
            trade["id"],
        )
        return None

    try:
        timestamps = [
            int(value)
            for value in parts[3:9]
        ]

    except (TypeError, ValueError):
        return None

    index_by_time = {
        int(candle["time"]): i
        for i, candle in enumerate(candles)
    }

    if not all(
        ts in index_by_time
        for ts in timestamps
    ):
        return None

    points = dict(zip(
        (
            "start",
            "p1",
            "p2",
            "p3",
            "p4",
            "p5",
        ),
        [
            index_by_time[ts]
            for ts in timestamps
        ],
    ))

    direction = str(trade["direction"])

    ha = heiken_ashi_candles(candles)

    pivot_prices = {
        "start": float(
            ha[points["start"]]["low"]
            if direction == "SHORT"
            else ha[points["start"]]["high"]
        ),
        "p1": float(
            ha[points["p1"]]["high"]
            if direction == "SHORT"
            else ha[points["p1"]]["low"]
        ),
        "p2": float(
            ha[points["p2"]]["low"]
            if direction == "SHORT"
            else ha[points["p2"]]["high"]
        ),
        "p3": float(
            ha[points["p3"]]["high"]
            if direction == "SHORT"
            else ha[points["p3"]]["low"]
        ),
        "p4": float(
            ha[points["p4"]]["low"]
            if direction == "SHORT"
            else ha[points["p4"]]["high"]
        ),
        "p5": float(
            ha[points["p5"]]["high"]
            if direction == "SHORT"
            else ha[points["p5"]]["low"]
        ),
    }

    records = find_confirmed_pivot_records(candles)

    confirmation_index = next(
        (
            int(p["confirmed_index"])
            for p in records
            if int(p["index"]) == points["p5"]
        ),
        points["p5"] + 1,
    )

    _resolution, interval_ms, timeframe_label = (
        trade_candle_settings(trade)
    )

    confirmation_index = min(
        confirmation_index,
        len(candles) - 1,
    )

    return {
        "symbol": str(trade["symbol"]),
        "direction": direction,
        "points": points,
        "pivot_prices": pivot_prices,
        "timeframe": timeframe_label,
        "confirmation_time": (
            float(candles[confirmation_index]["time"])
            + interval_ms
        ) / 1000.0,
    }


# ---------------------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------------------

def telegram_credentials() -> tuple[
    Optional[str],
    Optional[str],
]:

    token = (
        os.getenv("TELEGRAM_BOT_TOKEN")
        or os.getenv("TELEGRAM_TOKEN")
    )

    chat_id = (
        os.getenv("TELEGRAM_CHAT_ID")
        or os.getenv("TELEGRAM_CHAT")
    )

    return token, chat_id


def telegram_send_message(message: str) -> bool:

    token, chat_id = telegram_credentials()

    if not token or not chat_id:
        LOG.warning(
            "Telegram credentials missing"
        )
        return False

    url = (
        f"{TELEGRAM_API}/bot{token}/sendMessage"
    )

    chunks = []
    remaining = message

    while len(remaining) > 3800:

        split_at = remaining.rfind(
            "\n",
            0,
            3800,
        )

        if split_at < 1000:
            split_at = 3800

        chunks.append(
            remaining[:split_at]
        )

        remaining = remaining[split_at:].lstrip("\n")

    if remaining:
        chunks.append(remaining)

    ok = True

    for chunk in chunks:

        try:
            response = HTTP.post(
                url,
                data={
                    "chat_id": chat_id,
                    "text": chunk,
                },
                timeout=(8, 25),
            )

            if (
                response.status_code >= 400
                or not response.json().get("ok", False)
            ):
                LOG.error(
                    "Telegram message failed: %s",
                    response.text[:300],
                )
                ok = False

        except Exception:
            LOG.exception(
                "Telegram sendMessage error"
            )
            ok = False

    return ok


def telegram_send_photo(
    path: Path,
    caption: str,
) -> bool:

    token, chat_id = telegram_credentials()

    if not token or not chat_id:
        LOG.warning(
            "Telegram credentials missing"
        )
        return False

    url = (
        f"{TELEGRAM_API}/bot{token}/sendPhoto"
    )

    try:

        with path.open("rb") as photo_file:

            response = HTTP.post(
                url,
                data={
                    "chat_id": chat_id,
                    "caption": caption[:1000],
                },
                files={
                    "photo": (
                        path.name,
                        photo_file,
                        "image/png",
                    )
                },
                timeout=(10, 45),
            )

        if (
            response.status_code >= 400
            or not response.json().get("ok", False)
        ):
            LOG.error(
                "Telegram sendPhoto failed: %s",
                response.text[:300],
            )
            return False

        return True

    except Exception:
        LOG.exception(
            "Telegram sendPhoto error"
        )
        return False


# ---------------------------------------------------------------------
# TRADE MANAGEMENT
# ---------------------------------------------------------------------

def update_open_trades(
    conn: sqlite3.Connection,
    candles_by_symbol: dict[
        str,
        list[dict[str, Any]]
    ],
    live_prices: Optional[
        dict[str, Optional[float]]
    ] = None,
) -> list[dict[str, Any]]:

    closed = []

    for trade in open_trades(conn):

        symbol = str(trade["symbol"])

        resolution, interval_ms, timeframe_label = (
            trade_candle_settings(trade)
        )

        bars = candles_by_symbol.get(symbol)

        if timeframe_label == "M15":
            bars = None

        if bars is None:

            try:
                bars = get_candles(
                    symbol,
                    resolution,
                    M5_CANDLE_COUNT,
                )

                if timeframe_label == "M5":
                    candles_by_symbol[symbol] = bars

            except Exception as exc:

                LOG.warning(
                    "Cannot refresh open trade %s: %s",
                    trade["id"],
                    exc,
                )

                continue

        entry_ms = int(
            trade["next_bar_time_ms"]
        )

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

            # Conservative rule: SL wins if both are hit
            # in the same candle.
            if hit_sl:

                status = "CLOSED_SL"
                exit_price = sl

            else:

                status = "CLOSED_TP"
                exit_price = tp

            exit_time = (
                bar["time"] + interval_ms
            ) / 1000.0

            close_trade(
                conn,
                int(trade["id"]),
                status,
                exit_time,
                exit_price,
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

        else:

            live_price = (
                live_prices or {}
            ).get(symbol)

            if live_price is None or live_price <= 0:
                continue

            hit_sl_live = (
                live_price <= sl
                if direction == "LONG"
                else live_price >= sl
            )

            hit_tp_live = (
                live_price >= tp
                if direction == "LONG"
                else live_price <= tp
            )

            if not hit_sl_live and not hit_tp_live:
                continue

            if hit_sl_live:

                status = "CLOSED_SL"
                exit_price = sl

            else:

                status = "CLOSED_TP"
                exit_price = tp

            exit_time = time.time()

            close_trade(
                conn,
                int(trade["id"]),
                status,
                exit_time,
                exit_price,
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

    return closed


def get_performance(
    conn: sqlite3.Connection,
    prices: dict[str, Optional[float]],
) -> dict[str, Any]:

    closed_rows = list(
        conn.execute(
            """
            SELECT status, pnl_pct
            FROM trades
            WHERE status IN ('CLOSED_TP', 'CLOSED_SL')
            """
        )
    )

    open_rows = open_trades(conn)

    wins = sum(
        1
        for row in closed_rows
        if (
            row["status"] == "CLOSED_TP"
            and (row["pnl_pct"] or 0) > 0
        )
    )

    losses = len(closed_rows) - wins

    win_rate = (
        100.0 * wins / len(closed_rows)
        if closed_rows
        else 0.0
    )

    realized = sum(
        float(row["pnl_pct"] or 0.0)
        for row in closed_rows
    )

    floating = 0.0

    for row in open_rows:

        price = prices.get(
            str(row["symbol"])
        )

        pnl = trade_pnl_percent(
            float(row["entry_price"]),
            price,
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


# ---------------------------------------------------------------------
# OPEN TRADE CHARTS
# ---------------------------------------------------------------------

def send_open_trade_charts(
    conn: sqlite3.Connection,
    candles_by_symbol: dict[
        str,
        list[dict[str, Any]]
    ],
    prices: dict[str, Optional[float]],
) -> tuple[int, int]:

    sent = 0
    attempted = 0

    for trade in open_trades(conn):

        symbol = str(trade["symbol"])

        resolution, _interval_ms, timeframe_label = (
            trade_candle_settings(trade)
        )

        bars = candles_by_symbol.get(symbol)

        if timeframe_label == "M15":
            bars = None

        if not bars:

            try:
                bars = get_candles(
                    symbol,
                    resolution,
                    M5_CANDLE_COUNT,
                )

                if timeframe_label == "M5":
                    candles_by_symbol[symbol] = bars

            except Exception as exc:

                LOG.warning(
                    "Cannot fetch chart candles for trade %s: %s",
                    trade["id"],
                    exc,
                )

                continue

        if len(bars) < 10:
            continue

        pattern = restore_trade_pattern(
            trade,
            bars,
        )

        if pattern is None:
            continue

        current = prices.get(symbol)

        if current is None or current <= 0:

            current = float(
                bars[-1]["close"]
            )

            prices[symbol] = current

        entry = float(trade["entry_price"])
        tp = float(trade["tp_price"])
        sl = float(trade["sl_price"])
        direction = str(trade["direction"])

        floating = trade_pnl_percent(
            entry,
            current,
            direction,
        )

        try:

            chart_path = create_signal_chart(
                symbol,
                bars,
                pattern,
                entry,
                tp,
                sl,
                current_price=current,
                chart_title="OPEN TRADE UPDATE",
            )

            emoji = (
                "🟢"
                if direction == "LONG"
                else "🔴"
            )

            caption = (
                f"{emoji} OPEN {direction} | {symbol} "
                f"| Trade #{trade['id']}\n"
                f"Entry: {fmt_price(entry)}\n"
                f"Current: {fmt_price(current)} "
                f"| Floating PnL: {fmt_pct(floating)}\n"
                f"TP: {fmt_price(tp)} "
                f"({fmt_pct(pct_from_entry(entry, tp, direction))})\n"
                f"SL: {fmt_price(sl)} "
                f"({fmt_pct(pct_from_entry(entry, sl, direction))})\n"
                f"{timeframe_label} Heikin-Ashi chart: "
                "START, pivots 1/2, trigger, ENTRY, TP, SL and current price"
            )

            attempted += 1

            if telegram_send_photo(
                chart_path,
                caption,
            ):
                sent += 1

        except Exception:

            LOG.exception(
                "Open-trade chart failed: %s",
                symbol,
            )

    return sent, attempted


# ---------------------------------------------------------------------
# REPORT
# ---------------------------------------------------------------------

def make_report(
    scan_stats: dict[str, Any],
    new_signals: list[dict[str, Any]],
    closed_trades: list[dict[str, Any]],
    perf: dict[str, Any],
    prices: dict[str, Optional[float]],
) -> str:

    lines = [
        f"🔎 NDS M5 LONG + SHORT v{VERSION} | {dt_text(time.time())}",
        (
            f"Scan: {scan_stats['scanned']} symbols "
            f"| M5 OK {scan_stats['m5_ok']} "
            f"| errors {scan_stats['errors']} "
            f"| structures {scan_stats['patterns']}"
        ),
        (
            f"Universe: {scan_stats['universe_size']} "
            f"| Open trades: {perf['open_count']} "
            f"| No maximum limit"
        ),
        "",
    ]

    diagnostics = scan_stats.get(
        "diagnostics",
        {},
    )

    if diagnostics:

        lines.append("🧭 SIGNAL DIAGNOSTICS")

        lines.append(
            f"Detected: {diagnostics.get('detected_total', 0)} "
            f"| Fresh candidates: {diagnostics.get('candidates', 0)} "
            f"| Already traded: {diagnostics.get('already_seen', 0)} "
            f"| Expired: {diagnostics.get('expired', 0)}"
        )

        labels = (
            ("no_price", "No current price"),
            ("bad_tp", "TP on wrong side of entry"),
            ("sl_missing", "No valid HA SL pivot"),
            ("bad_levels", "Invalid SL/entry/TP order"),
            ("opened", "Paper trades opened"),
        )

        for key, label in labels:

            count = diagnostics.get(key, 0)

            if count:
                lines.append(
                    f"• {label}: {count}"
                )

        lines.append("")

    for direction, emoji in (
        ("LONG", "🟢"),
        ("SHORT", "🔴"),
    ):

        items = [
            item
            for item in new_signals
            if item["direction"] == direction
        ]

        lines.append(
            f"{emoji} NEW {direction} SIGNALS: {len(items)}"
        )

        for item in items:

            d = item["direction"]

            lines.append(
                f"• {item['symbol']} "
                f"| Entry {fmt_price(item['entry'])} "
                f"| TP {fmt_price(item['tp'])} "
                f"({fmt_pct(pct_from_entry(item['entry'], item['tp'], d))}) "
                f"| SL {fmt_price(item['sl'])} "
                f"({fmt_pct(pct_from_entry(item['entry'], item['sl'], d))})"
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
                "✅"
                if trade["status"] == "CLOSED_TP"
                else "🛑"
            )

            lines.append(
                f"{emoji} {trade['symbol']} "
                f"{trade['direction']} {trade['status']} "
                f"| Entry {fmt_price(trade['entry'])} "
                f"| Exit {fmt_price(trade['exit'])} "
                f"| PnL {fmt_pct(trade['pnl_pct'])}"
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

        price = prices.get(
            str(row["symbol"])
        )

        floating = trade_pnl_percent(
            entry,
            price,
            direction,
        )

        emoji = (
            "🟢"
            if direction == "LONG"
            else "🔴"
        )

        lines.append(
            f"{emoji} {row['symbol']} {direction} "
            f"| Entry {fmt_price(entry)} "
            f"| Now {fmt_price(price)} ({fmt_pct(floating)}) "
            f"| TP {fmt_price(tp)} "
            f"({fmt_pct(pct_from_entry(entry, tp, direction))}) "
            f"| SL {fmt_price(sl)} "
            f"({fmt_pct(pct_from_entry(entry, sl, direction))})"
        )

    if not perf["open_rows"]:
        lines.append("No open paper trades.")

    lines.extend([
        "",
        "📊 PERFORMANCE",
        (
            f"Closed trades: {perf['closed_count']} "
            f"| W/L: {perf['wins']}/{perf['losses']} "
            f"| Win rate: {perf['win_rate']:.2f}%"
        ),
        f"Realized PnL: {fmt_pct(perf['realized_pct'])}",
        f"Floating PnL: {fmt_pct(perf['floating_pct'])}",
        f"TOTAL PnL: {fmt_pct(perf['total_pct'])}",
        (
            "PnL is the sum of directional equal-notional "
            "trade percentages, gross before fees/slippage."
        ),
        "M5 only for new signals. No M1 confirmation.",
        "Paper/signal mode only. No real orders are placed.",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------

def main() -> int:

    started = time.time()

    conn = connect_db()

    try:

        tickers = get_tickers()

    except Exception as exc:

        message = (
            "❌ NDS M5 scanner startup error: "
            "unable to load Kraken Futures tickers.\n"
            f"{exc}"
        )

        LOG.exception(
            "Ticker fetch failed"
        )

        telegram_send_message(message)

        conn.close()

        return 1

    tickers_by_symbol = ticker_map(tickers)

    universe = get_universe(tickers)

    # Include symbols of existing open trades so they
    # continue to be managed even if they leave the universe.
    open_symbols = {
        str(row["symbol"])
        for row in open_trades(conn)
    }

    symbols_to_scan = sorted(
        set(universe) | open_symbols
    )

    LOG.info(
        "Selected %d symbols; target=%d; open trades=%d",
        len(universe),
        UNIVERSE_SIZE,
        len(open_symbols),
    )

    candles_by_symbol = {}
    errors_by_symbol = {}

    with ThreadPoolExecutor(
        max_workers=MAX_CONCURRENT_REQUESTS
    ) as pool:

        futures = {
            pool.submit(scan_symbol, symbol): symbol
            for symbol in symbols_to_scan
        }

        for future in as_completed(futures):

            symbol, candles, error = future.result()

            if candles is not None:

                candles_by_symbol[symbol] = candles

            elif error:

                errors_by_symbol[symbol] = error

                LOG.warning(
                    "M5 data error %s: %s",
                    symbol,
                    error,
                )

    prices = {}

    for symbol in symbols_to_scan:

        price = current_price_from_ticker(
            tickers_by_symbol.get(symbol)
        )

        bars = candles_by_symbol.get(symbol)

        if price is None and bars:
            price = float(bars[-1]["close"])

        prices[symbol] = price

    # Refresh the existing open paper trades first.
    closed_trades = update_open_trades(
        conn,
        candles_by_symbol,
        prices,
    )

    diagnostics = {
        "detected_total": 0,
        "candidates": 0,
        "already_seen": 0,
        "expired": 0,
        "opened": 0,
        "no_price": 0,
        "bad_tp": 0,
        "sl_missing": 0,
        "bad_levels": 0,
    }

    new_signals = []

    scan_time = time.time()

    # -------------------------------------------------------------
    # Detect and evaluate M5 structures.
    # -------------------------------------------------------------

    for symbol, bars in candles_by_symbol.items():

        if len(bars) < 10:
            continue

        candidates = []

        for direction in ("LONG", "SHORT"):

            found = detect_patterns(
                symbol,
                bars,
                direction,
            )

            diagnostics["detected_total"] += len(found)

            for pattern in found:

                age = (
                    scan_time
                    - float(pattern["confirmation_time"])
                )

                if (
                    0 <= age
                    <= MAX_SIGNAL_AGE_HOURS * 3600
                ):
                    candidates.append(pattern)

                elif age > MAX_SIGNAL_AGE_HOURS * 3600:
                    diagnostics["expired"] += 1

        if not candidates:
            continue

        candidates.sort(
            key=lambda item: item["confirmation_time"],
            reverse=True,
        )

        # Evaluate newest structure first for this symbol.
        pattern = candidates[0]

        if pattern_seen(
            conn,
            pattern["key"],
        ):

            diagnostics["already_seen"] += 1

            # Do not allow an older structure to create a
            # signal when the newest structure was already traded.
            continue

        diagnostics["candidates"] += 1

        entry = prices.get(symbol)

        if entry is None or entry <= 0:

            diagnostics["no_price"] += 1

            save_pattern_decision(
                conn,
                pattern,
                "SKIPPED",
                "No current market price",
            )

            continue

        entry = float(entry)

        # TP is the second main pivot (p4) of the M5 structure.
        tp = float(pattern["p4_price"])

        if (
            pattern["direction"] == "LONG"
            and tp <= entry
        ) or (
            pattern["direction"] == "SHORT"
            and tp >= entry
        ):

            diagnostics["bad_tp"] += 1

            save_pattern_decision(
                conn,
                pattern,
                "SKIPPED",
                "M5 TP is not on the profitable side of entry",
            )

            continue

        # SL is selected from the nearest intact M5 HA swing.
        if pattern["direction"] == "LONG":

            sl = find_nearest_valid_m5_low(
                bars,
                entry,
            )

        else:

            sl = find_nearest_valid_m5_high(
                bars,
                entry,
            )

        if sl is None:

            diagnostics["sl_missing"] += 1

            save_pattern_decision(
                conn,
                pattern,
                "SKIPPED",
                "No valid intact M5 HA swing for stop loss",
            )

            continue

        buffer = STOP_BUFFER_PCT / 100.0

        if pattern["direction"] == "LONG":

            sl = sl * (1.0 - buffer)

            valid_levels = (
                sl < entry < tp
            )

        else:

            sl = sl * (1.0 + buffer)

            valid_levels = (
                tp < entry < sl
            )

        if not valid_levels:

            diagnostics["bad_levels"] += 1

            save_pattern_decision(
                conn,
                pattern,
                "SKIPPED",
                "Invalid M5 SL/entry/TP order",
            )

            continue

        # ---------------------------------------------------------
        # Direct M5 entry. No READY stage. No M1 confirmation.
        # ---------------------------------------------------------

        entry_time = time.time()

        try:

            save_pattern_decision(
                conn,
                pattern,
                "TRADE",
                "Direct entry after complete M5 six-pivot structure",
            )

            insert_trade(
                conn,
                pattern,
                entry_time,
                entry,
                tp,
                float(sl),
            )

        except sqlite3.IntegrityError:

            LOG.info(
                "Duplicate trade ignored: %s",
                pattern["key"],
            )

            continue

        signal = {
            "symbol": symbol,
            "direction": pattern["direction"],
            "entry": entry,
            "tp": tp,
            "sl": float(sl),
            "pattern": pattern,
            "chart": None,
        }

        try:

            signal["chart"] = create_signal_chart(
                symbol,
                bars,
                pattern,
                entry,
                tp,
                float(sl),
                current_price=prices.get(symbol),
                chart_title="NEW M5 SIGNAL",
            )

        except Exception:

            LOG.exception(
                "Signal chart creation failed for %s",
                symbol,
            )

        new_signals.append(signal)

        diagnostics["opened"] += 1

    # -------------------------------------------------------------
    # Update prices for all open trades.
    # -------------------------------------------------------------

    for row in open_trades(conn):

        symbol = str(row["symbol"])

        if prices.get(symbol) is None:

            prices[symbol] = float(
                row["entry_price"]
            )

    perf = get_performance(
        conn,
        prices,
    )

    stats = {
        "scanned": len(universe),
        "m5_ok": len(candles_by_symbol),
        "errors": len(errors_by_symbol),
        "patterns": diagnostics["detected_total"],
        "universe_size": len(universe),
        "diagnostics": diagnostics,
    }

    report = make_report(
        stats,
        new_signals,
        closed_trades,
        perf,
        prices,
    )

    LOG.info(
        "\n%s",
        report,
    )

    telegram_send_message(report)

    # -------------------------------------------------------------
    # Send new signal charts.
    # -------------------------------------------------------------

    sent_signal_charts = 0

    for signal in new_signals:

        chart_path = signal.get("chart")

        if not chart_path:
            continue

        direction = signal["direction"]

        emoji = (
            "🟢"
            if direction == "LONG"
            else "🔴"
        )

        caption = (
            f"{emoji} NEW {direction} SIGNAL | "
            f"{signal['symbol']}\n"
            f"Entry (M5): {fmt_price(signal['entry'])}\n"
            f"TP (M5): {fmt_price(signal['tp'])} "
            f"({fmt_pct(pct_from_entry(signal['entry'], signal['tp'], direction))})\n"
            f"SL (M5): {fmt_price(signal['sl'])} "
            f"({fmt_pct(pct_from_entry(signal['entry'], signal['sl'], direction))})\n"
            "Direct M5 entry after complete six-pivot structure.\n"
            "No M1 confirmation. TP and SL remain fixed."
        )

        if telegram_send_photo(
            chart_path,
            caption,
        ):
            sent_signal_charts += 1

    # -------------------------------------------------------------
    # Send updated charts for every open trade on every run.
    # -------------------------------------------------------------

    open_charts_sent, open_charts_attempted = (
        send_open_trade_charts(
            conn,
            candles_by_symbol,
            prices,
        )
    )

    LOG.info(
        "Finished in %.1fs | signals=%d | closed=%d "
        "| open=%d | signal charts=%d | open charts=%d/%d",
        time.time() - started,
        len(new_signals),
        len(closed_trades),
        len(open_trades(conn)),
        sent_signal_charts,
        open_charts_sent,
        open_charts_attempted,
    )

    conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
