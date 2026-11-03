#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NDS M15 Long + Short Structure Scanner v1.0.0

LONG + SHORT mirrored signal/paper-trade scanner. It never places real exchange orders.

Two-stage setup/entry (closed M15 and M1 candles; pivots use Heikin-Ashi only):
  Pivots are confirmed by HA color reversals. No fixed left/right candle count is used.
  The pivot price and all structure comparisons use HA highs/lows only.
  SHORT: START low -> 1 GREEN -> 1 RED -> 2 GREEN higher high -> 2 RED lower low
         -> 3 GREEN higher high. M15 creates a 72-hour READY setup only.
         A complete same-direction six-pivot M1 structure confirms entry.
         TP and SL are fixed from M15 and are never recalculated at M1.
  LONG:  mirrored structure. M15 creates READY; a complete M1 structure confirms entry.
         Enter LONG at current market price. TP = 2 GREEN; SL = nearest intact
         confirmed HA swing low below entry.
  Every distinct subsequent structure can signal, whether same or opposite direction;
  only the exact same six-pivot structure is deduplicated.

Reporting:
  - Sends a PNG chart for each new signal to Telegram.
  - Reports new signals, open trades with entry/TP/SL and percentage distances,
    closed-trade win rate, realized PnL, floating PnL, and total PnL.
  - PnL is gross, equal-notional percentage-point PnL (fees/slippage excluded).

Environment variables:
  TELEGRAM_BOT_TOKEN or TELEGRAM_TOKEN
  TELEGRAM_CHAT_ID or TELEGRAM_CHAT
  UNIVERSE_SIZE (default 100)
  MAX_SIGNAL_AGE_HOURS (default 72)
  STOP_BUFFER_PCT (default 0.10, percent beyond swing stop)
  DB_PATH (default nds_m5_long_short_structure_v1.db; retained to preserve existing paper-trade history)
  CHART_DIR (default nds_m5_long_short_structure_charts)
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
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
except Exception as exc:  # Let startup explain the missing chart dependency clearly.
    raise RuntimeError("matplotlib is required; install it with: pip install matplotlib") from exc

# ----------------------------- Configuration -----------------------------

VERSION = "2.0.0"
FUTURES_REST = "https://futures.kraken.com/derivatives/api/v3"
CHARTS_REST = "https://futures.kraken.com/api/charts/v1"
TELEGRAM_API = "https://api.telegram.org"

UNIVERSE_SIZE = max(2, int(os.getenv("UNIVERSE_SIZE", "100")))
MAX_SIGNAL_AGE_HOURS = max(1, int(os.getenv("MAX_SIGNAL_AGE_HOURS", "72")))
M15_CANDLE_COUNT = 1500
STOP_BUFFER_PCT = max(0.0, float(os.getenv("STOP_BUFFER_PCT", "0.10")))
MAX_CONCURRENT_REQUESTS = 8
MAX_CHARTS_PER_RUN = 8
REQUIRED_SYMBOLS = ("PF_XBTUSD", "PF_XAUTUSD")
# Keep the existing DB filename so old paper-trade records remain available.
DB_PATH = Path(os.getenv("DB_PATH", "nds_m5_long_short_structure_v1.db"))
CHART_DIR = Path(os.getenv("CHART_DIR", "nds_m15_long_short_structure_charts"))
REPORT_TZ_NAME = os.getenv("REPORT_TIMEZONE", "Europe/Berlin")
try:
    REPORT_TZ = ZoneInfo(REPORT_TZ_NAME)
except Exception:
    REPORT_TZ = timezone.utc

M15_MS = 15 * 60 * 1000
M1_MS = 60 * 1000
M1_CANDLE_COUNT = 4500
M5_MS = 5 * 60 * 1000  # Retained only to manage paper trades opened by the previous M5 version.
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": f"NDS-M15-Structure-Scanner/{VERSION}", "Accept": "application/json"})

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
LOG = logging.getLogger("nds_m15_long_short")


# ----------------------------- Generic helpers -----------------------------

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
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).astimezone(REPORT_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")


def candle_epoch_seconds(candle: dict[str, Any]) -> float:
    return float(candle["time"]) / 1000.0


def request_json(url: str, params: Optional[dict[str, Any]] = None, retries: int = 3) -> dict[str, Any]:
    last_exc: Optional[BaseException] = None
    for attempt in range(retries):
        try:
            response = HTTP.get(url, params=params, timeout=(8, 25))
            if response.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {response.status_code}: {response.text[:200]}")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError(f"Unexpected JSON shape from {url}")
            return payload
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(0.7 * (attempt + 1))
    raise RuntimeError(f"Request failed for {url}: {last_exc}")


def get_tickers() -> list[dict[str, Any]]:
    payload = request_json(f"{FUTURES_REST}/tickers")
    tickers = payload.get("tickers")
    if not isinstance(tickers, list):
        raise RuntimeError(f"Kraken tickers response has no ticker list: {json.dumps(payload)[:500]}")
    return [item for item in tickers if isinstance(item, dict)]


def extract_volume(ticker: dict[str, Any]) -> float:
    # Kraken ticker field names can vary by contract/response generation.
    for key in ("vol24h", "volume24h", "volume", "volume24H", "turnover24h", "usdVolume24h"):
        value = as_float(ticker.get(key))
        if value is not None and value >= 0:
            return value
    return 0.0


def get_universe(tickers: list[dict[str, Any]]) -> list[str]:
    rows: list[tuple[str, float]] = []
    for ticker in tickers:
        symbol = str(ticker.get("symbol", "")).upper()
        # Keep USD-settled perpetual symbols for consistent cross-market behavior.
        if symbol.startswith("PF_") and symbol.endswith("USD"):
            rows.append((symbol, extract_volume(ticker)))
    rows.sort(key=lambda row: (row[1], row[0]), reverse=True)

    selected: list[str] = []
    for required in REQUIRED_SYMBOLS:
        selected.append(required)
    for symbol, _volume in rows:
        if symbol not in selected:
            selected.append(symbol)
        if len(selected) >= UNIVERSE_SIZE:
            break
    return selected[:UNIVERSE_SIZE] if len(selected) >= UNIVERSE_SIZE else selected


def current_price_from_ticker(ticker: Optional[dict[str, Any]]) -> Optional[float]:
    if not ticker:
        return None
    bid = as_float(ticker.get("bid")) or as_float(ticker.get("bidPrice"))
    ask = as_float(ticker.get("ask")) or as_float(ticker.get("askPrice"))
    if bid is not None and ask is not None and bid > 0 and ask >= bid:
        return (bid + ask) / 2.0
    for key in ("markPrice", "mark_price", "last", "lastPrice", "lastTradePrice", "indexPrice", "index_price"):
        value = as_float(ticker.get(key))
        if value is not None and value > 0:
            return value
    return None


def ticker_map(tickers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("symbol", "")).upper(): item for item in tickers if item.get("symbol")}


# ----------------------------- Candle data -----------------------------

