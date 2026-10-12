# ============================================================
# NDS M5 STRUCTURE SCANNER - VERSION 6.0.0
# PAPER / ALERT ONLY - NO EXCHANGE ORDERS ARE PLACED
#
# File name intentionally remains unchanged so the existing GitHub
# Actions workflow can continue invoking this file without edits.
#
# M5 strategy:
# LONG: two swing highs H1 -> H2 with H2 > H1, followed by two swing
#       lows L1 -> L2 with L2 < L1.
# SHORT: two swing lows L1 -> L2 with L2 < L1, followed by two swing
#        highs H1 -> H2 with H2 > H1.
#
# Only CLOSED M5 candles are used. Alerts identify chart structure only;
# this script does not create paper trades or send exchange orders.
# ============================================================

import os
import time
import math
import sqlite3
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

VERSION = "6.0.0-M5-STRUCTURE"
REAL_TRADING = False
PAPER_ONLY = True

TARGET_ASSETS = 100
TIMEFRAME = "5m"
TIMEFRAME_MINUTES = 5
CANDLES_TO_FETCH = 1500
PIVOT_LEFT = 2
PIVOT_RIGHT = 2
MIN_SWING_CANDLES = 5
MAX_FINAL_PIVOT_AGE_HOURS = 6
MAX_WORKERS = 4
REQUEST_TIMEOUT = 20
MAX_RETRIES = 2
MAX_BID_ASK_SPREAD_PCT = 1.0
MAX_LAST_TRADE_AGE_SECONDS = 300
REQUIRED_SYMBOLS = ("PF_XAUTUSD", "PF_XBTUSD")

KRAKEN_FUTURES_BASE = "https://futures.kraken.com/derivatives/api/v3"
KRAKEN_TICKERS_URL = f"{KRAKEN_FUTURES_BASE}/tickers"
KRAKEN_INSTRUMENTS_URL = f"{KRAKEN_FUTURES_BASE}/instruments"
KRAKEN_CHARTS_BASE = "https://futures.kraken.com/api/charts/v1/trade"

DB_FILE = "nds_m1_v592.db"  # Reuse the DB path already tracked by the existing workflow.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": f"NDS-M5-Structure-Scanner/{VERSION}"})
LIVE_PRICE_SOURCES: Dict[str, str] = {}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: Optional[datetime] = None) -> str:
    value = value or utc_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        try:
            number = float(raw)
            if number > 10_000_000_000:
                number /= 1000.0
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None


def safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def fmt_price(value: Optional[float]) -> str:
    if value is None:
        return "unavailable"
    x = float(value)
    ax = abs(x)
    if ax >= 1000:
        return f"{x:,.2f}"
    if ax >= 100:
        return f"{x:.3f}"
    if ax >= 1:
        return f"{x:.4f}"
    if ax >= 0.01:
        return f"{x:.6f}"
    if ax >= 0.0001:
        return f"{x:.8f}"
    return f"{x:.10f}"


def pct_change(new_value: float, old_value: float) -> float:
    if old_value == 0:
        return 0.0
    return (new_value - old_value) / abs(old_value) * 100.0


def get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < MAX_RETRIES:
                    time.sleep(0.75 * (attempt + 1))
                    continue
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else None
        except Exception as exc:
            if attempt < MAX_RETRIES:
                time.sleep(0.5 * (attempt + 1))
                continue
            print(f"HTTP_ERROR | endpoint={url.rsplit('/', 1)[-1]} | {type(exc).__name__}: {exc}")
    return None


