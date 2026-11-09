#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NDS M5 XAUUSD Single-Timeframe Structure Scanner v3.0.0

LONG + SHORT mirrored signal/paper-trade scanner. It never places real exchange orders.

Strategy:
  - Scan PF_XAUTUSD only.
  - Detect the seven-point structure on M5 only.
  - A paper signal is issued as soon as a valid M5 structure is confirmed.
  - LONG START must be the lowest of all seven structural points.
  - Entry, TP, SL and trade monitoring all use M5 candles.
  - TP=H3/L3 and SL=F1 from the M5 structure.

Reporting:
  - Sends a PNG chart for each new signal to Telegram.
  - Reports new signals, open trades with entry/TP/SL and percentage distances,
    closed-trade win rate, realized PnL, floating PnL, and total PnL.
  - PnL is gross, equal-notional percentage-point PnL (fees/slippage excluded).

Environment variables:
  TELEGRAM_BOT_TOKEN or TELEGRAM_TOKEN
  TELEGRAM_CHAT_ID or TELEGRAM_CHAT
  UNIVERSE_SIZE (default 100)
  MAX_OPEN_TRADES (default 3)
  MAX_SIGNAL_AGE_HOURS (default 72)
  STOP_BUFFER_PCT (legacy setting; retained but not applied)
  DB_PATH (default nds_xauusd_m5_single_tf_v3.db)
  CHART_DIR (default nds_xauusd_m5_single_tf_v3_charts)
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

VERSION = "3.0.0"
FUTURES_REST = "https://futures.kraken.com/derivatives/api/v3"
CHARTS_REST = "https://futures.kraken.com/api/charts/v1"
TELEGRAM_API = "https://api.telegram.org"

UNIVERSE_SIZE = 1
MAX_OPEN_TRADES = max(1, int(os.getenv("MAX_OPEN_TRADES", "3")))
MAX_SIGNAL_AGE_HOURS = max(1, int(os.getenv("MAX_SIGNAL_AGE_HOURS", "72")))
M5_CANDLE_COUNT = 3000
# Pivot detection uses Heikin Ashi color reversal plus 5 candles on each side.
# The pivot is confirmed only after all 5 right-side candles have closed.
PIVOT_LEFT = 5
PIVOT_RIGHT = 5
STOP_BUFFER_PCT = max(0.0, float(os.getenv("STOP_BUFFER_PCT", "0.10")))
MAX_CONCURRENT_REQUESTS = 8
MAX_CHARTS_PER_RUN = 8
REQUIRED_SYMBOLS = ("PF_XAUTUSD",)
# Separate database prevents old M1/M30 trades from being interpreted as M5 trades.
DB_PATH = Path(os.getenv("DB_PATH", "nds_xauusd_m5_single_tf_v3.db"))
CHART_DIR = Path(os.getenv("CHART_DIR", "nds_xauusd_m5_single_tf_v3_charts"))
REPORT_TZ_NAME = os.getenv("REPORT_TIMEZONE", "Europe/Berlin")
try:
    REPORT_TZ = ZoneInfo(REPORT_TZ_NAME)
except Exception:
    REPORT_TZ = timezone.utc

M5_MS = 5 * 60 * 1000
TIMEFRAME_MS = {"5m": M5_MS}
TIMEFRAME_LABEL = {"5m": "M5"}
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": f"NDS-XAUUSD-M5-Scanner/{VERSION}", "Accept": "application/json"})

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
LOG = logging.getLogger("nds_m5_long_short")


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
    available = {str(t.get("symbol", "")).upper() for t in tickers}
    if "PF_XAUTUSD" not in available:
        raise RuntimeError("Kraken Futures ticker list does not contain PF_XAUTUSD")
    return ["PF_XAUTUSD"]


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
    timeframe_ms = TIMEFRAME_MS.get(resolution, M5_MS)
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