def get_candles(symbol: str, resolution: str, limit: int) -> list[dict[str, Any]]:
    """Fetch OHLC candles and remove the currently-open candle."""
    now = time.time()
    timeframe_ms = {"1m": M1_MS, "5m": M5_MS, "15m": M15_MS}.get(resolution)
    if timeframe_ms is None:
        raise ValueError(f"Unsupported candle resolution: {resolution}")
    timeframe_seconds = timeframe_ms / 1000
    params = {
        "from": int(now - timeframe_seconds * (limit + 5)),
        "to": int(now),
        "count": limit,
    }
    payload = request_json(f"{CHARTS_REST}/trade/{symbol}/{resolution}", params=params)
    rows = payload.get("candles", [])
    if not isinstance(rows, list):
        raise RuntimeError(f"Invalid candles shape for {symbol} {resolution}")

    by_time: dict[int, dict[str, Any]] = {}
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
        if ts_i < 1_000_000_000_000:  # Defensive support for second timestamps.
            ts_i *= 1000
        # Only use a candle after its interval has ended.
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
    return [by_time[key] for key in sorted(by_time)][-limit:]


def heiken_ashi_candles(candles: list[dict[str, Any]]) -> list[dict[str, float]]:
    """Build standard Heikin-Ashi OHLC values from the source candles."""
    result: list[dict[str, float]] = []
    previous_open: Optional[float] = None
    previous_close: Optional[float] = None
    for candle in candles:
        ha_close = (
            float(candle["open"]) + float(candle["high"])
            + float(candle["low"]) + float(candle["close"])
        ) / 4.0
        ha_open = (
            (float(candle["open"]) + float(candle["close"])) / 2.0
            if previous_open is None
            else (previous_open + float(previous_close)) / 2.0
        )
        result.append({
            "open": ha_open,
            "close": ha_close,
            "high": max(float(candle["high"]), ha_open, ha_close),
            "low": min(float(candle["low"]), ha_open, ha_close),
        })
        previous_open, previous_close = ha_open, ha_close
    return result


def heiken_ashi_colors_from_values(ha: list[dict[str, float]]) -> list[int]:
    """Return HA direction: 1=green, -1=red, 0=doji/neutral."""
    colors: list[int] = []
    for candle in ha:
        if candle["close"] > candle["open"]:
            colors.append(1)
        elif candle["close"] < candle["open"]:
            colors.append(-1)
        else:
            colors.append(0)
    return colors


def heiken_ashi_colors(candles: list[dict[str, Any]]) -> list[int]:
    return heiken_ashi_colors_from_values(heiken_ashi_candles(candles))