def extract_items(data: Optional[Dict[str, Any]]) -> List[Any]:
    if not isinstance(data, dict):
        return []
    for key in ("tickers", "instruments", "candles", "result", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for subkey in ("tickers", "instruments", "candles", "data"):
                nested = value.get(subkey)
                if isinstance(nested, list):
                    return nested
    return []


def item_symbol(item: Dict[str, Any]) -> Optional[str]:
    value = item.get("symbol") or item.get("ticker") or item.get("product_id") or item.get("instrument")
    return value.strip().upper() if isinstance(value, str) else None


def ticker_volume_score(item: Dict[str, Any]) -> float:
    """Use Kraken's reported 24-hour volume field as the ranking proxy."""
    for field in (
        "vol24h", "volume24h", "volume_24h", "volume24hQuote",
        "volumeQuote", "quoteVolume", "volume",
    ):
        value = safe_float(item.get(field))
        if value is not None and value > 0:
            return value
    open_interest = safe_float(item.get("openInterest") or item.get("open_interest"))
    return open_interest if open_interest is not None and open_interest > 0 else 0.0


def get_target_assets(limit: int = TARGET_ASSETS) -> List[str]:
    instruments_payload = get_json(KRAKEN_INSTRUMENTS_URL)
    ticker_payload = get_json(KRAKEN_TICKERS_URL)

    instrument_symbols = {
        sym
        for item in extract_items(instruments_payload)
        if isinstance(item, dict)
        for sym in [item_symbol(item)]
        if sym and sym.startswith("PF_")
    }
    ticker_rows = [item for item in extract_items(ticker_payload) if isinstance(item, dict)]
    ticker_by_symbol = {
        sym: item for item in ticker_rows
        for sym in [item_symbol(item)]
        if sym and sym.startswith("PF_")
    }

    symbols = instrument_symbols or set(ticker_by_symbol)
    symbols |= set(ticker_by_symbol)
    missing_required = [symbol for symbol in REQUIRED_SYMBOLS if symbol not in symbols]
    if missing_required:
        raise RuntimeError("Required Kraken Futures symbol(s) not found: " + ", ".join(missing_required))

    ranked = sorted(
        (symbol for symbol in symbols if symbol not in REQUIRED_SYMBOLS),
        key=lambda symbol: (
            -ticker_volume_score(ticker_by_symbol.get(symbol, {})),
            symbol,
        ),
    )
    desired = max(len(REQUIRED_SYMBOLS), int(limit))
    selected = ranked[: max(0, desired - len(REQUIRED_SYMBOLS))] + list(REQUIRED_SYMBOLS)
    selected = list(dict.fromkeys(selected))
    if len(selected) < min(desired, len(symbols)):
        for symbol in sorted(symbols):
            if symbol not in selected:
                selected.append(symbol)
            if len(selected) >= min(desired, len(symbols)):
                break
    selected = selected[: min(desired, len(symbols))]

    print(
        f"ASSET_SELECTION | selected={len(selected)} | requested={desired} | "
        f"ranking=Kraken-reported-24h-volume | XAUT={'PF_XAUTUSD' in selected} | "
        f"BTC={'PF_XBTUSD' in selected}"
    )
    return selected


def parse_candle(item: Any) -> Optional[Dict[str, Any]]:
    if isinstance(item, dict):
        timestamp = item.get("time") or item.get("timestamp")
        open_value = item.get("open")
        high_value = item.get("high")
        low_value = item.get("low")
        close_value = item.get("close")
    elif isinstance(item, (list, tuple)) and len(item) >= 5:
        timestamp, open_value, high_value, low_value, close_value = item[:5]
    else:
        return None

    candle_time = parse_time(timestamp)
    open_value = safe_float(open_value)
    high_value = safe_float(high_value)
    low_value = safe_float(low_value)
    close_value = safe_float(close_value)
    if candle_time is None or None in (open_value, high_value, low_value, close_value):
        return None
    if high_value < low_value:
        return None
    return {
        "time": candle_time,
        "open": open_value,
        "high": high_value,
        "low": low_value,
        "close": close_value,
    }


def candle_is_closed(candle: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    now = now or utc_now()
    candle_time = parse_time(candle.get("time"))
    return bool(candle_time and candle_time + timedelta(minutes=TIMEFRAME_MINUTES) <= now)


def fetch_ohlc(symbol: str, candle_count: int = CANDLES_TO_FETCH) -> List[Dict[str, Any]]:
    end_dt = utc_now()
    start_dt = end_dt - timedelta(minutes=TIMEFRAME_MINUTES * (candle_count + 30))
    url = f"{KRAKEN_CHARTS_BASE}/{symbol}/{TIMEFRAME}"
    cursor_s = int(start_dt.timestamp())
    end_s = int(end_dt.timestamp())
    by_timestamp: Dict[int, Dict[str, Any]] = {}

    # The API may cap each response, so page forward until the requested window is covered.
    for _ in range(8):
        payload = get_json(url, {"from": cursor_s, "to": end_s})
        raw_candles = payload.get("candles") if isinstance(payload, dict) else None
        if not isinstance(raw_candles, list) or not raw_candles:
            break

        page_timestamps: List[int] = []
        for item in raw_candles:
            candle = parse_candle(item)
            if candle is None:
                continue
            ts_ms = int(candle["time"].timestamp() * 1000)
            by_timestamp[ts_ms] = candle
            page_timestamps.append(ts_ms)
        if not page_timestamps:
            break

        last_seconds = max(page_timestamps) // 1000
        next_cursor = last_seconds + TIMEFRAME_MINUTES * 60
        if next_cursor <= cursor_s or next_cursor >= end_s:
            break
        cursor_s = next_cursor

    rows = [by_timestamp[key] for key in sorted(by_timestamp)]
    rows = [row for row in rows if candle_is_closed(row, end_dt)]
    return rows[-max(100, int(candle_count)):]


def is_pivot_high(rows: List[Dict[str, Any]], i: int) -> bool:
    if i < PIVOT_LEFT or i + PIVOT_RIGHT >= len(rows):
        return False
    value = rows[i]["high"]
    window = [rows[j]["high"] for j in range(i - PIVOT_LEFT, i + PIVOT_RIGHT + 1)]
    left_values = [rows[j]["high"] for j in range(i - PIVOT_LEFT, i)]
    return value >= max(window) and value > max(left_values)


def is_pivot_low(rows: List[Dict[str, Any]], i: int) -> bool:
    if i < PIVOT_LEFT or i + PIVOT_RIGHT >= len(rows):
        return False
    value = rows[i]["low"]
    window = [rows[j]["low"] for j in range(i - PIVOT_LEFT, i + PIVOT_RIGHT + 1)]
    left_values = [rows[j]["low"] for j in range(i - PIVOT_LEFT, i)]
    return value <= min(window) and value < min(left_values)


def build_ordered_pivots(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    pivots: List[Dict[str, Any]] = []
    for i in range(len(rows)):
        high_pivot = is_pivot_high(rows, i)
        low_pivot = is_pivot_low(rows, i)
        if not high_pivot and not low_pivot:
            continue

        if high_pivot and low_pivot:
            upper_leg = rows[i]["high"] - rows[i]["open"]
            lower_leg = rows[i]["open"] - rows[i]["low"]
            pivot_type = "H" if upper_leg >= lower_leg else "L"
        else:
            pivot_type = "H" if high_pivot else "L"

        price = rows[i]["high"] if pivot_type == "H" else rows[i]["low"]
        node = {"idx": i, "time": rows[i]["time"], "price": price, "type": pivot_type}

        # Keep the more extreme point when two same-type pivots occur in a row.
        if pivots and pivots[-1]["type"] == pivot_type:
            previous = pivots[-1]
            more_extreme = price >= previous["price"] if pivot_type == "H" else price <= previous["price"]
            if more_extreme:
                pivots[-1] = node
        else:
            pivots.append(node)
    return pivots


def valid_pair_ends(
    pivots: List[Dict[str, Any]],
    pivot_type: str,
    second_is_more_extreme,
) -> List[Tuple[int, Dict[str, Any], Dict[str, Any]]]:
    """Return valid same-type pairs as (second index, first node, second node)."""
    nodes = [node for node in pivots if node["type"] == pivot_type]
    result: List[Tuple[int, Dict[str, Any], Dict[str, Any]]] = []
    for second_pos, second in enumerate(nodes):
        for first in reversed(nodes[:second_pos]):
            if second["idx"] - first["idx"] < MIN_SWING_CANDLES:
                continue
            if second_is_more_extreme(second["price"], first["price"]):
                result.append((second["idx"], first, second))
                break
    return result


def latest_pair_ending_before(
    pairs: List[Tuple[int, Dict[str, Any], Dict[str, Any]]],
    max_end_idx: int,
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    if not pairs:
        return None
    end_indices = [item[0] for item in pairs]
    position = bisect_right(end_indices, max_end_idx) - 1
    if position < 0:
        return None
    _, first, second = pairs[position]
    return first, second


def find_long_structure(
    pivots: List[Dict[str, Any]], now: Optional[datetime] = None
) -> Optional[Dict[str, Any]]:
    """HH pair first, then a lower-low pair. Final pivot is L2."""
    now = now or utc_now()
    lows = [node for node in pivots if node["type"] == "L"]
    highs = [node for node in pivots if node["type"] == "H"]
    higher_high_pairs = valid_pair_ends(pivots, "H", lambda second, first: second > first)

    for low2_pos in range(len(lows) - 1, -1, -1):
        low2 = lows[low2_pos]
        final_time = parse_time(low2["time"])
        if final_time is None:
            continue
        if (now - final_time).total_seconds() > MAX_FINAL_PIVOT_AGE_HOURS * 3600:
            break

        for low1 in reversed(lows[:low2_pos]):
            if low2["idx"] - low1["idx"] < MIN_SWING_CANDLES:
                continue
            if low2["price"] >= low1["price"]:
                continue
            high_pair = latest_pair_ending_before(
                higher_high_pairs,
                low1["idx"] - MIN_SWING_CANDLES,
            )
            if high_pair is None:
                continue
            high1, high2 = high_pair
            if not (high1["idx"] < high2["idx"] < low1["idx"] < low2["idx"]):
                continue
            return {
                "direction": "LONG",
                "trend_1": high1,
                "trend_2": high2,
                "pullback_1": low1,
                "pullback_2": low2,
                "final": low2,
            }
    return None


def find_short_structure(
    pivots: List[Dict[str, Any]], now: Optional[datetime] = None
) -> Optional[Dict[str, Any]]:
    """LL pair first, then a higher-high pair. Final pivot is H2."""
    now = now or utc_now()
    highs = [node for node in pivots if node["type"] == "H"]
    lows = [node for node in pivots if node["type"] == "L"]
    lower_low_pairs = valid_pair_ends(pivots, "L", lambda second, first: second < first)

    for high2_pos in range(len(highs) - 1, -1, -1):
        high2 = highs[high2_pos]
        final_time = parse_time(high2["time"])
        if final_time is None:
            continue
        if (now - final_time).total_seconds() > MAX_FINAL_PIVOT_AGE_HOURS * 3600:
            break

        for high1 in reversed(highs[:high2_pos]):
            if high2["idx"] - high1["idx"] < MIN_SWING_CANDLES:
                continue
            if high2["price"] <= high1["price"]:
                continue
            low_pair = latest_pair_ending_before(
                lower_low_pairs,
                high1["idx"] - MIN_SWING_CANDLES,
            )
            if low_pair is None:
                continue
            low1, low2 = low_pair
            if not (low1["idx"] < low2["idx"] < high1["idx"] < high2["idx"]):
                continue
            return {
                "direction": "SHORT",
                "trend_1": low1,
                "trend_2": low2,
                "pullback_1": high1,
                "pullback_2": high2,
                "final": high2,
            }
    return None


def detect_structure(symbol: str, rows: List[Dict[str, Any]], now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    now = now or utc_now()
    closed_rows = [row for row in rows if candle_is_closed(row, now)]
    if len(closed_rows) < 30:
        return None
    pivots = build_ordered_pivots(closed_rows)
    if len(pivots) < 6:
        return None

    candidates = [
        pattern for pattern in (
            find_long_structure(pivots, now),
            find_short_structure(pivots, now),
        ) if pattern is not None
    ]
    if not candidates:
        return None
    # Only report the most recently completed structure if both directions exist.
    pattern = max(candidates, key=lambda item: item["final"]["time"])
    pattern["symbol"] = symbol
    pattern["pivot_count"] = len(pivots)
    pattern["candle_count"] = len(closed_rows)
    pattern["pattern_key"] = make_pattern_key(pattern)
    return pattern


def make_pattern_key(pattern: Dict[str, Any]) -> str:
    nodes = (
        pattern["trend_1"], pattern["trend_2"],
        pattern["pullback_1"], pattern["pullback_2"],
    )
    key_nodes = "|".join(
        f"{node['type']}:{utc_iso(node['time'])}:{float(node['price']):.12g}"
        for node in nodes
    )
    return f"{pattern['symbol']}|{pattern['direction']}|{key_nodes}"


def get_live_prices(symbols: List[str]) -> Dict[str, float]:
    payload = get_json(KRAKEN_TICKERS_URL)
    LIVE_PRICE_SOURCES.clear()
    wanted = set(symbols)
    prices: Dict[str, float] = {}
    if not payload:
        print("LIVE_PRICE_ERROR | Kraken Futures ticker response unavailable")
        return prices

    now = utc_now()
    for item in extract_items(payload):
        if not isinstance(item, dict):
            continue
        symbol = item_symbol(item)
        if not symbol or symbol not in wanted:
            continue

        bid = safe_float(item.get("bid"))
        ask = safe_float(item.get("ask"))
        mark = safe_float(item.get("markPrice") or item.get("mark_price"))
        last = safe_float(item.get("last"))
        price = None
        source = ""
        if bid is not None and ask is not None and bid > 0 and ask >= bid:
            mid = (bid + ask) / 2.0
            spread_pct = (ask - bid) / mid * 100.0 if mid > 0 else float("inf")
            if spread_pct <= MAX_BID_ASK_SPREAD_PCT:
                price, source = mid, "bid/ask midpoint"
        if price is None and mark is not None and mark > 0:
            price, source = mark, "mark price fallback"
        if price is None and last is not None and last > 0:
            last_time = parse_time(item.get("lastTime") or item.get("last_time"))
            if last_time is not None and (now - last_time).total_seconds() <= MAX_LAST_TRADE_AGE_SECONDS:
                price, source = last, "recent last trade fallback"

        if price is not None and price > 0:
            prices[symbol] = price
            LIVE_PRICE_SOURCES[symbol] = source
    return prices


def price_source_label(symbol: str) -> str:
    source = LIVE_PRICE_SOURCES.get(symbol)
    if source == "bid/ask midpoint":
        return "Kraken bid/ask midpoint"
    if source == "mark price fallback":
        return "Kraken mark price"
    if source == "recent last trade fallback":
        return "recent Kraken last trade"
    return "live price unavailable"


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=20)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sent_patterns (
            pattern_key TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            final_pivot_time TEXT NOT NULL,
            sent_at TEXT NOT NULL
        )"""
    )
    conn.commit()
    return conn


def previously_sent(pattern_key: str) -> bool:
    conn = db_connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM sent_patterns WHERE pattern_key = ? LIMIT 1",
            (pattern_key,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def mark_pattern_sent(pattern: Dict[str, Any]) -> None:
    conn = db_connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sent_patterns(pattern_key, symbol, direction, final_pivot_time, sent_at) VALUES (?, ?, ?, ?, ?)",
            (
                pattern["pattern_key"], pattern["symbol"], pattern["direction"],
                utc_iso(pattern["final"]["time"]), utc_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def telegram_send(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_NOT_CONFIGURED | report follows in Actions log")
        print(text)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        response = SESSION.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and payload.get("ok") is True:
            return True
        print("TELEGRAM_SEND_ERROR | API returned ok=false")
    except Exception as exc:
        # Do not print the token-bearing request URL.
        print(f"TELEGRAM_SEND_ERROR | {type(exc).__name__}: {exc}")
    return False


def format_pattern(pattern: Dict[str, Any], prices: Dict[str, float]) -> str:
    symbol = pattern["symbol"]
    direction = pattern["direction"]
    trend1 = pattern["trend_1"]
    trend2 = pattern["trend_2"]
    pb1 = pattern["pullback_1"]
    pb2 = pattern["pullback_2"]
    current = prices.get(symbol)
    final_time = parse_time(pattern["final"]["time"])
    final_stamp = utc_iso(final_time) if final_time else "unknown"

    if direction == "LONG":
        trend_pct = pct_change(trend2["price"], trend1["price"])
        pullback_pct = pct_change(pb2["price"], pb1["price"])
        lines = [
            f"🟢 LONG STRUCTURE | {symbol}",
            f"Higher highs: H1 {fmt_price(trend1['price'])} → H2 {fmt_price(trend2['price'])} ({trend_pct:+.3f}%)",
            f"Lower lows: L1 {fmt_price(pb1['price'])} → L2 {fmt_price(pb2['price'])} ({pullback_pct:+.3f}%)",
        ]
    else:
        trend_pct = pct_change(trend2["price"], trend1["price"])
        pullback_pct = pct_change(pb2["price"], pb1["price"])
        lines = [
            f"🔴 SHORT STRUCTURE | {symbol}",
            f"Lower lows: L1 {fmt_price(trend1['price'])} → L2 {fmt_price(trend2['price'])} ({trend_pct:+.3f}%)",
            f"Higher highs: H1 {fmt_price(pb1['price'])} → H2 {fmt_price(pb2['price'])} ({pullback_pct:+.3f}%)",
        ]

    lines.extend([
        f"Current: {fmt_price(current)} ({price_source_label(symbol)})",
        f"Final pivot: {final_stamp}",
        "Alert only: no orders, entry, TP, or SL are created.",
    ])
    return "\n".join(lines)


def split_messages(header: str, signal_blocks: List[str], limit: int = 3500) -> List[Tuple[str, List[str]]]:
    """Split Telegram reports under the safe message length and track included patterns."""
    chunks: List[Tuple[str, List[str]]] = []
    current_blocks: List[str] = []
    current_length = len(header) + 2
    for block in signal_blocks:
        needed = len(block) + 2
        if current_blocks and current_length + needed > limit:
            chunks.append((header, current_blocks))
            current_blocks = []
            current_length = len(header) + 2
        current_blocks.append(block)
        current_length += needed
    if current_blocks or not chunks:
        chunks.append((header, current_blocks))
    return chunks


def scan_symbol(symbol: str) -> Tuple[str, int, Optional[Dict[str, Any]], Optional[str]]:
    try:
        rows = fetch_ohlc(symbol)
        if len(rows) < 30:
            return symbol, len(rows), None, "insufficient_candles"
        pattern = detect_structure(symbol, rows)
        return symbol, len(rows), pattern, None
    except Exception as exc:
        return symbol, 0, None, f"{type(exc).__name__}: {exc}"


def main() -> None:
    started = utc_now()
    print(f"NDS_M5_STRUCTURE_START | version={VERSION} | time={utc_iso(started)}")
    print("MODE | ALERT_ONLY=True | REAL_TRADING=False | PAPER_ONLY=True")
    print(
        "STRATEGY | LONG=HH_then_LL | SHORT=LL_then_HH | "
        f"TIMEFRAME={TIMEFRAME} | ASSETS={TARGET_ASSETS} | "
        f"MIN_SWING_CANDLES={MIN_SWING_CANDLES} | FINAL_PIVOT_MAX_AGE_HOURS={MAX_FINAL_PIVOT_AGE_HOURS}"
    )

    try:
        symbols = get_target_assets(TARGET_ASSETS)
    except Exception as exc:
        message = f"NDS M5 STRUCTURE SCAN ERROR\nTime: {utc_iso()}\nAsset selection failed: {type(exc).__name__}: {exc}"
        telegram_send(message)
        raise

    total = len(symbols)
    successes = 0
    failures = 0
    raw_patterns: List[Dict[str, Any]] = []
    candle_counts: Dict[str, int] = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(scan_symbol, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol, candle_count, pattern, error = future.result()
            candle_counts[symbol] = candle_count
            if error:
                failures += 1
                print(f"SCAN_SYMBOL_ERROR | symbol={symbol} | error={error}")
            else:
                successes += 1
            if pattern:
                raw_patterns.append(pattern)
                print(
                    f"STRUCTURE_FOUND | symbol={symbol} | direction={pattern['direction']} | "
                    f"final={pattern['final']['price']} | final_time={utc_iso(pattern['final']['time'])}"
                )

    prices = get_live_prices(symbols)
    raw_patterns.sort(key=lambda item: (item["final"]["time"], item["symbol"]), reverse=True)

    current_count = len(raw_patterns)
    new_patterns = [pattern for pattern in raw_patterns if not previously_sent(pattern["pattern_key"])]
    long_count = sum(1 for pattern in raw_patterns if pattern["direction"] == "LONG")
    short_count = sum(1 for pattern in raw_patterns if pattern["direction"] == "SHORT")
    ended = utc_now()
    elapsed = (ended - started).total_seconds()

    header = (
        "🔎 NDS M5 STRUCTURE SCAN\n"
        f"Version: {VERSION}\n"
        f"Time (UTC): {utc_iso(ended)}\n"
        f"Universe: {total} Kraken Futures contracts, ranked by reported 24h volume\n"
        f"Scanned with usable candles: {successes}/{total} | Data errors/short data: {failures}\n"
        f"Fresh structures: {current_count} (LONG {long_count} / SHORT {short_count})\n"
        f"New alerts this run: {len(new_patterns)} | Runtime: {elapsed:.1f}s\n"
        "Rules: LONG = higher highs then lower lows; SHORT = lower lows then higher highs.\n"
        "Uses closed M5 candles only. Structure alerts are not trade orders."
    )

    if new_patterns:
        blocks = [format_pattern(pattern, prices) for pattern in new_patterns]
        chunks = split_messages(header, blocks)
        # A pattern is marked as sent only after its Telegram message succeeds.
        cursor = 0
        for message_header, chunk_blocks in chunks:
            message = message_header + ("\n\n" + "\n\n".join(chunk_blocks) if chunk_blocks else "")
            sent = telegram_send(message)
            if sent:
                for pattern in new_patterns[cursor:cursor + len(chunk_blocks)]:
                    mark_pattern_sent(pattern)
                cursor += len(chunk_blocks)
            else:
                break
    else:
        # Compact diagnostics are useful to confirm the scheduled scan is alive,
        # without replaying old symbol-level alerts into Telegram.
        telegram_send(header + "\n\nNo new structures to alert on this run.")

    print(
        f"NDS_M5_STRUCTURE_DONE | scanned={total} | usable={successes} | failures={failures} | "
        f"fresh={current_count} | new={len(new_patterns)} | long={long_count} | short={short_count} | "
        f"runtime_seconds={elapsed:.1f}"
    )


if __name__ == "__main__":
    main()