def get_candles_history_from(
    symbol: str, resolution: str, start_time_ms: int, page_size: int = 4500
) -> list[dict[str, Any]]:
    """Fetch closed candles from an exact historical timestamp through now.

    Kraken's chart endpoint supports from/to/count. The request is paged into
    windows smaller than its documented 5000-candle ceiling so older Structure 1
    pivots remain available for open-trade chart regeneration.
    """
    timeframe_ms = TIMEFRAME_MS.get(resolution, M5_MS)
    timeframe_seconds = timeframe_ms // 1000
    page_size = max(100, min(int(page_size), 4500))
    cursor = max(0, int(start_time_ms // 1000))
    end_time = int(time.time())
    by_time: dict[int, dict[str, Any]] = {}
    pages = 0

    while cursor < end_time and pages < 20:
        page_end = min(end_time, cursor + timeframe_seconds * (page_size - 1))
        params = {"from": cursor, "to": page_end, "count": page_size}
        payload = request_json(f"{CHARTS_REST}/trade/{symbol}/{resolution}", params=params)
        rows = payload.get("candles", [])
        if not isinstance(rows, list):
            raise RuntimeError(f"Invalid historical candles shape for {symbol} {resolution}")

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
            if ts_i + timeframe_ms > now_ms:
                continue
            if low > high or min(opn, high, low, close) <= 0:
                continue
            by_time[ts_i] = {
                "time": ts_i, "open": float(opn), "high": float(high),
                "low": float(low), "close": float(close),
                "volume": as_float(row.get("volume")) or 0.0,
            }

        pages += 1
        if page_end >= end_time:
            break
        # Move to the next candle boundary; one second avoids inclusive-boundary
        # repeats while retaining the next full candle.
        cursor = page_end + 1

    return [by_time[key] for key in sorted(by_time)]


def heiken_ashi_candles(candles: list[dict[str, Any]]) -> list[dict[str, float]]:
    """Build Heikin Ashi OHLC values used for swing-pivot geometry."""
    result: list[dict[str, float]] = []
    previous_open: Optional[float] = None
    previous_close: Optional[float] = None
    for candle in candles:
        ha_close = (float(candle["open"]) + float(candle["high"]) + float(candle["low"]) + float(candle["close"])) / 4.0
        ha_open = ((float(candle["open"]) + float(candle["close"])) / 2.0) if previous_open is None else ((previous_open + float(previous_close)) / 2.0)
        result.append({
            "open": ha_open,
            "close": ha_close,
            "high": max(float(candle["high"]), ha_open, ha_close),
            "low": min(float(candle["low"]), ha_open, ha_close),
        })
        previous_open, previous_close = ha_open, ha_close
    return result


def is_pivot_low(candles: list[dict[str, Any]], index: int, left: int, right: int, ha: Optional[list[dict[str, float]]] = None) -> bool:
    if index < left or index + right >= len(candles):
        return False
    source = ha if ha is not None else heiken_ashi_candles(candles)
    value = source[index]["low"]
    return value < min(source[j]["low"] for j in range(index - left, index)) and value < min(source[j]["low"] for j in range(index + 1, index + right + 1))


def is_pivot_high(candles: list[dict[str, Any]], index: int, left: int, right: int, ha: Optional[list[dict[str, float]]] = None) -> bool:
    if index < left or index + right >= len(candles):
        return False
    source = ha if ha is not None else heiken_ashi_candles(candles)
    value = source[index]["high"]
    return value > max(source[j]["high"] for j in range(index - left, index)) and value > max(source[j]["high"] for j in range(index + 1, index + right + 1))


def heiken_ashi_colors(candles: list[dict[str, Any]]) -> list[int]:
    """Return HA direction for each candle: 1=green, -1=red, 0=neutral."""
    colors: list[int] = []
    previous_ha_open: Optional[float] = None
    previous_ha_close: Optional[float] = None
    for candle in candles:
        ha_close = (
            float(candle["open"]) + float(candle["high"])
            + float(candle["low"]) + float(candle["close"])
        ) / 4.0
        if previous_ha_open is None or previous_ha_close is None:
            ha_open = (float(candle["open"]) + float(candle["close"])) / 2.0
        else:
            ha_open = (previous_ha_open + previous_ha_close) / 2.0
        if ha_close > ha_open:
            colors.append(1)
        elif ha_close < ha_open:
            colors.append(-1)
        else:
            colors.append(0)
        previous_ha_open = ha_open
        previous_ha_close = ha_close
    return colors


def find_confirmed_pivots(
    candles: list[dict[str, Any]], left: int, right: int
) -> tuple[list[int], list[int]]:
    """Find confirmed OHLC pivots using 5x5 (or supplied) confirmation and HA reversal at the pivot.

    Pivot low: the pivot candle itself changes HA from RED to GREEN.
    Pivot high: the pivot candle itself changes HA from GREEN to RED.
    The pivot price is still the real market low/high, not the synthetic HA price.
    """
    lows: list[int] = []
    highs: list[int] = []
    ha_colors = heiken_ashi_colors(candles)
    ha_candles = heiken_ashi_candles(candles)
    for i in range(max(left, 1), len(candles) - right):
        turns_green = ha_colors[i - 1] == -1 and ha_colors[i] == 1
        turns_red = ha_colors[i - 1] == 1 and ha_colors[i] == -1
        if turns_green and is_pivot_low(candles, i, left, right, ha_candles):
            lows.append(i)
        if turns_red and is_pivot_high(candles, i, left, right, ha_candles):
            highs.append(i)
    return lows, highs


# ----------------------------- Strategy detection -----------------------------

def pattern_key(symbol: str, candles: list[dict[str, Any]], points: dict[str, int], direction: str, timeframe: str = "5m") -> str:
    names = ("start", "h1", "f1", "h2", "f2", "h3", "f3")
    timestamps = [str(candles[points[name]]["time"]) for name in names]
    return f"{direction}|{symbol}|{TIMEFRAME_LABEL[timeframe]}|" + "|".join(timestamps)


def _detect_raw_structures(symbol: str, candles: list[dict[str, Any]], direction: str, timeframe: str = "5m") -> list[dict[str, Any]]:
    """Detect one seven-point HA structure; START is 5x5, later points are HA color-run extremes."""
    if len(candles) < PIVOT_LEFT + PIVOT_RIGHT + 20:
        return []
    ha = heiken_ashi_candles(candles)
    colors = heiken_ashi_colors(candles)
    start_indices: list[int] = []
    for i in range(PIVOT_LEFT, len(candles) - PIVOT_RIGHT):
        if direction == "LONG":
            if is_pivot_low(candles, i, PIVOT_LEFT, PIVOT_RIGHT, ha):
                start_indices.append(i)
        else:
            if is_pivot_high(candles, i, PIVOT_LEFT, PIVOT_RIGHT, ha):
                start_indices.append(i)

    patterns: list[dict[str, Any]] = []
    for start_i in start_indices:
        # Ignore neutral HA candles while detecting color runs. A run is complete
        # only when the opposite non-neutral color appears on a closed candle.
        expected = 1 if direction == "LONG" else -1
        runs: list[dict[str, int]] = []
        run_color = expected
        run_start: Optional[int] = None
        run_end: Optional[int] = None
        for i in range(start_i + 1, len(candles)):
            color = colors[i]
            if color == 0:
                continue
            if run_start is None:
                if color != expected:
                    continue
                run_start = i
                run_end = i
                continue
            if color == run_color:
                run_end = i
                continue
            # The previous color run is now confirmed by this closed reversal candle.
            assert run_start is not None and run_end is not None
            runs.append({"start": run_start, "end": run_end, "reverse": i})
            if len(runs) == 6:
                break
            run_color = color
            run_start = i
            run_end = i
        if len(runs) != 6:
            continue
        # First six alternating completed runs must have the exact expected colors.
        required = ([1, -1, 1, -1, 1, -1] if direction == "LONG"
                    else [-1, 1, -1, 1, -1, 1])
        if [expected if n % 2 == 0 else -expected for n in range(6)] != required:
            continue
        names = ("h1", "f1", "h2", "f2", "h3", "f3")
        points: dict[str, int] = {"start": start_i}
        prices: dict[str, float] = {}
        for n, (name, run) in enumerate(zip(names, runs)):
            a, b = run["start"], run["end"]
            if direction == "LONG":
                use_high = (n % 2 == 0)
                vals = [ha[j]["high"] if use_high else ha[j]["low"] for j in range(a, b + 1)]
                extreme = max(vals) if use_high else min(vals)
            else:
                use_high = (n % 2 == 1)
                vals = [ha[j]["high"] if use_high else ha[j]["low"] for j in range(a, b + 1)]
                extreme = max(vals) if use_high else min(vals)
            # First occurrence of the run's extreme is the structural point.
            extreme_i = next(j for j in range(a, b + 1)
                             if (ha[j]["high"] if use_high else ha[j]["low"]) == extreme)
            points[name] = extreme_i
            prices[name] = float(extreme)
        start_price = float(ha[start_i]["low"] if direction == "LONG" else ha[start_i]["high"])
        h1, f1, h2, f2, h3, f3 = (prices[k] for k in names)
        if direction == "LONG":
            # START must be the absolute lowest of all seven structural points.
            start_is_lowest = start_price <= min(h1, f1, h2, f2, h3, f3)
            valid = (start_is_lowest and h2 > h1 and h3 > h2
                     and f2 > f1 and f3 < f2 and f3 > f1)
            tp, sl = h3, f1
        else:
            # For SHORT, H-named legs are lows (L1/L2/L3); F-named legs are highs.
            l1, sf1, l2, sf2, l3, sf3 = (prices[k] for k in names)
            valid = l2 < l1 and l3 < l2 and sf2 < sf1 and sf3 > sf2 and sf3 < sf1
            tp, sl = l3, sf1
        if not valid:
            continue
        # The reversal candle after F3 is the first confirmation-color candle.
        confirmation_i = int(runs[-1]["reverse"])
        interval_ms = TIMEFRAME_MS[timeframe]
        confirmation_time = (int(candles[confirmation_i]["time"]) + interval_ms) / 1000.0
        point_prices = {"start": start_price, **prices}
        patterns.append({
            "symbol": symbol, "direction": direction, "points": points,
            "key": pattern_key(symbol, candles, points, direction, timeframe),
            "timeframe": TIMEFRAME_LABEL[timeframe],
            "resolution": timeframe,
            "confirmation_time": confirmation_time,
            "terminal_time": candles[points["f3"]]["time"] / 1000.0,
            "point_prices": point_prices,
            "tp_price": float(tp), "sl_price": float(sl),
            "start_price": start_price,
            "p1_price": prices["h1"], "p2_price": prices["f1"],
            "p3_price": prices["h2"], "p4_price": prices["h3"],
            "p5_price": prices["f3"],
        })
    # Multiple START pivots may parse the same sequence. Deduplicate exact keys.
    unique = {item["key"]: item for item in patterns}
    result = list(unique.values())
    result.sort(key=lambda item: item["confirmation_time"], reverse=True)
    return result


def paired_pattern_key(*args: Any, **kwargs: Any) -> str:
    """Legacy helper retained for compatibility with older saved records."""
    symbol, candles, first_points, second_points, direction = args
    names = ("start", "p1", "p2", "p3", "p4", "p5")
    first_times = [str(candles[first_points[name]]["time"]) for name in names]
    second_times = [str(candles[second_points[name]]["time"]) for name in names]
    return f"{direction}|{symbol}|M5|" + "|".join(first_times + second_times)


def detect_patterns(symbol: str, candles: list[dict[str, Any]], direction: str, timeframe: str = "5m") -> list[dict[str, Any]]:
    return _detect_raw_structures(symbol, candles, direction, timeframe)


def find_nearest_valid_m5_low(candles15: list[dict[str, Any]], entry: float) -> Optional[float]:
    """Closest intact confirmed M5 swing low below entry, with a small buffer applied later."""
    lows, _ = find_confirmed_pivots(candles15, PIVOT_LEFT, PIVOT_RIGHT)
    eligible = []
    for i in lows:
        price = candles15[i]["low"]
        if price < entry and not any(b["low"] <= price for b in candles15[i + 1:]):
            eligible.append((price, i))
    return max(eligible, key=lambda item: item[0])[0] if eligible else None


def find_nearest_valid_m5_high(candles15: list[dict[str, Any]], entry: float) -> Optional[float]:
    """Closest intact confirmed M5 swing high above entry, with a small buffer applied later."""
    _, highs = find_confirmed_pivots(candles15, PIVOT_LEFT, PIVOT_RIGHT)
    eligible = []
    for i in highs:
        price = candles15[i]["high"]
        if price > entry and not any(b["high"] >= price for b in candles15[i + 1:]):
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
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
    if "direction" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN direction TEXT NOT NULL DEFAULT 'LONG'")
    conn.commit()
    return conn


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
    """All trades in this strategy are managed from closed M5 candles."""
    return "5m", M5_MS, "M5"


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
    next_bar_time_ms = ((entry_ms // M5_MS) + 1) * M5_MS
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
    pts = pattern["points"]
    direction = str(pattern["direction"])
    ha = heiken_ashi_candles(candles)
    new_names = ("start", "h1", "f1", "h2", "f2", "h3", "f3")
    legacy = not all(name in pts for name in new_names)
    names = ("start", "p1", "p2", "p3", "p4", "p5") if legacy else new_names
    all_indices = [int(pts[n]) for n in names if n in pts]
    left_edge = max(0, min(all_indices, default=0) - 18)
    right_edge = min(len(candles), max(all_indices, default=len(candles)-1) + 35)
    if right_edge - left_edge < 20:
        left_edge = max(0, right_edge - 120)
    chart_candles = candles[left_edge:right_edge]
    fig, ax = plt.subplots(figsize=(14, 7.5), dpi=140)
    colors = heiken_ashi_colors(candles)
    for x, c in enumerate(chart_candles):
        i = left_edge + x
        color = "#18a558" if colors[i] == 1 else ("#e74c3c" if colors[i] == -1 else "#7f8c8d")
        o, h, l, cl = ha[i]["open"], ha[i]["high"], ha[i]["low"], ha[i]["close"]
        ax.vlines(x, l, h, color=color, linewidth=1.0, zorder=2)
        body_low = min(o, cl)
        body_h = max(abs(cl-o), abs(cl)*0.000002)
        ax.add_patch(Rectangle((x-0.31, body_low), 0.62, body_h, facecolor=color, edgecolor=color, linewidth=0.6, zorder=3))

    if legacy:
        if direction == "LONG":
            specs = {"start":("START","#277da1","o","high"),"p1":("1 RED","#db2b39","v","low"),"p2":("1 GREEN","#149447","^","high"),"p3":("2 RED","#db2b39","v","low"),"p4":("2 GREEN / TP","#149447","^","high"),"p5":("3 RED / END","#db2b39","v","low")}
        else:
            specs = {"start":("START","#277da1","o","low"),"p1":("1 GREEN","#149447","^","high"),"p2":("1 RED","#db2b39","v","low"),"p3":("2 GREEN","#149447","^","high"),"p4":("2 RED / TP","#db2b39","v","low"),"p5":("3 GREEN / END","#149447","^","high")}
    else:
        if direction == "LONG":
            specs = {"start":("START","#277da1","o","low"),"h1":("H1","#149447","^","high"),"f1":("F1 / SL","#db2b39","v","low"),"h2":("H2","#149447","^","high"),"f2":("F2","#db2b39","v","low"),"h3":("H3 / TP","#149447","^","high"),"f3":("F3","#db2b39","v","low")}
        else:
            specs = {"start":("START","#277da1","o","high"),"h1":("L1","#db2b39","v","low"),"f1":("F1 / SL","#149447","^","high"),"h2":("L2","#db2b39","v","low"),"f2":("F2","#149447","^","high"),"h3":("L3 / TP","#db2b39","v","low"),"f3":("F3","#149447","^","high")}
    xs, ys = [], []
    for name in names:
        if name not in pts:
            continue
        idx = int(pts[name]); x = idx-left_edge
        if not (0 <= x < len(chart_candles)):
            continue
        label, color, marker, side = specs[name]
        price = pattern.get("point_prices", {}).get(name)
        if price is None:
            price = ha[idx][side]
        y = float(price)
        xs.append(x); ys.append(y)
        ax.scatter([x],[y],s=100,marker=marker,color=color,edgecolors="black",linewidths=0.6,zorder=8)
        offset = 18 if marker in ("^","o") else -24
        ax.annotate(label,(x,y),xytext=(0,offset),textcoords="offset points",fontsize=8,color=color,fontweight="bold",ha="center",va="bottom" if offset>0 else "top",bbox={"boxstyle":"round,pad=0.2","fc":"white","ec":color,"alpha":0.9,"lw":0.7})
    if len(xs) >= 2:
        ax.plot(xs,ys,color="#202a35",linewidth=1.8,alpha=0.9,zorder=4,label="Seven-point HA structure" if not legacy else "Legacy structure")
    if tp is not None: ax.axhline(tp,color="#149447",linestyle="--",linewidth=1.3,label=f"TP {fmt_price(tp)}")
    if sl is not None: ax.axhline(sl,color="#db2b39",linestyle="--",linewidth=1.3,label=f"SL {fmt_price(sl)}")
    last_x=len(chart_candles)-1
    if entry is not None:
        ax.axhline(entry,color="#277da1",linestyle=":",linewidth=1.5,label=f"ENTRY {fmt_price(entry)}")
        ax.scatter([last_x],[entry],color="#277da1",marker="D",s=68,edgecolors="white",linewidths=0.7,zorder=9)
    if current_price is not None and current_price>0:
        ax.axhline(current_price,color="#8e44ad",linestyle="-.",linewidth=1.35,label=f"NOW {fmt_price(current_price)}")
    n=min(9,len(chart_candles)); ticks=sorted(set(round(i*(len(chart_candles)-1)/max(1,n-1)) for i in range(n)))
    ax.set_xticks(ticks)
    ax.set_xticklabels([datetime.fromtimestamp(chart_candles[i]["time"]/1000,timezone.utc).astimezone(REPORT_TZ).strftime("%m-%d %H:%M") for i in ticks],rotation=25,ha="right",fontsize=8)
    ax.set_title(f"NDS {pattern.get('timeframe', 'M5')} {direction} | {symbol} | {chart_title}\nHA seven-point structure | {dt_text(pattern['confirmation_time'])}",fontsize=11,fontweight="bold")
    ax.set_ylabel("Heikin-Ashi price"); ax.grid(True,alpha=0.2); ax.legend(loc="best",fontsize=8,framealpha=0.9); ax.margins(x=0.03,y=0.12)
    fig.tight_layout(); safe="".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in symbol)
    path=CHART_DIR/f"{safe}_{pattern.get('timeframe', 'M5')}_{direction}_{time.time_ns()}.png"; fig.savefig(path,bbox_inches="tight"); plt.close(fig)
    return path


def restore_trade_pattern(trade: sqlite3.Row, candles: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Restore seven-point new keys and legacy six/twelve-pivot keys for charting."""
    parts = str(trade["pattern_key"]).split("|")
    if len(parts) not in (9, 15, 10) or parts[0] not in ("LONG", "SHORT"):
        LOG.warning("Cannot restore structure for trade %s: invalid pattern_key", trade["id"])
        return None
    try:
        if len(parts) == 10:
            timestamps=[int(v) for v in parts[3:10]]
            names=("start","h1","f1","h2","f2","h3","f3")
        elif len(parts) == 15:
            timestamps=[int(v) for v in parts[9:15]]
            names=("start","p1","p2","p3","p4","p5")
        else:
            timestamps=[int(v) for v in parts[3:9]]
            names=("start","p1","p2","p3","p4","p5")
    except (TypeError,ValueError):
        return None
    index_by_time={int(c["time"]):i for i,c in enumerate(candles)}
    if not all(ts in index_by_time for ts in timestamps):
        LOG.warning("Cannot restore structure for open trade %s (%s); chart skipped",trade["id"],trade["symbol"])
        return None
    points=dict(zip(names,[index_by_time[ts] for ts in timestamps]))
    _resolution, interval_ms, timeframe_label=trade_candle_settings(trade)
    return {"symbol":str(trade["symbol"]),"direction":str(trade["direction"]),"points":points,"timeframe":timeframe_label,"confirmation_time":(timestamps[-1]+interval_ms)/1000.0}


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
        bars = candles_by_symbol.get(symbol) if timeframe_label == "M5" else None
        if not bars:
            try:
                bars = get_candles(symbol, resolution, M5_CANDLE_COUNT)
                if timeframe_label == "M5":
                    candles_by_symbol[symbol] = bars
            except Exception as exc:
                LOG.warning("Cannot fetch chart candles for open trade %s: %s", trade["id"], exc)
                continue
        if len(bars) < 10:
            LOG.warning("Not enough candles to chart open trade %s", trade["id"])
            continue

        # Legacy multi-structure keys are retained only for safe compatibility with old records.
        key_parts = str(trade["pattern_key"]).split("|")
        if len(key_parts) == 15 and key_parts[2] == "M5":
            try:
                first_start_ms = int(key_parts[3])
                if first_start_ms < int(bars[0]["time"]):
                    extended_start_ms = max(0, first_start_ms - 12 * M5_MS)
                    extended_bars = get_candles_history_from(symbol, resolution, extended_start_ms)
                    if extended_bars:
                        bars = extended_bars
                        candles_by_symbol[symbol] = bars
                        LOG.info(
                            "Expanded chart history for trade %s: %d candles from %s",
                            trade["id"], len(bars), dt_text(bars[0]["time"] / 1000.0)
                        )
            except Exception as exc:
                LOG.warning(
                    "Could not expand history for Structure 1 on trade %s: %s",
                    trade["id"], exc
                )

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
                "Chart: seven-point HA structure; TP=H3/L3 and SL=F1"
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
        bars = candles_by_symbol.get(symbol) if timeframe_label == "M5" else None
        if bars is None:
            try:
                bars = get_candles(symbol, resolution, M5_CANDLE_COUNT)
                if timeframe_label == "M5":
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
            # Conservative resolution if both levels are touched in one M5 candle.
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
    lines=[f"🔎 NDS M5 LONG + SHORT v{VERSION} | {dt_text(time.time())}",f"Scan: {scan_stats['scanned']} symbols | M5 OK {scan_stats['m5_ok']} | errors {scan_stats['errors']} | seven-point structures {scan_stats['patterns']}",f"Universe: {scan_stats['universe_size']} | Open slots: {perf['open_count']}/{MAX_OPEN_TRADES}",""]
    diagnostics = scan_stats.get("diagnostics", {})
    if diagnostics:
        lines.append("🧭 SIGNAL DIAGNOSTICS")
        lines.append(
            f"Detected: {diagnostics.get('detected_total', scan_stats.get('patterns', 0))} | "
            f"Fresh candidates: {diagnostics.get('candidates', 0)} | "
            f"Already traded: {diagnostics.get('already_seen', 0)} | "
            f"Expired: {diagnostics.get('expired', 0)}"
        )
        for key, label in (("no_price", "No current price"), ("bad_tp", "TP on wrong side of entry"), ("symbol_open", "Symbol already has open trade"), ("slots_full", "Open-trade limit reached"), ("sl_missing", "F1 HA stop invalid"), ("bad_levels", "Invalid SL/entry/TP order"), ("candle_refresh_error", "Failed to refresh M5 candles"), ("opened", "Paper trades opened")):
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

def scan_timeframe(symbol: str, resolution: str, limit: int) -> tuple[str, str, Optional[list[dict[str, Any]]], Optional[str]]:
    try:
        return symbol, resolution, get_candles(symbol, resolution, limit), None
    except Exception as exc:
        return symbol, resolution, None, str(exc)


def _fresh_structure(conn: sqlite3.Connection, pattern: dict[str, Any]) -> bool:
    row = conn.execute("SELECT 1 FROM patterns WHERE pattern_key=?", (pattern["key"],)).fetchone()
    return row is None


def _chart_caption(pattern: dict[str, Any], label: str) -> str:
    direction = str(pattern["direction"])
    emoji = "🟢" if direction == "LONG" else "🔴"
    return (
        f"{emoji} M5 STRUCTURE CONFIRMED | {pattern['symbol']} | {direction}\n"
        f"Confirmed: {dt_text(float(pattern['confirmation_time']))}\n"
        f"{label}: single-timeframe M5 structure."
    )


def main() -> int:
    started = time.time()
    conn = connect_db()
    try:
        tickers = get_tickers()
        universe = get_universe(tickers)
    except Exception as exc:
        message = f"❌ NDS XAUUSD M5 scanner startup error.\n{exc}"
        LOG.exception("Startup/ticker fetch failed")
        telegram_send_message(message)
        conn.close()
        return 1

    symbol = "PF_XAUTUSD"
    tickers_by_symbol = ticker_map(tickers)
    prices: dict[str, Optional[float]] = {symbol: current_price_from_ticker(tickers_by_symbol.get(symbol))}
    try:
        bars = get_candles(symbol, "5m", M5_CANDLE_COUNT)
    except Exception as exc:
        LOG.exception("M5 candle fetch failed")
        telegram_send_message(f"❌ XAUUSD M5 scan failed: {exc}")
        conn.close()
        return 1

    if len(bars) < PIVOT_LEFT + PIVOT_RIGHT + 20:
        telegram_send_message(f"❌ XAUUSD M5 scan incomplete: only {len(bars)} closed candles received.")
        conn.close()
        return 1
    if prices[symbol] is None:
        prices[symbol] = float(bars[-1]["close"])
    current = prices[symbol]

    patterns: list[dict[str, Any]] = []
    chart_events: list[tuple[dict[str, Any], Path]] = []
    for direction in ("LONG", "SHORT"):
        found = detect_patterns(symbol, bars, direction, "5m")
        patterns.extend(found)
        # Send a chart for each newly confirmed direction's latest structure.
        if found:
            pattern = found[0]
            if _fresh_structure(conn, pattern):
                try:
                    chart = create_signal_chart(
                        symbol, bars, pattern, None, None, None,
                        current_price=current, chart_title=f"M5 {direction} STRUCTURE CONFIRMED",
                    )
                    chart_events.append((pattern, chart))
                except Exception:
                    LOG.exception("M5 structure chart failed: %s", direction)

    now = time.time()
    max_age = MAX_SIGNAL_AGE_HOURS * 3600
    candidates = [p for p in patterns if 0 <= now - p["confirmation_time"] <= max_age]
    candidates.sort(key=lambda p: p["confirmation_time"], reverse=True)

    new_signals: list[dict[str, Any]] = []
    diagnostics = {"structures": len(patterns), "opened": 0, "already_seen": 0,
                   "bad_levels": 0, "slots_full": 0}
    for pattern in candidates:
        if pattern_seen(conn, pattern["key"]):
            diagnostics["already_seen"] += 1
            continue
        if len(open_trades(conn)) >= MAX_OPEN_TRADES:
            diagnostics["slots_full"] += 1
            save_pattern_decision(conn, pattern, "SKIPPED", "Maximum number of open paper trades reached")
            continue
        # Entry and all risk levels are taken from the same closed M5 timeframe.
        entry = float(bars[-1]["close"])
        tp = float(pattern["tp_price"])
        sl = float(pattern["sl_price"])
        direction = str(pattern["direction"])
        valid = (sl < entry < tp) if direction == "LONG" else (tp < entry < sl)
        if not valid:
            diagnostics["bad_levels"] += 1
            save_pattern_decision(conn, pattern, "SKIPPED", "M5 levels are not correctly ordered around current entry")
            continue
        save_pattern_decision(conn, pattern, "TRADE", "Single confirmed M5 structure; M5 trade levels")
        insert_trade(conn, pattern, time.time(), entry, tp, sl)
        diagnostics["opened"] += 1
        signal = {"symbol": symbol, "direction": direction, "entry": entry,
                  "tp": tp, "sl": sl, "pattern": pattern, "chart": None}
        try:
            signal["chart"] = create_signal_chart(
                symbol, bars, pattern, entry, tp, sl,
                current_price=current, chart_title="CONFIRMED M5 SIGNAL (M5 LEVELS)",
            )
        except Exception:
            LOG.exception("M5 signal chart creation failed")
        new_signals.append(signal)
        break

    closed_trades = update_open_trades(conn, {symbol: bars}, prices)
    perf = get_performance(conn, prices)
    report_lines = [
        f"🔎 NDS XAUUSD M5 SINGLE-TF v{VERSION} | {dt_text(time.time())}",
        f"Symbol: {symbol} only | M5 closed candles: {len(bars)}",
        f"Confirmed M5 structures: {diagnostics['structures']}",
        f"New paper signals: {diagnostics['opened']} | Open trades: {perf['open_count']}/{MAX_OPEN_TRADES}",
        "",
    ]
    for signal in new_signals:
        d = signal["direction"]
        report_lines.append(
            f"{'🟢' if d == 'LONG' else '🔴'} NEW {d} | {symbol} | Entry(M5) {fmt_price(signal['entry'])} "
            f"| TP(M5) {fmt_price(signal['tp'])} ({fmt_pct(pct_from_entry(signal['entry'], signal['tp'], d))}) "
            f"| SL(M5) {fmt_price(signal['sl'])} ({fmt_pct(pct_from_entry(signal['entry'], signal['sl'], d))})"
        )
    if not new_signals:
        report_lines.append("No new M5 signal. A single confirmed M5 structure is required.")
    if closed_trades:
        report_lines.append("")
        for trade in closed_trades:
            report_lines.append(
                f"CLOSED {trade['direction']} {trade['status']} | Entry {fmt_price(trade['entry'])} "
                f"| Exit(M5) {fmt_price(trade['exit'])} | PnL {fmt_pct(trade['pnl_pct'])}"
            )
    report_lines.extend([
        "", f"OPEN TRADES: {perf['open_count']}",
        f"Closed: {perf['closed_count']} | W/L: {perf['wins']}/{perf['losses']} | Win rate: {perf['win_rate']:.2f}%",
        f"Realized PnL: {fmt_pct(perf['realized_pct'])} | Floating PnL: {fmt_pct(perf['floating_pct'])} | Total: {fmt_pct(perf['total_pct'])}",
        "Structure detection, entry, TP, SL and trade monitoring use M5 only. Paper mode only; no real orders.",
    ])
    report = "\n".join(report_lines)
    LOG.info("\n%s", report)
    telegram_send_message(report)

    charts_sent = 0
    for pattern, chart_path in chart_events:
        if telegram_send_photo(chart_path, _chart_caption(pattern, "M5 structure confirmed")):
            charts_sent += 1
            save_pattern_decision(conn, pattern, "CHART", "M5 structure chart sent")

    for signal in new_signals:
        chart = signal.get("chart")
        if chart:
            d = signal["direction"]
            caption = (
                f"{'🟢' if d == 'LONG' else '🔴'} NEW {d} SIGNAL | {symbol}\n"
                f"Single M5 structure confirmed\nEntry (M5): {fmt_price(signal['entry'])}\n"
                f"TP (M5): {fmt_price(signal['tp'])}\nSL (M5): {fmt_price(signal['sl'])}\n"
                "All signal and trade levels use M5 candles."
            )
            telegram_send_photo(chart, caption)

    open_charts_sent, open_charts_attempted = send_open_trade_charts(conn, {symbol: bars}, prices)
    LOG.info("Finished in %.1fs | M5 structure charts=%d | signals=%d | open charts=%d/%d",
             time.time() - started, charts_sent, len(new_signals), open_charts_sent, open_charts_attempted)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