def find_confirmed_pivot_records(
    candles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find HA swing pivots from color-run reversals, with no fixed left/right count.

    A green-to-red reversal confirms the highest HA high of the completed green run.
    A red-to-green reversal confirms the lowest HA low of the completed red run.
    Neutral HA candles stay attached to the active run. The reversal candle must be
    closed because get_candles() already removes the currently-open candle.
    """
    if len(candles) < 3:
        return []
    ha = heiken_ashi_candles(candles)
    colors = heiken_ashi_colors_from_values(ha)
    records: list[dict[str, Any]] = []

    active_direction = 0
    run_indices: list[int] = []

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

        # A genuine HA color reversal closes the preceding directional run.
        if run_indices:
            if active_direction == 1 and color == -1:
                pivot_i = max(run_indices, key=lambda k: ha[k]["high"])
                records.append({
                    "index": pivot_i,
                    "kind": "H",
                    "price": float(ha[pivot_i]["high"]),
                    "confirmed_index": i,
                })
            elif active_direction == -1 and color == 1:
                pivot_i = min(run_indices, key=lambda k: ha[k]["low"])
                records.append({
                    "index": pivot_i,
                    "kind": "L",
                    "price": float(ha[pivot_i]["low"]),
                    "confirmed_index": i,
                })

        active_direction = color
        run_indices = [i]

    return records


def find_confirmed_pivots(
    candles: list[dict[str, Any]], left: Optional[int] = None, right: Optional[int] = None
) -> tuple[list[int], list[int]]:
    """Compatibility wrapper returning HA-only pivot indices; left/right are ignored."""
    records = find_confirmed_pivot_records(candles)
    lows = [int(p["index"]) for p in records if p["kind"] == "L"]
    highs = [int(p["index"]) for p in records if p["kind"] == "H"]
    return lows, highs


def pattern_key(symbol: str, candles: list[dict[str, Any]], points: dict[str, int], direction: str, timeframe: str = "M15") -> str:
    timestamps = [str(candles[points[name]]["time"]) for name in ("start", "p1", "p2", "p3", "p4", "p5")]
    return f"{direction}|{symbol}|{timeframe}|" + "|".join(timestamps)


def _same_price(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-12)


def detect_patterns(symbol: str, candles: list[dict[str, Any]], direction: str, timeframe: str = "M15") -> list[dict[str, Any]]:
    """Detect every six-pivot structure using HA pivots and HA prices only.

    SHORT: START low -> H1 -> L1 -> H2 -> L2 -> H3.
    LONG:  START high -> L1 -> H1 -> L2 -> H2 -> L3 (mirror).
    Each pivot is a color-reversal swing. No left/right candle count is required.
    """
    if len(candles) < 10:
        return []

    ha = heiken_ashi_candles(candles)
    records = find_confirmed_pivot_records(candles)
    if len(records) < 6:
        return []

    patterns: list[dict[str, Any]] = []
    wanted = ["L", "H", "L", "H", "L", "H"] if direction == "SHORT" else ["H", "L", "H", "L", "H", "L"]

    for j in range(0, len(records) - 5):
        seq = records[j:j + 6]
        if [p["kind"] for p in seq] != wanted:
            continue

        idx = [int(p["index"]) for p in seq]
        start_i, p1_i, p2_i, p3_i, p4_i, p5_i = idx
        prices = [float(p["price"]) for p in seq]
        start_price, p1, p2, p3, p4, p5 = prices

        if direction == "SHORT":
            valid = (
                p1 > start_price and p2 > start_price
                and p3 > p1 and p4 > start_price and p4 < p2
                and p5 > p1 and p5 > p3
            )
            valid = valid and _same_price(
                p1, max(float(c["high"]) for c in ha[start_i:p2_i + 1])
            )
            valid = valid and _same_price(
                p2, min(float(c["low"]) for c in ha[p1_i:p3_i + 1])
            )
            valid = valid and _same_price(
                p3, max(float(c["high"]) for c in ha[p2_i:p4_i + 1])
            )
            valid = valid and _same_price(
                p4, min(float(c["low"]) for c in ha[p3_i:p5_i + 1])
            )
            valid = valid and _same_price(
                start_price, min(float(c["low"]) for c in ha[start_i:p5_i + 1])
            )
        else:
            valid = (
                p1 < start_price and p2 < start_price
                and p3 < p1 and p4 < start_price and p4 > p2
                and p5 < p1 and p5 < p3
            )
            valid = valid and _same_price(
                p1, min(float(c["low"]) for c in ha[start_i:p2_i + 1])
            )
            valid = valid and _same_price(
                p2, max(float(c["high"]) for c in ha[p1_i:p3_i + 1])
            )
            valid = valid and _same_price(
                p3, min(float(c["low"]) for c in ha[p2_i:p4_i + 1])
            )
            valid = valid and _same_price(
                p4, max(float(c["high"]) for c in ha[p3_i:p5_i + 1])
            )
            valid = valid and _same_price(
                start_price, max(float(c["high"]) for c in ha[start_i:p5_i + 1])
            )

        if not valid:
            continue

        pts = {
            "start": start_i, "p1": p1_i, "p2": p2_i,
            "p3": p3_i, "p4": p4_i, "p5": p5_i,
        }
        confirmation_index = int(seq[-1]["confirmed_index"])
        if confirmation_index >= len(candles):
            continue
        pivot_prices = {
            "start": start_price, "p1": p1, "p2": p2,
            "p3": p3, "p4": p4, "p5": p5,
        }
        patterns.append({
            "symbol": symbol,
            "direction": direction,
            "points": pts,
            "pivot_prices": pivot_prices,
            "key": pattern_key(symbol, candles, pts, direction, timeframe),
            "confirmation_time": (float(candles[confirmation_index]["time"]) + (M1_MS if timeframe == "M1" else M15_MS)) / 1000.0,
            "timeframe": timeframe,
            "start_price": start_price,
            "p1_price": p1,
            "p2_price": p2,
            "p3_price": p3,
            "p4_price": p4,
            "p5_price": p5,
            "terminal_time": float(candles[p5_i]["time"]) / 1000.0,
        })

    # Return every valid structure, newest first. A seen or expired structure
    # must never mask a later structure, including one in the opposite direction.
    patterns.sort(key=lambda item: item["confirmation_time"], reverse=True)
    return patterns

def find_nearest_valid_m15_low(candles15: list[dict[str, Any]], entry: float) -> Optional[float]:
    """Closest intact confirmed HA swing low below entry."""
    ha = heiken_ashi_candles(candles15)
    records = find_confirmed_pivot_records(candles15)
    eligible: list[tuple[float, int]] = []
    for pivot in records:
        if pivot["kind"] != "L":
            continue
        i = int(pivot["index"])
        price = float(pivot["price"])
        if price < entry and not any(float(b["low"]) <= price for b in ha[i + 1:]):
            eligible.append((price, i))
    return max(eligible, key=lambda item: item[0])[0] if eligible else None


def find_nearest_valid_m15_high(candles15: list[dict[str, Any]], entry: float) -> Optional[float]:
    """Closest intact confirmed HA swing high above entry."""
    ha = heiken_ashi_candles(candles15)
    records = find_confirmed_pivot_records(candles15)
    eligible: list[tuple[float, int]] = []
    for pivot in records:
        if pivot["kind"] != "H":
            continue
        i = int(pivot["index"])
        price = float(pivot["price"])
        if price > entry and not any(float(b["high"]) >= price for b in ha[i + 1:]):
            eligible.append((price, i))
    return min(eligible, key=lambda item: item[0])[0] if eligible else None


# ----------------------------- Database -----------------------------

def connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
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
            FOREIGN KEY(pattern_key) REFERENCES patterns(pattern_key)
        );
        CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
        CREATE INDEX IF NOT EXISTS idx_trades_symbol_status ON trades(symbol, status);
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
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
    if "direction" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN direction TEXT NOT NULL DEFAULT 'LONG'")
    conn.commit()
    return conn



def serialize_ready_pattern(pattern: dict[str, Any], candles: list[dict[str, Any]]) -> str:
    names = ("start", "p1", "p2", "p3", "p4", "p5")
    payload = {
        "symbol": pattern["symbol"], "direction": pattern["direction"],
        "timeframe": "M15", "key": pattern["key"],
        "point_times": {name: int(candles[int(pattern["points"][name])]["time"]) for name in names},
        "pivot_prices": {name: float(pattern["pivot_prices"][name]) for name in names},
        "confirmation_time": float(pattern["confirmation_time"]),
        "start_price": float(pattern["start_price"]), "p1_price": float(pattern["p1_price"]),
        "p2_price": float(pattern["p2_price"]), "p3_price": float(pattern["p3_price"]),
        "p4_price": float(pattern["p4_price"]), "p5_price": float(pattern["p5_price"]),
        "terminal_time": float(pattern["terminal_time"]),
    }
    return json.dumps(payload, separators=(",", ":"))


def restore_ready_pattern(setup: sqlite3.Row, candles: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(str(setup["pattern_json"]))
        by_time = {int(c["time"]): i for i, c in enumerate(candles)}
        point_times = data["point_times"]
        points = {name: by_time[int(point_times[name])] for name in ("start", "p1", "p2", "p3", "p4", "p5")}
        result = dict(data)
        result["points"] = points
        result["pivot_prices"] = {k: float(v) for k, v in data["pivot_prices"].items()}
        return result
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        LOG.warning("Cannot restore M15 ready setup for %s", setup["symbol"])
        return None


def active_ready_setups(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM ready_setups WHERE status='WAITING_M1' ORDER BY created_at ASC"))


def save_ready_setup(conn: sqlite3.Connection, pattern: dict[str, Any], candles: list[dict[str, Any]], tp: float, sl: float) -> None:
    now = time.time()
    conn.execute(
        """INSERT INTO ready_setups(symbol,direction,pattern_key,pattern_json,tp_price,sl_price,created_at,status,updated_at)
           VALUES(?,?,?,?,?,?,?,'WAITING_M1',?)
           ON CONFLICT(symbol) DO UPDATE SET direction=excluded.direction, pattern_key=excluded.pattern_key,
           pattern_json=excluded.pattern_json, tp_price=excluded.tp_price, sl_price=excluded.sl_price,
           created_at=excluded.created_at, status='WAITING_M1', updated_at=excluded.updated_at""",
        (pattern["symbol"], pattern["direction"], pattern["key"], serialize_ready_pattern(pattern, candles), tp, sl,
         float(pattern["confirmation_time"]), now),
    )
    conn.commit()


def finish_ready_setup(conn: sqlite3.Connection, symbol: str, status: str) -> None:
    conn.execute("UPDATE ready_setups SET status=?, updated_at=? WHERE symbol=? AND status='WAITING_M1'",
                 (status, time.time(), symbol))
    conn.commit()


def pattern_seen(conn: sqlite3.Connection, key: str) -> bool:
    # A pattern is permanently consumed only after a paper trade was opened.
    # Old SKIPPED decisions (for example, temporarily full trade slots) must
    # not block a still-valid pattern from being evaluated again.
    row = conn.execute(
        "SELECT 1 FROM patterns p LEFT JOIN trades t ON t.pattern_key=p.pattern_key "
        "WHERE p.pattern_key=? AND (p.decision='TRADE' OR t.pattern_key IS NOT NULL) LIMIT 1",
        (key,),
    ).fetchone()
    return row is not None


def save_pattern_decision(conn: sqlite3.Connection, pattern: dict[str, Any], decision: str, reason: str = "") -> None:
    conn.execute(
        """INSERT INTO patterns(pattern_key,symbol,detected_at,decision,reason)
           VALUES(?,?,?,?,?)
           ON CONFLICT(pattern_key) DO UPDATE SET
               decision=excluded.decision,
               reason=excluded.reason
           WHERE patterns.decision != 'TRADE'""",
        (pattern["key"], pattern["symbol"], time.time(), decision, reason[:500]),
    )
    conn.commit()


def open_trades(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY entry_time ASC"))


def trade_candle_settings(trade: sqlite3.Row) -> tuple[str, int, str]:
    """Keep legacy M5 trades working while new trades use M15."""
    parts = str(trade["pattern_key"]).split("|")
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
    direction = str(pattern.get("direction", "LONG"))
    entry_ms = int(entry_time * 1000)
    _resolution, trade_interval_ms, _label = trade_candle_settings({"pattern_key": pattern["key"]})
    next_bar_time_ms = ((entry_ms // trade_interval_ms) + 1) * trade_interval_ms
    conn.execute(
        """INSERT INTO trades
        (pattern_key,symbol,direction,entry_time,next_bar_time_ms,entry_price,tp_price,sl_price,status,created_at)
        VALUES(?,?,?,?,?,?,?,?,'OPEN',?)""",
        (pattern["key"], pattern["symbol"], direction, entry_time, next_bar_time_ms, entry_price, tp, sl, time.time()),
    )
    conn.commit()


def close_trade(conn: sqlite3.Connection, trade_id: int, status: str, exit_time: float, exit_price: float) -> None:
    row = conn.execute("SELECT entry_price,direction FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row:
        return
    entry = float(row["entry_price"])
    pnl = ((exit_price / entry - 1.0) if row["direction"] == "LONG" else (entry / exit_price - 1.0)) * 100.0
    conn.execute(
        "UPDATE trades SET status=?, exit_time=?, exit_price=?, pnl_pct=? WHERE id=? AND status='OPEN'",
        (status, exit_time, exit_price, pnl, trade_id),
    )
    conn.commit()


def trade_pnl_percent(entry: float, price: Optional[float], direction: str = "LONG") -> Optional[float]:
    if price is None or entry <= 0 or price <= 0:
        return None
    return ((price / entry - 1.0) if direction == "LONG" else (entry / price - 1.0)) * 100.0


# ----------------------------- Charting -----------------------------

def create_signal_chart(symbol: str, candles: list[dict[str, Any]], pattern: dict[str, Any], entry: Optional[float], tp: Optional[float], sl: Optional[float], current_price: Optional[float] = None, chart_title: str = "NEW SIGNAL") -> Path:
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    pts = pattern["points"]; direction = pattern["direction"]
    left_edge = max(0, min(min(pts.values()) - 12, len(candles) - 175))
    chart_candles = candles[left_edge:]
    if len(chart_candles) < 10:
        chart_candles = candles[-100:]; left_edge = len(candles) - len(chart_candles)
    fig, ax = plt.subplots(figsize=(13, 7), dpi=135)
    # Draw actual Heikin-Ashi OHLC candles, not real OHLC candles merely colored
    # with HA direction. This keeps the visual structure consistent with pivots.
    ha_all = heiken_ashi_candles(candles)
    for x, _source_candle in enumerate(chart_candles):
        c = ha_all[left_edge + x]
        ha_direction = 1 if c["close"] > c["open"] else (-1 if c["close"] < c["open"] else 0)
        color = "#18a558" if ha_direction >= 0 else "#e74c3c"
        ax.vlines(x, c["low"], c["high"], color=color, linewidth=1.1, zorder=2)
        body_low = min(c["open"], c["close"])
        body_h = max(abs(c["close"] - c["open"]), abs(c["close"]) * 0.000002)
        ax.add_patch(Rectangle((x-0.31, body_low), 0.62, body_h,
                               facecolor=color, edgecolor=color, linewidth=0.6, zorder=3))
    pivot_prices = pattern.get("pivot_prices") or {}
    if direction == "LONG":
        style={"start":("START","#277da1","o",pivot_prices.get("start", candles[pts["start"]]["high"])),"p1":("1 RED","#db2b39","v",pivot_prices.get("p1", candles[pts["p1"]]["low"])),"p2":("1 GREEN","#149447","^",pivot_prices.get("p2", candles[pts["p2"]]["high"])),"p3":("2 RED","#db2b39","v",pivot_prices.get("p3", candles[pts["p3"]]["low"])),"p4":("2 GREEN / TP","#149447","^",pivot_prices.get("p4", candles[pts["p4"]]["high"])),"p5":("3 RED / TRIGGER","#db2b39","v",pivot_prices.get("p5", candles[pts["p5"]]["low"]))}
    else:
        style={"start":("START","#277da1","o",pivot_prices.get("start", candles[pts["start"]]["low"])),"p1":("1 GREEN","#149447","^",pivot_prices.get("p1", candles[pts["p1"]]["high"])),"p2":("1 RED","#db2b39","v",pivot_prices.get("p2", candles[pts["p2"]]["low"])),"p3":("2 GREEN","#149447","^",pivot_prices.get("p3", candles[pts["p3"]]["high"])),"p4":("2 RED / TP","#db2b39","v",pivot_prices.get("p4", candles[pts["p4"]]["low"])),"p5":("3 GREEN / TRIGGER","#149447","^",pivot_prices.get("p5", candles[pts["p5"]]["high"]))}
    for name,(label,color,marker,y) in style.items():
        x=pts[name]-left_edge
        if 0 <= x < len(chart_candles):
            ax.scatter([x],[y],s=75,marker=marker,color=color,edgecolors="white",linewidths=0.7,zorder=5)
            off=9 if marker in ("^","o") else -14
            ax.annotate(label,(x,y),xytext=(0,off),textcoords="offset points",fontsize=8,color=color,fontweight="bold",ha="center",va="bottom" if off>0 else "top",bbox={"boxstyle":"round,pad=0.18","fc":"white","ec":color,"alpha":0.86,"lw":0.65})
    last_x=len(chart_candles)-1
    if tp is not None:
        ax.axhline(tp,color="#149447",linestyle="--",linewidth=1.25,label=f"TP {fmt_price(tp)}")
    if sl is not None:
        ax.axhline(sl,color="#db2b39",linestyle="--",linewidth=1.25,label=f"SL {fmt_price(sl)}")
    if entry is not None:
        ax.axhline(entry,color="#277da1",linestyle=":",linewidth=1.5,label=f"ENTRY {fmt_price(entry)}")
        ax.scatter([last_x],[entry],color="#277da1",marker="D",s=68,edgecolors="white",linewidths=0.7,zorder=6)
        ax.annotate("ENTRY",(last_x,entry),xytext=(-8,8),textcoords="offset points",ha="right",fontsize=8,color="#277da1",fontweight="bold")
    if current_price is not None and current_price > 0:
        ax.axhline(current_price,color="#8e44ad",linestyle="-.",linewidth=1.35,label=f"NOW {fmt_price(current_price)}")
        ax.scatter([last_x],[current_price],color="#8e44ad",marker="o",s=55,edgecolors="white",linewidths=0.7,zorder=7)
    n=min(8,len(chart_candles)); ticks=sorted(set(round(i*(len(chart_candles)-1)/max(1,n-1)) for i in range(n)))
    ax.set_xticks(ticks); ax.set_xticklabels([datetime.fromtimestamp(chart_candles[i]["time"]/1000,timezone.utc).astimezone(REPORT_TZ).strftime("%m-%d %H:%M") for i in ticks],rotation=25,ha="right",fontsize=8)
    chart_timeframe = str(pattern.get("timeframe", "M15"))
    ax.set_title(f"NDS {chart_timeframe} {direction} | {symbol} | {chart_title}\n{chart_timeframe} Heikin-Ashi candles | HA reversal-run pivots | {dt_text(pattern['confirmation_time'])}",fontsize=12,fontweight="bold")
    ax.set_ylabel("Price"); ax.grid(True,alpha=0.2); ax.legend(loc="best",fontsize=8,framealpha=0.9); ax.margins(x=0.03,y=0.1); fig.tight_layout()
    safe="".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in symbol)
    path=CHART_DIR/f"{safe}_{direction}_{int(time.time())}.png"; fig.savefig(path,bbox_inches="tight"); plt.close(fig); return path


def restore_trade_pattern(trade: sqlite3.Row, candles: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Restore the six structural pivot indices saved in a trade's pattern_key."""
    parts = str(trade["pattern_key"]).split("|")
    if len(parts) != 9 or parts[0] not in ("LONG", "SHORT"):
        LOG.warning("Cannot restore structure for trade %s: invalid pattern_key", trade["id"])
        return None
    try:
        timestamps = [int(value) for value in parts[3:9]]
    except (TypeError, ValueError):
        LOG.warning("Cannot restore structure for trade %s: invalid timestamps", trade["id"])
        return None
    index_by_time = {int(candle["time"]): i for i, candle in enumerate(candles)}
    if not all(ts in index_by_time for ts in timestamps):
        LOG.warning("Cannot restore all six pivots for open trade %s (%s); chart skipped", trade["id"], trade["symbol"])
        return None
    points = dict(zip(("start", "p1", "p2", "p3", "p4", "p5"), [index_by_time[ts] for ts in timestamps]))
    _resolution, interval_ms, timeframe_label = trade_candle_settings(trade)
    ha = heiken_ashi_candles(candles)
    pivot_prices = {
        "start": float(ha[points["start"]]["low"] if str(trade["direction"]) == "SHORT" else ha[points["start"]]["high"]),
        "p1": float(ha[points["p1"]]["high"] if str(trade["direction"]) == "SHORT" else ha[points["p1"]]["low"]),
        "p2": float(ha[points["p2"]]["low"] if str(trade["direction"]) == "SHORT" else ha[points["p2"]]["high"]),
        "p3": float(ha[points["p3"]]["high"] if str(trade["direction"]) == "SHORT" else ha[points["p3"]]["low"]),
        "p4": float(ha[points["p4"]]["low"] if str(trade["direction"]) == "SHORT" else ha[points["p4"]]["high"]),
        "p5": float(ha[points["p5"]]["high"] if str(trade["direction"]) == "SHORT" else ha[points["p5"]]["low"]),
    }
    pivot_records = find_confirmed_pivot_records(candles)
    confirmation_index = next(
        (int(p["confirmed_index"]) for p in pivot_records if int(p["index"]) == points["p5"]),
        points["p5"] + 1,
    )
    return {
        "symbol": str(trade["symbol"]),
        "direction": str(trade["direction"]),
        "points": points,
        "pivot_prices": pivot_prices,
        "timeframe": timeframe_label,
        "confirmation_time": (float(candles[min(confirmation_index, len(candles)-1)]["time"]) + interval_ms) / 1000.0,
    }


def send_open_trade_charts(
    conn: sqlite3.Connection,
    candles_by_symbol: dict[str, list[dict[str, Any]]],
    prices: dict[str, Optional[float]],
) -> tuple[int, int]:
    """Send a fresh chart for every trade that remains OPEN in SQLite."""
    sent = 0
    attempted = 0
    for trade in open_trades(conn):
        symbol = str(trade["symbol"])
        resolution, interval_ms, timeframe_label = trade_candle_settings(trade)
        bars = candles_by_symbol.get(symbol) if timeframe_label == "M15" else None
        if not bars:
            try:
                bars = get_candles(symbol, resolution, M15_CANDLE_COUNT)
                if timeframe_label == "M15":
                    candles_by_symbol[symbol] = bars
            except Exception as exc:
                LOG.warning("Cannot fetch chart candles for open trade %s: %s", trade["id"], exc)
                continue
        if len(bars) < 10:
            LOG.warning("Not enough candles to chart open trade %s", trade["id"])
            continue
        pattern = restore_trade_pattern(trade, bars)
        if pattern is None:
            continue
        current = prices.get(symbol)
        if current is None or current <= 0:
            current = float(bars[-1]["close"])
            prices[symbol] = current
        entry = float(trade["entry_price"])
        tp = float(trade["tp_price"])
        sl = float(trade["sl_price"])
        direction = str(trade["direction"])
        floating = trade_pnl_percent(entry, current, direction)
        try:
            chart_path = create_signal_chart(
                symbol, bars, pattern, entry, tp, sl,
                current_price=current, chart_title="OPEN TRADE UPDATE",
            )
            emoji = "🟢" if direction == "LONG" else "🔴"
            caption = (
                f"{emoji} OPEN {direction} | {symbol} | Trade #{trade['id']}\n"
                f"Entry: {fmt_price(entry)}\n"
                f"Current: {fmt_price(current)} | Floating PnL: {fmt_pct(floating)}\n"
                f"TP: {fmt_price(tp)} ({fmt_pct(pct_from_entry(entry, tp, direction))})\n"
                f"SL: {fmt_price(sl)} ({fmt_pct(pct_from_entry(entry, sl, direction))})\n"
                f"{timeframe_label} Heikin-Ashi chart: START, pivots 1/2, trigger, ENTRY, TP, SL and current price"
            )
            attempted += 1
            if telegram_send_photo(chart_path, caption):
                sent += 1
        except Exception:
            LOG.exception("Open-trade chart/send failed for %s trade #%s", symbol, trade["id"])
    return sent, attempted


# ----------------------------- Telegram -----------------------------

def telegram_credentials() -> tuple[Optional[str], Optional[str]]:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT")
    return token, chat_id


def telegram_send_message(text: str) -> bool:
    token, chat_id = telegram_credentials()
    if not token or not chat_id:
        LOG.warning("Telegram credentials missing; message not sent")
        return False
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    # Keep comfortably below Telegram's message length limit.
    chunks: list[str] = []
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
            response = HTTP.post(url, data={"chat_id": chat_id, "text": chunk}, timeout=(8, 25))
            if response.status_code >= 400 or not response.json().get("ok", False):
                LOG.error("Telegram sendMessage failed: HTTP %s %s", response.status_code, response.text[:300])
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
                data={"chat_id": chat_id, "caption": caption[:1000]},
                files={"photo": (path.name, photo_file, "image/png")},
                timeout=(10, 45),
            )
        if response.status_code >= 400 or not response.json().get("ok", False):
            LOG.error("Telegram sendPhoto failed: HTTP %s %s", response.status_code, response.text[:300])
            return False
        return True
    except Exception:
        LOG.exception("Telegram sendPhoto error")
        return False


# ----------------------------- Trade management -----------------------------

def update_open_trades(
    conn: sqlite3.Connection,
    candles_by_symbol: dict[str, list[dict[str, Any]]],
    live_prices: Optional[dict[str, Optional[float]]] = None,
) -> list[dict[str, Any]]:
    closed: list[dict[str, Any]] = []
    for trade in open_trades(conn):
        symbol = str(trade["symbol"])
        resolution, interval_ms, timeframe_label = trade_candle_settings(trade)
        bars = candles_by_symbol.get(symbol) if timeframe_label == "M15" else None
        if bars is None:
            try:
                bars = get_candles(symbol, resolution, M15_CANDLE_COUNT)
                if timeframe_label == "M15":
                    candles_by_symbol[symbol] = bars
            except Exception as exc:
                LOG.warning("Cannot refresh open trade %s: %s", symbol, exc)
                continue
        entry_ms = int(trade["next_bar_time_ms"])
        tp = float(trade["tp_price"])
        sl = float(trade["sl_price"])
        for bar in bars:
            if int(bar["time"]) < entry_ms:
                continue
            direction = str(trade["direction"])
            hit_sl = bar["low"] <= sl if direction == "LONG" else bar["high"] >= sl
            hit_tp = bar["high"] >= tp if direction == "LONG" else bar["low"] <= tp
            if not hit_sl and not hit_tp:
                continue
            # Conservative resolution if both levels are touched in one candle.
            if hit_sl:
                status, exit_price = "CLOSED_SL", sl
            else:
                status, exit_price = "CLOSED_TP", tp
            exit_time = (bar["time"] + interval_ms) / 1000.0
            close_trade(conn, int(trade["id"]), status, exit_time, exit_price)
            closed.append({
                "symbol": symbol,
                "status": status,
                "entry": float(trade["entry_price"]),
                "exit": exit_price,
                "pnl_pct": trade_pnl_percent(float(trade["entry_price"]), exit_price, str(trade["direction"])),
                "direction": str(trade["direction"]),
                "exit_time": exit_time,
            })
            break
        else:
            # Fallback to the latest live ticker. This catches a TP/SL touch that
            # may be missing from the locally fetched closed-candle window, and
            # prevents a trade remaining OPEN after the market has crossed a level.
            live_price = (live_prices or {}).get(symbol)
            if live_price is not None and live_price > 0:
                direction = str(trade["direction"])
                hit_sl_live = live_price <= sl if direction == "LONG" else live_price >= sl
                hit_tp_live = live_price >= tp if direction == "LONG" else live_price <= tp
                if hit_sl_live or hit_tp_live:
                    # If the current price is beyond both levels due to a gap or
                    # inconsistent feed, resolve conservatively in favor of SL.
                    if hit_sl_live:
                        status, exit_price = "CLOSED_SL", sl
                    else:
                        status, exit_price = "CLOSED_TP", tp
                    exit_time = time.time()
                    close_trade(conn, int(trade["id"]), status, exit_time, exit_price)
                    closed.append({
                        "symbol": symbol,
                        "status": status,
                        "entry": float(trade["entry_price"]),
                        "exit": exit_price,
                        "pnl_pct": trade_pnl_percent(float(trade["entry_price"]), exit_price, direction),
                        "direction": direction,
                        "exit_time": exit_time,
                    })
    return closed


def get_performance(conn: sqlite3.Connection, prices: dict[str, Optional[float]]) -> dict[str, Any]:
    closed_rows = list(conn.execute("SELECT status,pnl_pct FROM trades WHERE status IN ('CLOSED_TP','CLOSED_SL')"))
    open_rows = open_trades(conn)
    wins = sum(1 for row in closed_rows if row["status"] == "CLOSED_TP" and (row["pnl_pct"] or 0) > 0)
    losses = len(closed_rows) - wins
    win_rate = 100.0 * wins / len(closed_rows) if closed_rows else 0.0
    realized = sum(float(row["pnl_pct"] or 0.0) for row in closed_rows)
    floating = 0.0
    for row in open_rows:
        price = prices.get(str(row["symbol"]))
        pnl = trade_pnl_percent(float(row["entry_price"]), price, str(row["direction"]))
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


def pct_from_entry(entry: float, level: float, direction: str = "LONG") -> float:
    return ((level / entry - 1.0) if direction == "LONG" else (entry / level - 1.0)) * 100.0


def make_report(scan_stats: dict[str, Any], new_signals: list[dict[str, Any]], closed_trades: list[dict[str, Any]], perf: dict[str, Any], prices: dict[str, Optional[float]]) -> str:
    lines=[f"🔎 NDS M15 LONG + SHORT v{VERSION} | {dt_text(time.time())}",f"Scan: {scan_stats['scanned']} symbols | M15 OK {scan_stats['m15_ok']} | errors {scan_stats['errors']} | structures {scan_stats['patterns']}",f"Universe: {scan_stats['universe_size']} | Open trades: {perf['open_count']} | No maximum limit",""]
    diagnostics = scan_stats.get("diagnostics", {})
    if diagnostics:
        lines.append("🧭 SIGNAL DIAGNOSTICS")
        lines.append(
            f"Detected: {diagnostics.get('detected_total', scan_stats.get('patterns', 0))} | "
            f"Fresh candidates: {diagnostics.get('candidates', 0)} | "
            f"Already traded: {diagnostics.get('already_seen', 0)} | "
            f"Expired: {diagnostics.get('expired', 0)}"
        )
        for key, label in (("no_price", "No current price"), ("bad_tp", "TP on wrong side of entry"), ("sl_missing", "No valid HA SL pivot"), ("bad_levels", "Invalid SL/entry/TP order"), ("candle_refresh_error", "Failed to refresh M15 candles"), ("opened", "Paper trades opened")):
            count = diagnostics.get(key, 0)
            if count:
                lines.append(f"• {label}: {count}")
        lines.append("")
    for direction, emoji in (("LONG","🟢"),("SHORT","🔴")):
        items=[x for x in new_signals if x["direction"]==direction]
        lines.append(f"{emoji} NEW {direction} SIGNALS: {len(items)}")
        for item in items:
            d=item["direction"]; lines.append(f"• {item['symbol']} | Entry {fmt_price(item['entry'])} | TP {fmt_price(item['tp'])} ({fmt_pct(pct_from_entry(item['entry'],item['tp'],d))}) | SL {fmt_price(item['sl'])} ({fmt_pct(pct_from_entry(item['entry'],item['sl'],d))})")
        if not items: lines.append("No new signals.")
    if closed_trades:
        lines.extend(["",f"✅ CLOSED THIS RUN: {len(closed_trades)}"])
        for t in closed_trades:
            emoji="✅" if t["status"]=="CLOSED_TP" else "🛑"
            lines.append(f"{emoji} {t['symbol']} {t['direction']} {t['status']} | Entry {fmt_price(t['entry'])} | Exit {fmt_price(t['exit'])} | PnL {fmt_pct(t['pnl_pct'])}")
    lines.extend(["",f"📂 OPEN TRADES: {perf['open_count']}"])
    for row in perf["open_rows"]:
        d=str(row["direction"]); entry=float(row["entry_price"]); tp=float(row["tp_price"]); sl=float(row["sl_price"]); price=prices.get(str(row["symbol"])); floating=trade_pnl_percent(entry,price,d); emoji="🟢" if d=="LONG" else "🔴"
        lines.append(f"{emoji} {row['symbol']} {d} | Entry {fmt_price(entry)} | Now {fmt_price(price)} ({fmt_pct(floating)}) | TP {fmt_price(tp)} ({fmt_pct(pct_from_entry(entry,tp,d))}) | SL {fmt_price(sl)} ({fmt_pct(pct_from_entry(entry,sl,d))})")
    if not perf["open_rows"]: lines.append("No open paper trades.")
    lines.extend(["","📊 PERFORMANCE",f"Closed trades: {perf['closed_count']} | W/L: {perf['wins']}/{perf['losses']} | Win rate: {perf['win_rate']:.2f}%",f"Realized PnL: {fmt_pct(perf['realized_pct'])}",f"Floating PnL: {fmt_pct(perf['floating_pct'])}",f"TOTAL PnL: {fmt_pct(perf['total_pct'])}","PnL is the sum of directional equal-notional trade percentages, gross before fees/slippage.","Paper/signal mode only. No real orders are placed."])
    return "\n".join(lines)


# ----------------------------- Main run -----------------------------

def scan_symbol(symbol: str) -> tuple[str, Optional[list[dict[str, Any]]], Optional[str]]:
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
        message = f"❌ NDS M15/M1 scanner startup error: unable to load Kraken Futures tickers.\n{exc}"
        LOG.exception("Ticker fetch failed")
        telegram_send_message(message)
        conn.close()
        return 1

    tickers_by_symbol = ticker_map(tickers)
    universe = get_universe(tickers)
    active_before = {str(r["symbol"]): r for r in active_ready_setups(conn)}
    symbols_to_scan = sorted(set(universe) | set(active_before) | {str(row["symbol"]) for row in open_trades(conn)})
    LOG.info("Selected %d symbols (target=%d); active M15 setups=%d", len(universe), UNIVERSE_SIZE, len(active_before))

    candles_by_symbol: dict[str, list[dict[str, Any]]] = {}
    errors_by_symbol: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_REQUESTS) as pool:
        futures = {pool.submit(scan_symbol, symbol): symbol for symbol in symbols_to_scan}
        for future in as_completed(futures):
            symbol, candles, error = future.result()
            if candles is not None:
                candles_by_symbol[symbol] = candles
            elif error:
                errors_by_symbol[symbol] = error
                LOG.warning("M15 data error %s: %s", symbol, error)

    prices: dict[str, Optional[float]] = {}
    for symbol in symbols_to_scan:
        price = current_price_from_ticker(tickers_by_symbol.get(symbol))
        bars = candles_by_symbol.get(symbol)
        if price is None and bars:
            price = bars[-1]["close"]
        prices[symbol] = price

    closed_trades = update_open_trades(conn, candles_by_symbol, prices)
    diagnostics: dict[str, int] = {
        "detected_total": 0, "ready_created": 0, "ready_replaced": 0,
        "ready_expired": 0, "m1_checked": 0, "m1_confirmed": 0,
        "opened": 0, "no_price": 0, "bad_levels": 0, "m1_errors": 0,
    }
    ready_charts: list[dict[str, Any]] = []
    new_signals: list[dict[str, Any]] = []
    scan_time = time.time()

    # Stage 1: detect M15 structures and create/replace persistent 72-hour setups.
    for symbol, bars15 in candles_by_symbol.items():
        candidates: list[dict[str, Any]] = []
        for direction in ("LONG", "SHORT"):
            found = detect_patterns(symbol, bars15, direction, "M15")
            diagnostics["detected_total"] += len(found)
            candidates.extend(p for p in found if 0 <= scan_time - float(p["confirmation_time"]) <= MAX_SIGNAL_AGE_HOURS * 3600)
        if not candidates:
            continue
        candidates.sort(key=lambda p: float(p["confirmation_time"]), reverse=True)
        pattern = candidates[0]
        if pattern_seen(conn, pattern["key"]):
            continue
        existing = next((r for r in active_ready_setups(conn) if str(r["symbol"]) == symbol), None)
        if existing and str(existing["pattern_key"]) == str(pattern["key"]):
            continue
        if existing and float(pattern["confirmation_time"]) <= float(existing["created_at"]):
            continue

        entry_reference = prices.get(symbol)
        if entry_reference is None or entry_reference <= 0:
            diagnostics["no_price"] += 1
            continue
        entry_reference = float(entry_reference)
        tp = float(pattern["p4_price"])
        if (pattern["direction"] == "LONG" and tp <= entry_reference) or (pattern["direction"] == "SHORT" and tp >= entry_reference):
            diagnostics["bad_levels"] += 1
            continue
        sl = find_nearest_valid_m15_low(bars15, entry_reference) if pattern["direction"] == "LONG" else find_nearest_valid_m15_high(bars15, entry_reference)
        if sl is not None:
            buffer = STOP_BUFFER_PCT / 100.0
            sl = sl * (1.0 - buffer) if pattern["direction"] == "LONG" else sl * (1.0 + buffer)
        if sl is None or (pattern["direction"] == "LONG" and not sl < entry_reference < tp) or (pattern["direction"] == "SHORT" and not tp < entry_reference < sl):
            diagnostics["bad_levels"] += 1
            continue

        save_ready_setup(conn, pattern, bars15, tp, float(sl))
        diagnostics["ready_created"] += 1
        if existing:
            diagnostics["ready_replaced"] += 1
        try:
            chart = create_signal_chart(symbol, bars15, pattern, None, tp, float(sl),
                                        current_price=prices.get(symbol), chart_title="M15 READY - WAITING FOR M1")
            ready_charts.append({"symbol": symbol, "direction": pattern["direction"], "tp": tp, "sl": float(sl), "chart": chart})
        except Exception:
            LOG.exception("M15 ready chart creation failed for %s", symbol)

    # Stage 2: expire setups and fetch M1 candles only for symbols still waiting.
    now = time.time()
    for setup in active_ready_setups(conn):
        if now - float(setup["created_at"]) > MAX_SIGNAL_AGE_HOURS * 3600:
            finish_ready_setup(conn, str(setup["symbol"]), "EXPIRED")
            diagnostics["ready_expired"] += 1

    waiting = active_ready_setups(conn)
    m1_by_symbol: dict[str, list[dict[str, Any]]] = {}
    if waiting:
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_REQUESTS) as pool:
            futures = {pool.submit(get_candles, str(r["symbol"]), "1m", M1_CANDLE_COUNT): str(r["symbol"]) for r in waiting}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    m1_by_symbol[symbol] = future.result()
                except Exception as exc:
                    diagnostics["m1_errors"] += 1
                    LOG.warning("M1 data error %s: %s", symbol, exc)

    # Refresh tickers immediately before M1-triggered entries so the recorded fill
    # is based on the market price at confirmation time, not the start of this scan.
    if waiting:
        try:
            fresh_tickers = get_tickers()
            tickers_by_symbol.update(ticker_map(fresh_tickers))
            for setup_row in active_ready_setups(conn):
                sym = str(setup_row["symbol"])
                fresh_price = current_price_from_ticker(tickers_by_symbol.get(sym))
                if fresh_price is not None and fresh_price > 0:
                    prices[sym] = fresh_price
        except Exception as exc:
            LOG.warning("Ticker refresh before M1 confirmation failed; using latest cached prices: %s", exc)

    # Stage 3: wait for a complete, fresh six-pivot M1 structure in the same direction.
    for setup in active_ready_setups(conn):
        symbol = str(setup["symbol"])
        direction = str(setup["direction"])
        bars1 = m1_by_symbol.get(symbol)
        bars15 = candles_by_symbol.get(symbol)
        if not bars1 or not bars15:
            continue
        diagnostics["m1_checked"] += 1
        ready_time = float(setup["created_at"])
        confirmed = []
        for m1_pattern in detect_patterns(symbol, bars1, direction, "M1"):
            point_times = [float(bars1[int(m1_pattern["points"][name])]["time"]) / 1000.0
                           for name in ("start", "p1", "p2", "p3", "p4", "p5")]
            if min(point_times) >= ready_time and float(m1_pattern["confirmation_time"]) > ready_time:
                confirmed.append(m1_pattern)
        if not confirmed:
            continue
        confirmed.sort(key=lambda p: float(p["confirmation_time"]), reverse=True)
        m1_confirmation = confirmed[0]
        entry = prices.get(symbol)
        if entry is None or entry <= 0:
            diagnostics["no_price"] += 1
            continue
        entry = float(entry)
        tp = float(setup["tp_price"])
        sl = float(setup["sl_price"])
        valid_levels = (sl < entry < tp) if direction == "LONG" else (tp < entry < sl)
        if not valid_levels:
            # M15 levels are immutable; if price has crossed either level before M1 confirmation,
            # this setup is no longer executable and must not be recalculated at M1.
            finish_ready_setup(conn, symbol, "INVALIDATED")
            diagnostics["bad_levels"] += 1
            continue

        m15_pattern = restore_ready_pattern(setup, bars15)
        if m15_pattern is None:
            diagnostics["m1_errors"] += 1
            continue
        # M15 structure key remains the trade identity; M1 is the confirmation trigger only.
        trade_pattern = dict(m15_pattern)
        trade_pattern["key"] = str(setup["pattern_key"])
        trade_pattern["symbol"] = symbol
        trade_pattern["direction"] = direction
        entry_time = time.time()
        try:
            save_pattern_decision(conn, trade_pattern, "TRADE", "M15 setup confirmed by complete same-direction M1 structure")
            insert_trade(conn, trade_pattern, entry_time, entry, tp, sl)
        except sqlite3.IntegrityError:
            finish_ready_setup(conn, symbol, "TRIGGERED")
            continue
        finish_ready_setup(conn, symbol, "TRIGGERED")
        diagnostics["m1_confirmed"] += 1
        diagnostics["opened"] += 1
        signal = {"symbol": symbol, "direction": direction, "entry": entry, "tp": tp, "sl": sl,
                  "pattern": m15_pattern, "chart": None, "m1_confirmation": m1_confirmation}
        try:
            signal["chart"] = create_signal_chart(symbol, bars15, m15_pattern, entry, tp, sl,
                                                   current_price=prices.get(symbol), chart_title="SIGNAL - M1 CONFIRMED")
        except Exception:
            LOG.exception("Signal chart creation failed for %s", symbol)
        new_signals.append(signal)

    # Keep the current price cache populated for all open trades.
    for row in open_trades(conn):
        symbol = str(row["symbol"])
        if prices.get(symbol) is None:
            prices[symbol] = float(row["entry_price"])

    perf = get_performance(conn, prices)
    ready_now = active_ready_setups(conn)
    stats = {
        "scanned": len(universe), "m15_ok": len(candles_by_symbol), "errors": len(errors_by_symbol),
        "patterns": diagnostics["detected_total"], "universe_size": len(universe),
        "diagnostics": diagnostics, "ready_count": len(ready_now),
    }
    report = make_report(stats, new_signals, closed_trades, perf, prices)
    report += "\n\n⏳ M15 READY / WAITING FOR M1: " + str(len(ready_now))
    for setup in ready_now:
        report += (f"\n• {setup['symbol']} {setup['direction']} | TP {fmt_price(float(setup['tp_price']))}"
                   f" | SL {fmt_price(float(setup['sl_price']))} | expires {dt_text(float(setup['created_at']) + MAX_SIGNAL_AGE_HOURS * 3600)}")
    report += (f"\n\nM1 confirmations checked: {diagnostics['m1_checked']} | confirmed: {diagnostics['m1_confirmed']}"
               f" | new M15 ready: {diagnostics['ready_created']} | replaced: {diagnostics['ready_replaced']}"
               f" | expired: {diagnostics['ready_expired']}")
    LOG.info("\n%s", report)
    telegram_send_message(report)

    # Send the M15 chart exactly when a setup enters/re-enters READY state.
    for item in ready_charts:
        emoji = "🟢" if item["direction"] == "LONG" else "🔴"
        caption = (f"⏳ M15 READY {emoji} {item['direction']} | {item['symbol']}\n"
                   f"TP (fixed from M15): {fmt_price(item['tp'])}\nSL (fixed from M15): {fmt_price(item['sl'])}\n"
                   "Waiting for a complete same-direction six-pivot Heikin-Ashi structure on closed M1 candles.\n"
                   "No entry yet. ENTRY is intentionally not plotted.")
        telegram_send_photo(item["chart"], caption)

    sent_charts = 0
    for signal in new_signals:
        chart_path = signal.get("chart")
        if chart_path:
            direction = signal["direction"]
            emoji = "🟢" if direction == "LONG" else "🔴"
            caption = (f"{emoji} NEW {direction} SIGNAL | {signal['symbol']}\n"
                       f"Entry (M1 confirmation): {fmt_price(signal['entry'])}\n"
                       f"TP (fixed from M15): {fmt_price(signal['tp'])} ({fmt_pct(pct_from_entry(signal['entry'], signal['tp'], direction))})\n"
                       f"SL (fixed from M15): {fmt_price(signal['sl'])} ({fmt_pct(pct_from_entry(signal['entry'], signal['sl'], direction))})\n"
                       "Chart: M15 Heikin-Ashi structure with M1-confirmed ENTRY and unchanged M15 TP/SL.")
            if telegram_send_photo(chart_path, caption):
                sent_charts += 1

    open_charts_sent, open_charts_attempted = send_open_trade_charts(conn, candles_by_symbol, prices)
    LOG.info("Finished in %.1fs | signals=%d | closed=%d | open=%d | ready=%d | ready charts=%d | signal charts=%d | open charts=%d/%d",
             time.time() - started, len(new_signals), len(closed_trades), len(open_trades(conn)), len(ready_now),
             len(ready_charts), sent_charts, open_charts_sent, open_charts_attempted)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
