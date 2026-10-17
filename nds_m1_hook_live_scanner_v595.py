# ============================================================
# NDS M5 10-NODE STRUCTURE SCANNER - VERSION 6.4.0
# PAPER TRADING ONLY: NEVER SENDS REAL EXCHANGE ORDERS
# SHORT: START-L-N1-H-S1-L-F1-H-S2-L-N2-H-S3-L-F2-H-S4-L-F3-H
# LONG is the exact mirror. The three upward/downward legs must match within <2%.
# Entry is F3; TP is 86.4% retracement from START to F3; SL is half the entry-to-TP distance.
# START is confirmed as the low/high over five closed candles on each side.
# The existing workflow filename, DB filename, and dependencies are intentionally retained.
# ============================================================

import os
import time
import math
import json
import sqlite3
import traceback
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

VERSION = "6.4.0"
REAL_TRADING = False
PAPER_ONLY = True

TARGET_ASSETS = 100
REQUIRED_SYMBOLS = ("PF_XAUTUSD", "PF_XBTUSD")
M5_INTERVAL = "5m"
M1_INTERVAL = "1m"
M5_MINUTES = 5
M1_MINUTES = 1
M5_CANDLES = 1500
M1_CANDLES = 360
START_LOOKBACK_CANDLES = 5  # START must be the extreme among 5 candles before and 5 after
START_CONFIRM_CANDLES = 5
MIN_LEG_DISTANCE_PCT = 0.75  # minimum size of each of the three matched legs
LEG_EQUALITY_TOLERANCE_PCT = 2.0  # max-min leg size must be strictly below 2% of the smallest leg
RETRACE_TP_PCT = 86.4
STOP_TO_TP_DISTANCE_RATIO = 0.5
PIVOT_LEFT = 2
PIVOT_RIGHT = 2
MIN_NODE_CANDLES = 5
CCI_PERIOD = 20
CCI_LEVEL = 100.0
CCI_CROSS_MAX_AGE_MINUTES = 15  # accommodates a 5-minute workflow cadence
MAX_OPEN_TRADES = 3
MAX_BID_ASK_SPREAD_PCT = 1.0
MAX_LAST_TRADE_AGE_SECONDS = 300
MAX_PATTERN_AGE_HOURS = 12
MAX_WORKERS = 8
REQUEST_TIMEOUT = 20
TELEGRAM_TIMEOUT = 30

KRAKEN_FUTURES_BASE = "https://futures.kraken.com/derivatives/api/v3"
KRAKEN_TICKERS_URL = f"{KRAKEN_FUTURES_BASE}/tickers"
KRAKEN_INSTRUMENTS_URL = f"{KRAKEN_FUTURES_BASE}/instruments"
KRAKEN_CHARTS_BASE = "https://futures.kraken.com/api/charts/v1/trade"

# Keep the DB filename used by the existing workflow. New table names prevent
# schema collisions with prior NDS versions in that same SQLite file.
DB_FILE = "nds_m1_v592.db"
CHART_DIR = "nds_m5_cci_f1_f2_charts"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": f"NDS-M5-10-Node-Scanner/{VERSION}", "Accept": "application/json"})
LIVE_PRICE_SOURCES: Dict[str, str] = {}
LAST_TICKERS: Dict[str, Dict[str, Any]] = {}
DIAG: Dict[str, int] = {}
STRATEGY_EPOCH: Optional[datetime] = None


def reset_diag() -> None:
    DIAG.clear()
    for key in (
        "assets", "m5_ok", "m5_errors", "m5_candles", "pivots", "structures",
        "reject_spacing", "reject_start_extreme", "reject_geometry", "reject_leg_imbalance",
        "reject_leg_distance", "reject_invalid_levels", "reject_stale_structure", "reject_pre_strategy",
        "signals_new", "signals_duplicate", "entries_opened", "entries_blocked", "trades_tp", "trades_sl",
    ):
        DIAG[key] = 0


def dinc(key: str, amount: int = 1) -> None:
    DIAG[key] = DIAG.get(key, 0) + amount


reset_diag()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(dt: Optional[datetime] = None) -> str:
    dt = dt or utc_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        x = float(value)
        if x > 10_000_000_000:
            x /= 1000.0
        try:
            return datetime.fromtimestamp(x, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
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
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
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
        return "-"
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
    if not old_value:
        return 0.0
    return (new_value / old_value - 1.0) * 100.0


def trade_pnl_pct(direction: str, entry: float, current: float) -> float:
    if entry <= 0:
        return 0.0
    return ((current - entry) / entry * 100.0) if direction == "LONG" else ((entry - current) / entry * 100.0)


def get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    try:
        response = SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        print(f"HTTP_JSON_ERROR | url={url} | {type(exc).__name__}: {exc}")
        return None


def extract_items(payload: Dict[str, Any]) -> List[Any]:
    for key in ("tickers", "instruments", "candles", "result", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for subkey in ("tickers", "instruments", "data"):
                nested = value.get(subkey)
                if isinstance(nested, list):
                    return nested
    return []


def item_symbol(item: Dict[str, Any]) -> Optional[str]:
    value = item.get("symbol") or item.get("ticker") or item.get("product_id") or item.get("instrument")
    return value.strip().upper() if isinstance(value, str) and value.strip() else None


def volume_rank_value(item: Dict[str, Any]) -> float:
    """Prefer quote-currency notional volume; fall back to base volume x last price."""
    for key in ("volumeQuote", "volume_quote", "quoteVolume", "quote_volume", "turnover24h", "turnover_24h", "vol24hUsd", "vol24hUSD"):
        val = safe_float(item.get(key))
        if val is not None and val >= 0:
            return val
    for key in ("vol24h", "volume24h", "volume_24h", "volume"):
        val = safe_float(item.get(key))
        if val is not None and val >= 0:
            price = safe_float(item.get("last") or item.get("markPrice") or item.get("mark_price")) or 0.0
            return val * price
    return 0.0


def get_market_tickers() -> Dict[str, Dict[str, Any]]:
    payload = get_json(KRAKEN_TICKERS_URL)
    if not payload:
        raise RuntimeError("Kraken Futures ticker endpoint returned no data")
    tickers: Dict[str, Dict[str, Any]] = {}
    for item in extract_items(payload):
        if not isinstance(item, dict):
            continue
        symbol = item_symbol(item)
        if symbol and symbol.startswith("PF_"):
            tickers[symbol] = item
    if not tickers:
        raise RuntimeError("No PF_ Kraken Futures contracts were returned by ticker endpoint")
    LAST_TICKERS.clear()
    LAST_TICKERS.update(tickers)
    return tickers


def get_target_assets(tickers: Dict[str, Dict[str, Any]], limit: int = TARGET_ASSETS) -> List[str]:
    # Rank by 24h quote/notional volume, with volume * last-price as fallback.
    # Preserve the user's required XAUT and BTC contracts even if outside top 100.
    ranked = sorted(tickers.keys(), key=lambda sym: volume_rank_value(tickers[sym]), reverse=True)
    required_present = [s for s in REQUIRED_SYMBOLS if s in tickers]
    if len(required_present) < len(REQUIRED_SYMBOLS):
        missing = sorted(set(REQUIRED_SYMBOLS) - set(required_present))
        raise RuntimeError("Required Kraken Futures contract(s) not present: " + ", ".join(missing))
    others = [s for s in ranked if s not in REQUIRED_SYMBOLS]
    target = max(int(limit), len(REQUIRED_SYMBOLS))
    selected = required_present + others[:target - len(required_present)]
    selected = list(dict.fromkeys(selected))
    print("ASSET_SELECTION | " + json.dumps({
        "requested": target,
        "selected": len(selected),
        "ranking": "24h quote volume / estimated USD notional",
        "required": list(REQUIRED_SYMBOLS),
        "top5_by_volume": [
            {"symbol": s, "volume_score": round(volume_rank_value(tickers[s]), 2)} for s in ranked[:5]
        ],
    }))
    return selected


def get_live_prices(symbols: List[str], tickers: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, float]:
    data = tickers or get_market_tickers()
    wanted = set(symbols)
    LIVE_PRICE_SOURCES.clear()
    prices: Dict[str, float] = {}
    now = utc_now()
    for symbol in wanted:
        item = data.get(symbol)
        if not item:
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
        else:
            print(f"LIVE_PRICE_UNAVAILABLE | symbol={symbol}")
    return prices


def live_price_label(symbol: str) -> str:
    source = LIVE_PRICE_SOURCES.get(symbol)
    if source == "bid/ask midpoint":
        return "Kraken bid/ask mid"
    if source == "mark price fallback":
        return "Kraken mark"
    if source == "recent last trade fallback":
        return "recent last"
    return "unavailable"


def normalize_candle(item: Any) -> Optional[Dict[str, Any]]:
    if isinstance(item, dict):
        t = item.get("time")
        o, h, low, c = item.get("open"), item.get("high"), item.get("low"), item.get("close")
        v = item.get("volume")
    elif isinstance(item, (list, tuple)) and len(item) >= 5:
        t, o, h, low, c = item[:5]
        v = item[5] if len(item) > 5 else 0
    else:
        return None
    dt = parse_time(t)
    o, h, low, c = safe_float(o), safe_float(h), safe_float(low), safe_float(c)
    if dt is None or None in (o, h, low, c) or h < low:
        return None
    return {"time": dt, "open": o, "high": h, "low": low, "close": c, "volume": safe_float(v) or 0.0}


def fetch_ohlc(symbol: str, interval: str, candle_count: int) -> List[Dict[str, Any]]:
    minutes = {M5_INTERVAL: M5_MINUTES, M1_INTERVAL: M1_MINUTES}.get(interval)
    if minutes is None:
        raise ValueError(f"Unsupported candle interval: {interval}")
    end_dt = utc_now()
    start_dt = end_dt - timedelta(minutes=minutes * (candle_count + 30))
    url = f"{KRAKEN_CHARTS_BASE}/{symbol}/{interval}"
    cursor_s, end_s = int(start_dt.timestamp()), int(end_dt.timestamp())
    rows_by_ms: Dict[int, Dict[str, Any]] = {}
    # The charts endpoint can paginate the range, so move forward using the last returned candle.
    for page in range(8):
        payload = get_json(url, {"from": cursor_s, "to": end_s, "count": min(candle_count, 1000)})
        raw = payload.get("candles") if isinstance(payload, dict) else None
        if not isinstance(raw, list) or not raw:
            break
        times: List[int] = []
        for item in raw:
            candle = normalize_candle(item)
            if candle is None:
                continue
            stamp_ms = int(candle["time"].timestamp() * 1000)
            rows_by_ms[stamp_ms] = candle
            times.append(stamp_ms)
        if not times:
            break
        next_cursor = max(times) // 1000 + minutes * 60
        if next_cursor <= cursor_s or next_cursor >= end_s:
            break
        cursor_s = next_cursor
        # A page smaller than the practical page size normally means the range is complete.
        if len(raw) < min(candle_count, 1000):
            more = payload.get("more_candles") if isinstance(payload, dict) else False
            if not more:
                break
        if page == 7:
            print(f"OHLC_PAGE_LIMIT | symbol={symbol} | interval={interval} | rows={len(rows_by_ms)}")
    rows = [rows_by_ms[k] for k in sorted(rows_by_ms)]
    now = utc_now()
    rows = [r for r in rows if r["time"] + timedelta(minutes=minutes) <= now]
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
        ph, pl = is_pivot_high(rows, i), is_pivot_low(rows, i)
        if not ph and not pl:
            continue
        if ph and pl:
            ptype = "H" if rows[i]["high"] - rows[i]["open"] >= rows[i]["open"] - rows[i]["low"] else "L"
        else:
            ptype = "H" if ph else "L"
        price = rows[i]["high"] if ptype == "H" else rows[i]["low"]
        node = {"idx": i, "time": rows[i]["time"], "price": price, "type": ptype}
        if pivots and pivots[-1]["type"] == ptype:
            previous = pivots[-1]
            more_extreme = price >= previous["price"] if ptype == "H" else price <= previous["price"]
            if more_extreme:
                pivots[-1] = node
        else:
            pivots.append(node)
    # Avoid adjacent same-type pivots with impossible spacing from noisy candles.
    return pivots


def node_spacing_ok(nodes: List[Dict[str, Any]]) -> bool:
    return all((b["idx"] - a["idx"]) >= MIN_NODE_CANDLES for a, b in zip(nodes, nodes[1:]))


def pattern_key(pattern: Dict[str, Any]) -> str:
    """Stable identity for a fully confirmed 10-node structure."""
    pieces = [pattern["symbol"], pattern["direction"]]
    for name in ("start", "n1", "s1", "f1", "s2", "n2", "s3", "f2", "s4", "f3"):
        node = pattern[name]
        pieces.append(f"{name}:{utc_iso(node['time'])}:{float(node['price']):.12g}")
    return "|".join(pieces)


def is_start_extreme(rows: List[Dict[str, Any]], idx: int, kind: str) -> bool:
    """START is the strict low/high across five candles before and after it."""
    left = START_LOOKBACK_CANDLES
    right = START_CONFIRM_CANDLES
    if idx < left or idx + right >= len(rows):
        return False
    if kind == "L":
        value = rows[idx]["low"]
        neighbors = [rows[j]["low"] for j in range(idx - left, idx + right + 1) if j != idx]
        return value < min(neighbors)
    value = rows[idx]["high"]
    neighbors = [rows[j]["high"] for j in range(idx - left, idx + right + 1) if j != idx]
    return value > max(neighbors)


def levels_for_structure(direction: str, start: float, f3: float) -> Optional[Tuple[float, float, float]]:
    """Return entry=F3, TP=86.4% retracement of START→F3, and half-distance SL."""
    if start <= 0 or f3 <= 0:
        return None
    distance = (f3 - start) if direction == "SHORT" else (start - f3)
    if distance <= 0:
        return None
    entry = f3
    retrace = distance * (RETRACE_TP_PCT / 100.0)
    if direction == "SHORT":
        tp = entry - retrace
        sl = entry + retrace * STOP_TO_TP_DISTANCE_RATIO
        if not (0 < tp < entry < sl):
            return None
    else:
        tp = entry + retrace
        sl = entry - retrace * STOP_TO_TP_DISTANCE_RATIO
        if not (0 < sl < entry < tp):
            return None
    return entry, sl, tp


def detect_structures(symbol: str, rows: List[Dict[str, Any]], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Detect the exact 10-node NDS sequence using closed M5 candles only.

    SHORT nodes: START(L), N1(H), S1(L), F1(H), S2(L), N2(H), S3(L),
    F2(H), S4(L), F3(H). Enforced relations: N1>START, F1>N1, S2<S1,
    F2>F1, S4<S3, and F3>START. LONG is the mirrored sequence.

    Matched legs are START→F1, S2→F2, and S4→F3. Their max/min size ratio
    must differ by strictly less than 2%; each leg must also be >=0.75% of
    its origin node. F3 is actionable only after its pivot is confirmed by
    closed candles, and only patterns confirmed after this strategy's epoch
    can be reported/traded.
    """
    now = now or utc_now()
    if len(rows) < max(40, START_LOOKBACK_CANDLES + START_CONFIRM_CANDLES + 10):
        return []
    pivots = build_ordered_pivots(rows)
    dinc("pivots", len(pivots))
    candidates: List[Dict[str, Any]] = []
    expected_short = ["L", "H", "L", "H", "L", "H", "L", "H", "L", "H"]
    expected_long = ["H", "L", "H", "L", "H", "L", "H", "L", "H", "L"]

    for pos in range(0, len(pivots) - 9):
        seq = pivots[pos:pos + 10]
        types = [node["type"] for node in seq]
        if types not in (expected_short, expected_long):
            dinc("reject_geometry")
            continue
        if not node_spacing_ok(seq):
            dinc("reject_spacing")
            continue
        start, n1, s1, f1, s2, n2, s3, f2, s4, f3 = seq
        is_short = types == expected_short

        if not is_start_extreme(rows, start["idx"], "L" if is_short else "H"):
            dinc("reject_start_extreme")
            continue

        if is_short:
            # Required upward-structure shape before the short reversal.
            geometry_ok = (
                start["price"] < n1["price"]
                and f1["price"] > n1["price"]
                and s2["price"] < s1["price"]
                and f2["price"] > f1["price"]
                and s4["price"] < s3["price"]
                and f3["price"] > start["price"]
            )
            direction = "SHORT"
            legs = (
                f1["price"] - start["price"],
                f2["price"] - s2["price"],
                f3["price"] - s4["price"],
            )
            leg_origins = (start["price"], s2["price"], s4["price"])
        else:
            # Exact mirror for the long setup in a downward structure.
            geometry_ok = (
                start["price"] > n1["price"]
                and f1["price"] < n1["price"]
                and s2["price"] > s1["price"]
                and f2["price"] < f1["price"]
                and s4["price"] > s3["price"]
                and f3["price"] < start["price"]
            )
            direction = "LONG"
            legs = (
                start["price"] - f1["price"],
                s2["price"] - f2["price"],
                s4["price"] - f3["price"],
            )
            leg_origins = (start["price"], s2["price"], s4["price"])

        if not geometry_ok or any(leg <= 0 for leg in legs):
            dinc("reject_geometry")
            continue

        # Equal-length legs: max/min must be strictly under 2% difference.
        smallest, largest = min(legs), max(legs)
        if smallest <= 0 or ((largest - smallest) / smallest * 100.0) >= LEG_EQUALITY_TOLERANCE_PCT:
            dinc("reject_leg_imbalance")
            continue

        leg_pcts = [leg / origin * 100.0 if origin > 0 else 0.0 for leg, origin in zip(legs, leg_origins)]
        if any(pct < MIN_LEG_DISTANCE_PCT for pct in leg_pcts):
            dinc("reject_leg_distance")
            continue

        # The F3 pivot is not confirmed until its right-side closed candles exist.
        confirm_idx = f3["idx"] + PIVOT_RIGHT
        if confirm_idx >= len(rows):
            continue
        confirmation_time = rows[confirm_idx]["time"] + timedelta(minutes=M5_MINUTES)
        if (now - confirmation_time).total_seconds() > MAX_PATTERN_AGE_HOURS * 3600:
            dinc("reject_stale_structure")
            continue
        if STRATEGY_EPOCH is not None and confirmation_time < STRATEGY_EPOCH:
            dinc("reject_pre_strategy")
            continue

        levels = levels_for_structure(direction, start["price"], f3["price"])
        if levels is None:
            dinc("reject_invalid_levels")
            continue
        entry, sl, tp = levels
        final = dict(f3)
        candidate = {
            "symbol": symbol, "direction": direction,
            "start": start, "n1": n1, "s1": s1, "f1": f1, "s2": s2,
            "n2": n2, "s3": s3, "f2": f2, "s4": s4, "f3": f3,
            "final": final, "entry_price": entry, "sl_price": sl, "tp_price": tp,
            "leg_lengths": [float(x) for x in legs],
            "leg_diff_pct": (largest - smallest) / smallest * 100.0,
            "confirmed_at": utc_iso(confirmation_time),
            "pivots": pivots, "rows": rows,
        }
        candidate["pattern_key"] = pattern_key(candidate)
        candidates.append(candidate)

    unique = {item["pattern_key"]: item for item in candidates}
    raw = sorted(unique.values(), key=lambda x: x["final"]["time"], reverse=True)
    result: List[Dict[str, Any]] = []
    for item in raw:
        superseded = any(
            other["direction"] == item["direction"]
            and other["start"]["idx"] > item["start"]["idx"]
            and other["start"]["idx"] < item["final"]["idx"]
            for other in raw if other["pattern_key"] != item["pattern_key"]
        )
        if not superseded:
            result.append(item)
    if result:
        dinc("structures", len(result))
    return result

def calculate_cci(rows: List[Dict[str, Any]], period: int = CCI_PERIOD) -> List[Optional[float]]:
    typical = [(r["high"] + r["low"] + r["close"]) / 3.0 for r in rows]
    result: List[Optional[float]] = [None] * len(rows)
    for i in range(period - 1, len(typical)):
        window = typical[i - period + 1:i + 1]
        mean = sum(window) / period
        mean_dev = sum(abs(x - mean) for x in window) / period
        result[i] = 0.0 if mean_dev == 0 else (typical[i] - mean) / (0.015 * mean_dev)
    return result


def find_cci_cross(rows: List[Dict[str, Any]], direction: str, after_time: datetime, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Return the newest recent closed-candle CCI threshold cross after final node."""
    now = now or utc_now()
    closed = [r for r in rows if r["time"] + timedelta(minutes=M1_MINUTES) <= now]
    if len(closed) < CCI_PERIOD + 2:
        return None
    values = calculate_cci(closed)
    candidates = []
    for i in range(1, len(closed)):
        prev, cur = values[i - 1], values[i]
        if prev is None or cur is None:
            continue
        cross_time = closed[i]["time"] + timedelta(minutes=M1_MINUTES)
        if cross_time < after_time:
            continue
        age_minutes = (now - cross_time).total_seconds() / 60.0
        if age_minutes < 0 or age_minutes > CCI_CROSS_MAX_AGE_MINUTES:
            continue
        if direction == "LONG":
            crossed = prev <= -CCI_LEVEL and cur > -CCI_LEVEL
        else:
            crossed = prev >= CCI_LEVEL and cur < CCI_LEVEL
        if crossed:
            candidates.append({"time": cross_time, "cci": cur, "previous_cci": prev, "age_minutes": age_minutes})
    if not candidates:
        return None
    return max(candidates, key=lambda x: x["time"])


def target_price_for_pattern(pattern: Dict[str, Any], entry: float) -> Optional[float]:
    """TP is the 86.4% retracement from START to the F3 entry."""
    levels = levels_for_structure(pattern.get("direction", ""), float(pattern["start"]["price"]), float(pattern["final"]["price"]))
    if levels is None:
        return None
    return float(levels[2])


def build_entry_signal(
    pattern: Dict[str, Any], m1_rows: Optional[List[Dict[str, Any]]] = None, live_price: Optional[float] = None
) -> Optional[Dict[str, Any]]:
    """Build a signal immediately after the F3 pivot is confirmed on closed M5 candles."""
    entry = safe_float(pattern.get("entry_price"))
    sl = safe_float(pattern.get("sl_price"))
    tp = safe_float(pattern.get("tp_price"))
    if entry is None or sl is None or tp is None:
        dinc("reject_invalid_levels")
        return None
    direction = pattern["direction"]
    if direction == "SHORT" and not (0 < tp < entry < sl):
        dinc("reject_invalid_levels")
        return None
    if direction == "LONG" and not (0 < sl < entry < tp):
        dinc("reject_invalid_levels")
        return None
    signal = dict(pattern)
    signal.update({
        "signal_key": pattern["pattern_key"],
        "entry": float(entry), "sl": float(sl), "tp": float(tp),
        "signal_time": pattern.get("confirmed_at", utc_iso()),
        "detected_time": utc_iso(),
        "cross": None,
    })
    return signal


def build_structure_chart_payload(pattern: Dict[str, Any]) -> Dict[str, Any]:
    """Chart payload for a fully confirmed 10-node structure and F3 entry."""
    payload = dict(pattern)
    payload.update({
        "entry": float(pattern["entry_price"]),
        "sl": float(pattern["sl_price"]),
        "tp": float(pattern["tp_price"]),
        "structure_only": True,
    })
    return payload

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS nds6_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_key TEXT NOT NULL UNIQUE,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        entry_price REAL NOT NULL,
        sl_price REAL NOT NULL,
        tp_price REAL NOT NULL,
        entry_time TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN',
        exit_price REAL,
        exit_time TEXT,
        pnl_pct REAL,
        exit_reason TEXT,
        nodes_json TEXT,
        cci_value REAL,
        cci_cross_time TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS nds6_processed_signals (
        signal_key TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        created_at TEXT NOT NULL,
        state TEXT NOT NULL,
        note TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS nds6_structure_alerts (
        pattern_key TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        created_at TEXT NOT NULL,
        telegram_sent INTEGER NOT NULL DEFAULT 0
    )""")
    conn.execute("CREATE TABLE IF NOT EXISTS nds6_migration_flags (flag TEXT PRIMARY KEY, created_at TEXT NOT NULL)")
    # Start a clean report for the materially new v6.4.0 strategy exactly once.
    # Old paper trades/signals/structure alerts came from incompatible entry logic.
    reset_flag = "v6.4.0_clean_reporting_start"
    already_reset = conn.execute("SELECT 1 FROM nds6_migration_flags WHERE flag=?", (reset_flag,)).fetchone()
    if not already_reset:
        conn.execute("DELETE FROM nds6_trades")
        conn.execute("DELETE FROM nds6_processed_signals")
        conn.execute("DELETE FROM nds6_structure_alerts")
        conn.execute("INSERT INTO nds6_migration_flags(flag,created_at) VALUES(?,?)", (reset_flag, utc_iso()))
        print("REPORT_HISTORY_RESET | old strategy trades/signals/alerts cleared for v6.4.0")
    epoch_flag = "v6.4.0_strategy_epoch"
    has_epoch = conn.execute("SELECT 1 FROM nds6_migration_flags WHERE flag=?", (epoch_flag,)).fetchone()
    if not has_epoch:
        conn.execute("INSERT INTO nds6_migration_flags(flag,created_at) VALUES(?,?)", (epoch_flag, utc_iso()))
        print("STRATEGY_EPOCH_SET | only F3 structures confirmed from this version onward can signal")
    conn.commit()
    return conn


def structure_alert_was_sent(pattern_key_value: str) -> bool:
    conn = db_connect()
    try:
        row = conn.execute(
            "SELECT telegram_sent FROM nds6_structure_alerts WHERE pattern_key=?",
            (pattern_key_value,),
        ).fetchone()
        return bool(row and int(row[0]) == 1)
    finally:
        conn.close()


def mark_structure_alert_sent(pattern: Dict[str, Any], sent: bool) -> None:
    conn = db_connect()
    try:
        conn.execute(
            "INSERT INTO nds6_structure_alerts(pattern_key,symbol,direction,created_at,telegram_sent) VALUES(?,?,?,?,?) "
            "ON CONFLICT(pattern_key) DO UPDATE SET created_at=excluded.created_at, telegram_sent=excluded.telegram_sent",
            (pattern["pattern_key"], pattern["symbol"], pattern["direction"], utc_iso(), 1 if sent else 0),
        )
        conn.commit()
    finally:
        conn.close()


def get_open_trades() -> List[Dict[str, Any]]:
    conn = db_connect()
    try:
        rows = conn.execute("SELECT * FROM nds6_trades WHERE status='OPEN' ORDER BY entry_time, id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_trades() -> List[Dict[str, Any]]:
    conn = db_connect()
    try:
        rows = conn.execute("SELECT * FROM nds6_trades ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def signal_was_processed(signal_key: str) -> bool:
    conn = db_connect()
    try:
        return conn.execute("SELECT 1 FROM nds6_processed_signals WHERE signal_key=?", (signal_key,)).fetchone() is not None
    finally:
        conn.close()


def mark_signal_processed(signal: Dict[str, Any], state: str, note: str = "") -> None:
    conn = db_connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO nds6_processed_signals(signal_key,symbol,direction,created_at,state,note) VALUES(?,?,?,?,?,?)",
            (signal["signal_key"], signal["symbol"], signal["direction"], utc_iso(), state, note[:500]),
        )
        conn.commit()
    finally:
        conn.close()


def insert_trade(signal: Dict[str, Any]) -> Optional[int]:
    node_names = ("start", "n1", "s1", "f1", "s2", "n2", "s3", "f2", "s4", "f3")
    nodes_json = json.dumps({
        name: {"price": float(signal[name]["price"]), "time": utc_iso(signal[name]["time"])}
        for name in node_names
    }, ensure_ascii=False)
    conn = db_connect()
    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO nds6_trades
            (signal_key,symbol,direction,entry_price,sl_price,tp_price,entry_time,status,nodes_json,cci_value,cci_cross_time)
            VALUES(?,?,?,?,?,?,?,'OPEN',?,?,?)""",
            (signal["signal_key"], signal["symbol"], signal["direction"], signal["entry"], signal["sl"], signal["tp"],
             utc_iso(), nodes_json, None, None),
        )
        conn.commit()
        if cur.rowcount <= 0:
            row = conn.execute("SELECT id FROM nds6_trades WHERE signal_key=?", (signal["signal_key"],)).fetchone()
            return int(row[0]) if row else None
        return int(cur.lastrowid)
    finally:
        conn.close()

def close_trade(trade_id: int, exit_price: float, exit_reason: str, direction: str, entry: float) -> float:
    pnl = trade_pnl_pct(direction, entry, exit_price)
    conn = db_connect()
    try:
        conn.execute(
            "UPDATE nds6_trades SET status='CLOSED',exit_price=?,exit_time=?,pnl_pct=?,exit_reason=? WHERE id=? AND status='OPEN'",
            (exit_price, utc_iso(), pnl, exit_reason, trade_id),
        )
        conn.commit()
    finally:
        conn.close()
    return pnl


def check_exit_for_trade(trade: Dict[str, Any], rows: List[Dict[str, Any]], current: Optional[float]) -> Optional[Tuple[float, str]]:
    direction = trade["direction"]
    sl = float(trade["sl_price"])
    tp = float(trade["tp_price"])
    entry_time = parse_time(trade["entry_time"]) or datetime.min.replace(tzinfo=timezone.utc)
    # Use completed M5 bars after entry. If TP and SL occur in the same bar,
    # assume SL first (conservative; intrabar order is unknowable from OHLC).
    for candle in rows:
        if candle["time"] < entry_time:
            continue
        if direction == "LONG":
            sl_hit = candle["low"] <= sl
            tp_hit = candle["high"] >= tp
        else:
            sl_hit = candle["high"] >= sl
            tp_hit = candle["low"] <= tp
        if sl_hit:
            return sl, "SL"
        if tp_hit:
            return tp, "TP"
    if current is not None:
        if direction == "LONG":
            if current <= sl:
                return sl, "SL"
            if current >= tp:
                return tp, "TP"
        else:
            if current >= sl:
                return sl, "SL"
            if current <= tp:
                return tp, "TP"
    return None


def manage_open_trades(prices: Dict[str, float], rows_by_symbol: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    closed: List[Dict[str, Any]] = []
    for trade in get_open_trades():
        symbol = trade["symbol"]
        exit_info = check_exit_for_trade(trade, rows_by_symbol.get(symbol, []), prices.get(symbol))
        if not exit_info:
            continue
        exit_price, reason = exit_info
        pnl = close_trade(int(trade["id"]), exit_price, reason, trade["direction"], float(trade["entry_price"]))
        trade.update({"status": "CLOSED", "exit_price": exit_price, "exit_reason": reason, "pnl_pct": pnl, "exit_time": utc_iso()})
        closed.append(trade)
        dinc("trades_tp" if reason == "TP" else "trades_sl")
    return closed


def performance_stats(prices: Dict[str, float]) -> Dict[str, Any]:
    trades = get_all_trades()
    closed = [t for t in trades if t["status"] == "CLOSED"]
    opened = [t for t in trades if t["status"] == "OPEN"]
    wins = [t for t in closed if safe_float(t.get("pnl_pct")) is not None and float(t["pnl_pct"]) > 0]
    losses = [t for t in closed if safe_float(t.get("pnl_pct")) is None or float(t["pnl_pct"]) <= 0]
    realized = sum(float(t.get("pnl_pct") or 0.0) for t in closed)
    floating = sum(
        trade_pnl_pct(t["direction"], float(t["entry_price"]), prices[t["symbol"]])
        for t in opened if t["symbol"] in prices and float(t["entry_price"]) > 0
    )
    denominator = len(wins) + len(losses)
    return {
        "total": len(trades), "closed": len(closed), "open": len(opened),
        "wins": len(wins), "losses": len(losses),
        "win_rate": (len(wins) / denominator * 100.0) if denominator else 0.0,
        "realized": realized, "floating": floating, "combined": realized + floating,
    }


def telegram_send(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_NOT_CONFIGURED | report follows in Actions log")
        print(message)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        response = SESSION.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "disable_web_page_preview": True}, timeout=TELEGRAM_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and payload.get("ok") is True:
            return True
        print("TELEGRAM_SEND_ERROR | API returned ok=false")
    except Exception as exc:
        print(f"TELEGRAM_SEND_ERROR | {type(exc).__name__}: {exc}")
    return False


def telegram_send_photo(path: str, caption: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or not os.path.isfile(path):
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        with open(path, "rb") as image_file:
            response = SESSION.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1000]},
                files={"photo": (Path(path).name, image_file, "image/png")},
                timeout=TELEGRAM_TIMEOUT,
            )
        response.raise_for_status()
        payload = response.json()
        return isinstance(payload, dict) and payload.get("ok") is True
    except Exception as exc:
        print(f"TELEGRAM_PHOTO_ERROR | {type(exc).__name__}: {exc}")
        return False


def create_signal_chart(signal: Dict[str, Any], rows: List[Dict[str, Any]]) -> Optional[str]:
    if not rows:
        return None
    try:
        Path(CHART_DIR).mkdir(parents=True, exist_ok=True)
        start_idx = int(signal["start"]["idx"])
        final_idx = int(signal["final"]["idx"])
        left = max(0, start_idx - 25)
        right = min(len(rows), max(final_idx + 35, start_idx + 150))
        view = rows[left:right]
        if len(view) < 5:
            return None
        fig, ax = plt.subplots(figsize=(12, 6))
        for x, candle in enumerate(view):
            op, hi, lo, cl = candle["open"], candle["high"], candle["low"], candle["close"]
            is_up = cl >= op
            candle_color = "#218c53" if is_up else "#c0392b"
            ax.vlines(x, lo, hi, linewidth=0.8, color=candle_color)
            body_low = min(op, cl)
            body_height = max(abs(cl - op), max(abs(cl), 1.0) * 0.00001)
            ax.add_patch(Rectangle((x - 0.28, body_low), 0.56, body_height, fill=True, facecolor=candle_color, edgecolor=candle_color, linewidth=0.5, alpha=0.65))
        annotations = [(label, signal[name]) for label, name in (
            ("START", "start"), ("N1", "n1"), ("S1", "s1"), ("F1", "f1"),
            ("S2", "s2"), ("N2", "n2"), ("S3", "s3"), ("F2", "f2"),
            ("S4", "s4"), ("F3 / ENTRY", "f3"),
        )]
        for label, node in annotations:
            x = int(node["idx"]) - left
            if 0 <= x < len(view):
                ax.scatter([x], [node["price"]], s=42, zorder=5)
                offset = 12 if signal["direction"] == "LONG" else -30
                ax.annotate(f"{label}\n{fmt_price(node['price'])}", (x, node["price"]),
                            xytext=(0, offset), textcoords="offset points",
                            ha="center", fontsize=8, fontweight="bold",
                            bbox={"boxstyle": "round,pad=0.2", "fc": "white", "alpha": 0.75, "ec": "none"})

        # ENTRY is the FINAL pivot itself, not the latest candle on the chart.
        entry_x = int(signal["final"]["idx"]) - left
        ax.axhline(signal["entry"], color="#1565c0", linestyle="--", linewidth=1.5,
                   label=f"ENTRY {fmt_price(signal['entry'])}")
        ax.scatter([entry_x], [signal["entry"]], marker="^" if signal["direction"] == "LONG" else "v",
                   s=95, color="#1565c0", edgecolors="white", linewidths=0.7, zorder=7)
        ax.annotate(f"ENTRY\n{fmt_price(signal['entry'])}", (entry_x, signal["entry"]),
                    xytext=(-8, 12 if signal["direction"] == "LONG" else -30),
                    textcoords="offset points", ha="right", fontsize=8, color="#1565c0",
                    fontweight="bold", bbox={"boxstyle": "round,pad=0.2", "fc": "white", "alpha": 0.85, "ec": "#1565c0"})
        ax.axhline(signal["sl"], color="#c62828", linestyle=":", linewidth=1.5,
                   label=f"STOP LOSS (half TP distance) {fmt_price(signal['sl'])}")
        if signal.get("tp") is not None:
            ax.axhline(signal["tp"], color="#2e7d32", linestyle="-.", linewidth=1.5,
                       label=f"TP (86.4% retracement) {fmt_price(signal['tp'])}")
        chart_state = "F3 CONFIRMED ON CLOSED M5"
        ax.set_title(f"NDS v{VERSION} | {signal['symbol']} | {signal['direction']} | {chart_state} | TP 86.4%, SL half-risk")
        ax.set_xlabel("Closed M5 candles")
        ax.set_ylabel("Price")
        ax.grid(True, alpha=0.2)
        ax.legend(loc="best", fontsize=8)
        fig.tight_layout()
        out = os.path.join(CHART_DIR, f"{signal['symbol'].replace('/', '_')}_{signal['direction']}_{int(time.time())}.png")
        fig.savefig(out, dpi=130)
        plt.close(fig)
        return out
    except Exception as exc:
        print(f"CHART_ERROR | symbol={signal.get('symbol')} | {type(exc).__name__}: {exc}")
        return None


def signed_level_pct(direction: str, level: float, entry: float, name: str) -> float:
    # Return measured in the trade direction: TP is positive, SL is negative.
    return trade_pnl_pct(direction, entry, level)


def format_signal(signal: Dict[str, Any], trade_id: Optional[int], trade_opened: bool, current: Optional[float]) -> str:
    direction = signal["direction"]
    icon = "🟢" if direction == "LONG" else "🔴"
    entry, sl, tp = float(signal["entry"]), float(signal["sl"]), float(signal["tp"])
    live = current if current is not None else entry
    current_pnl = trade_pnl_pct(direction, entry, live)
    tp_entry_pct = signed_level_pct(direction, tp, entry, "TP")
    sl_entry_pct = signed_level_pct(direction, sl, entry, "SL")
    tp_from_current = ((tp / live) - 1.0) * 100.0 if direction == "LONG" and live else ((live / tp) - 1.0) * 100.0 if direction == "SHORT" and tp else 0.0
    sl_from_current = ((sl / live) - 1.0) * 100.0 if direction == "LONG" and live else ((live / sl) - 1.0) * 100.0 if direction == "SHORT" and sl else 0.0
    state = f"PAPER TRADE #{trade_id} OPENED" if trade_opened and trade_id else "SIGNAL ONLY (no free slot / duplicate symbol)"
    legs = signal.get("leg_lengths", [])
    legs_text = " / ".join(fmt_price(float(x)) for x in legs) if legs else "N/A"
    nodes_text = " | ".join(f"{name.upper()} {fmt_price(signal[name]['price'])}" for name in ("start", "n1", "s1", "f1", "s2", "n2", "s3", "f2", "s4", "f3"))
    return "\n".join([
        f"{icon} {direction} SIGNAL | {signal['symbol']} | {state}",
        f"Entry at F3: {fmt_price(entry)} | Current: {fmt_price(live)} ({current_pnl:+.2f}% PnL, {live_price_label(signal['symbol'])})",
        f"SL (half TP distance): {fmt_price(sl)} ({sl_entry_pct:+.2f}% from entry; current→SL {sl_from_current:+.2f}%)",
        f"TP (86.4% retracement): {fmt_price(tp)} ({tp_entry_pct:+.2f}% from entry; current→TP {tp_from_current:+.2f}%)",
        f"Nodes: {nodes_text}",
        f"Matched legs START→F1 / S2→F2 / S4→F3: {legs_text} | max difference {float(signal.get('leg_diff_pct', 0.0)):.2f}%",
        f"F3 confirmed: {signal.get('confirmed_at', signal.get('signal_time', 'N/A'))}",
    ])

def format_open_trade(trade: Dict[str, Any], current: Optional[float]) -> str:
    symbol, direction = trade["symbol"], trade["direction"]
    entry, sl, tp = float(trade["entry_price"]), float(trade["sl_price"]), float(trade["tp_price"])
    age = "?"
    dt = parse_time(trade.get("entry_time"))
    if dt:
        age = f"{max(0, int((utc_now() - dt).total_seconds() // 60))}m"
    if current is None:
        pnl = None
        current_text = "unavailable"
        tp_gap = sl_gap = None
    else:
        pnl = trade_pnl_pct(direction, entry, current)
        current_text = fmt_price(current)
        if direction == "LONG":
            tp_gap = (tp / current - 1) * 100 if current else 0.0
            sl_gap = (sl / current - 1) * 100 if current else 0.0
        else:
            tp_gap = (current / tp - 1) * 100 if tp else 0.0
            sl_gap = (current / sl - 1) * 100 if sl else 0.0
    tp_entry_pct = signed_level_pct(direction, tp, entry, "TP")
    sl_entry_pct = signed_level_pct(direction, sl, entry, "SL")
    pnl_text = f"{pnl:+.2f}%" if pnl is not None else "N/A"
    tp_text = f"{fmt_price(tp)} ({tp_entry_pct:+.2f}% entry; {tp_gap:+.2f}% from now)" if tp_gap is not None else f"{fmt_price(tp)} ({tp_entry_pct:+.2f}% entry)"
    sl_text = f"{fmt_price(sl)} ({sl_entry_pct:+.2f}% entry; {sl_gap:+.2f}% from now)" if sl_gap is not None else f"{fmt_price(sl)} ({sl_entry_pct:+.2f}% entry)"
    icon = "🟢" if direction == "LONG" else "🔴"
    return "\n".join([
        f"{icon} #{trade['id']} {direction} | {symbol} | age {age}",
        f"Entry: {fmt_price(entry)} | Current: {current_text} | PnL: {pnl_text}",
        f"SL (half TP distance): {sl_text}",
        f"TP (86.4% retracement): {tp_text}",
    ])


def format_closed_trade(trade: Dict[str, Any]) -> str:
    pnl = float(trade.get("pnl_pct") or 0.0)
    icon = "✅" if pnl > 0 else "❌"
    return f"{icon} #{trade['id']} {trade['symbol']} {trade['direction']} | {trade['exit_reason']} | Exit {fmt_price(float(trade['exit_price']))} | PnL {pnl:+.2f}%"


def split_message(text: str, limit: int = 3500) -> List[str]:
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    current = ""
    for block in text.split("\n\n"):
        addition = block if not current else "\n\n" + block
        if current and len(current) + len(addition) > limit:
            chunks.append(current)
            current = block
        else:
            current += addition
    if current:
        chunks.append(current)
    return chunks


def scan_m5_symbol(symbol: str) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]], Optional[str]]:
    try:
        rows = fetch_ohlc(symbol, M5_INTERVAL, M5_CANDLES)
        dinc("m5_candles", len(rows))
        if len(rows) < 40:
            return symbol, rows, [], "insufficient_m5_candles"
        structures = detect_structures(symbol, rows)
        return symbol, rows, structures, None
    except Exception as exc:
        return symbol, [], [], f"{type(exc).__name__}: {exc}"


def main() -> None:
    global STRATEGY_EPOCH
    started = utc_now()
    print(f"NDS_M5_10_NODE_START | version={VERSION} | time={utc_iso(started)}")
    print("MODE | REAL_TRADING=False | PAPER_ONLY=True | no exchange order endpoints are used")

    # Initialize/reset report history once and load the strategy-start cutoff.
    conn = db_connect()
    try:
        row = conn.execute("SELECT created_at FROM nds6_migration_flags WHERE flag=?", ("v6.4.0_strategy_epoch",)).fetchone()
        STRATEGY_EPOCH = parse_time(row[0]) if row else started
    finally:
        conn.close()
    print(f"STRATEGY_EPOCH | {utc_iso(STRATEGY_EPOCH or started)} | old structures are excluded")

    try:
        tickers = get_market_tickers()
        selected_symbols = get_target_assets(tickers, TARGET_ASSETS)
    except Exception as exc:
        message = f"NDS M5 10-NODE SCANNER ERROR\nTime: {utc_iso()}\n{type(exc).__name__}: {exc}"
        telegram_send(message)
        raise

    previously_open = get_open_trades()
    scan_symbols = list(dict.fromkeys(selected_symbols + [t["symbol"] for t in previously_open]))
    dinc("assets", len(scan_symbols))
    rows_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    structures_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    errors = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(scan_m5_symbol, symbol): symbol for symbol in scan_symbols}
        for future in as_completed(futures):
            symbol, rows, structures, error = future.result()
            rows_by_symbol[symbol] = rows
            structures_by_symbol[symbol] = structures
            if error:
                errors += 1
                dinc("m5_errors")
                print(f"M5_SCAN_ERROR | symbol={symbol} | error={error}")
            else:
                dinc("m5_ok")
            if structures:
                p = structures[0]
                print(f"M5_STRUCTURES | symbol={symbol} | count={len(structures)} | newest={p['direction']} | F3={fmt_price(p['f3']['price'])} | legs={p['leg_lengths']} | diff={p['leg_diff_pct']:.2f}%")

    prices = get_live_prices(scan_symbols, tickers)
    closed_trades = manage_open_trades(prices, rows_by_symbol)

    # Keep only the newest valid fresh structure per symbol/direction.
    candidate_patterns: List[Dict[str, Any]] = []
    for symbol, patterns in structures_by_symbol.items():
        latest_by_direction: Dict[str, Dict[str, Any]] = {}
        for pattern in patterns:
            direction = pattern["direction"]
            current = latest_by_direction.get(direction)
            if current is None or pattern["final"]["time"] > current["final"]["time"]:
                latest_by_direction[direction] = pattern
        candidate_patterns.extend(latest_by_direction.values())

    # Send one annotated chart for each freshly confirmed 10-node structure.
    for pattern in candidate_patterns:
        pkey = pattern["pattern_key"]
        if structure_alert_was_sent(pkey):
            continue
        chart_payload = build_structure_chart_payload(pattern)
        chart_path = create_signal_chart(chart_payload, rows_by_symbol.get(pattern["symbol"], []))
        if chart_path:
            caption = (
                f"🧩 NDS v{VERSION} | CONFIRMED 10-NODE STRUCTURE | {pattern['symbol']} | {pattern['direction']}\n"
                f"F3/ENTRY {fmt_price(pattern['entry_price'])} | TP 86.4% {fmt_price(pattern['tp_price'])} | SL {fmt_price(pattern['sl_price'])}\n"
                f"Legs START→F1 / S2→F2 / S4→F3: "
                f"{' / '.join(fmt_price(float(x)) for x in pattern['leg_lengths'])} | difference {pattern['leg_diff_pct']:.2f}%\n"
                f"Confirmed on closed M5 candles at {pattern['confirmed_at']}"
            )
            sent_chart = telegram_send_photo(chart_path, caption)
            mark_structure_alert_sent(pattern, sent_chart)
        else:
            print(f"STRUCTURE_CHART_NOT_CREATED | symbol={pattern['symbol']} | key={pkey}")

    new_signals: List[Dict[str, Any]] = []
    for pattern in sorted(candidate_patterns, key=lambda x: x["final"]["time"], reverse=True):
        symbol = pattern["symbol"]
        live = prices.get(symbol)
        if not live:
            dinc("reject_invalid_levels")
            continue
        if signal_was_processed(pattern["pattern_key"]):
            dinc("signals_duplicate")
            continue
        signal = build_entry_signal(pattern, None, live)
        if not signal:
            continue
        current_open = get_open_trades()
        same_symbol_open = any(t["symbol"] == symbol for t in current_open)
        can_open = len(current_open) < MAX_OPEN_TRADES and not same_symbol_open
        if can_open:
            trade_id = insert_trade(signal)
            if trade_id is None:
                mark_signal_processed(signal, "SIGNAL_ONLY", "duplicate signal key or trade was not inserted")
                continue
            mark_signal_processed(signal, "TRADE_OPENED", f"paper_trade_id={trade_id}")
            dinc("entries_opened")
            new_signals.append({"signal": signal, "trade_id": trade_id, "opened": True})
        else:
            note = "max open trades reached" if len(current_open) >= MAX_OPEN_TRADES else "symbol already has an open trade"
            mark_signal_processed(signal, "SIGNAL_ONLY", note)
            dinc("entries_blocked")
            new_signals.append({"signal": signal, "trade_id": None, "opened": False})
        dinc("signals_new")

    perf = performance_stats(prices)
    ended = utc_now()
    elapsed = (ended - started).total_seconds()
    lines = [
        f"🔎 NDS Scanner v{VERSION} | {utc_iso(ended)}",
        f"Scan: {len(scan_symbols)} symbols | M5 OK {DIAG['m5_ok']} | errors {errors} | 10-node structures {DIAG['structures']} | {elapsed:.1f}s",
        f"New signals {len(new_signals)} | open slots {MAX_OPEN_TRADES - len(get_open_trades())}/{MAX_OPEN_TRADES}",
        "",
        "🧪 DIAGNOSTICS (candidate 10-node windows)",
        f"Rejected geometry: {DIAG['reject_geometry']} | spacing: {DIAG['reject_spacing']} | invalid START: {DIAG['reject_start_extreme']}",
        f"Leg-size mismatch ≥2%: {DIAG['reject_leg_imbalance']} | leg <0.75%: {DIAG['reject_leg_distance']}",
        f"Stale structures (>12h): {DIAG['reject_stale_structure']} | formed before v6.4.0 epoch: {DIAG['reject_pre_strategy']} | invalid levels: {DIAG['reject_invalid_levels']}",
        "Rules: START has 5 candles on both sides | legs START-F1, S2-F2, S4-F3 differ by <2% | entry F3 | TP 86.4% retracement | SL half TP distance",
        "",
        f"🆕 SIGNALS: {len(new_signals)}",
    ]
    if new_signals:
        for item in new_signals:
            lines.extend([format_signal(item["signal"], item["trade_id"], item["opened"], prices.get(item["signal"]["symbol"])), ""])
    else:
        lines.append("No new confirmed entries.")

    open_now = get_open_trades()
    lines.append(f"📂 OPEN: {len(open_now)}/{MAX_OPEN_TRADES}")
    if open_now:
        for trade in open_now:
            lines.extend([format_open_trade(trade, prices.get(trade["symbol"])), ""])
    else:
        lines.append("No open paper trades.\n")

    lines.append(f"📊 RESULTS: {perf['closed']} closed | {perf['wins']}W/{perf['losses']}L | win {perf['win_rate']:.1f}%")
    lines.append(f"Realized PnL: {perf['realized']:+.2f}% (sum of trade returns) | Floating PnL: {perf['floating']:+.2f}% | Combined: {perf['combined']:+.2f}%")
    lines.append(f"Exits this run: TP {DIAG['trades_tp']} | SL {DIAG['trades_sl']} | Paper trades total {perf['total']}")
    if closed_trades:
        lines.extend(["", "✅ CLOSED THIS RUN"])
        lines.extend(format_closed_trade(t) for t in closed_trades)
    lines.append("Paper only | PnL excludes fees/funding")
    report = "\n".join(lines)
    sent = True
    for chunk in split_message(report):
        if not telegram_send(chunk):
            sent = False
            break

    if sent:
        for item in new_signals:
            signal = item["signal"]
            chart_path = create_signal_chart(signal, rows_by_symbol.get(signal["symbol"], []))
            if chart_path:
                state = f"PAPER TRADE #{item['trade_id']} OPENED" if item["opened"] and item["trade_id"] else "SIGNAL ONLY"
                caption = (
                    f"NDS v{VERSION} | {signal['symbol']} | {signal['direction']} | {state}\n"
                    f"Entry F3 {fmt_price(signal['entry'])} | TP 86.4% {fmt_price(signal['tp'])} | SL half distance {fmt_price(signal['sl'])}\n"
                    f"Legs START→F1 / S2→F2 / S4→F3: {' / '.join(fmt_price(float(x)) for x in signal['leg_lengths'])}"
                )
                telegram_send_photo(chart_path, caption)

    print("NDS_M5_10_NODE_DONE | " + json.dumps({
        "version": VERSION,
        "scanned": len(scan_symbols),
        "m5_ok": DIAG["m5_ok"],
        "m5_errors": errors,
        "structures": DIAG["structures"],
        "signals_new": len(new_signals),
        "diagnostics": {k: v for k, v in DIAG.items() if k.startswith("reject_")},
        "entries_opened": DIAG["entries_opened"],
        "entries_blocked": DIAG["entries_blocked"],
        "open_trades": len(open_now),
        "closed": perf["closed"],
        "wins": perf["wins"],
        "losses": perf["losses"],
        "win_rate": round(perf["win_rate"], 2),
        "runtime_seconds": round(elapsed, 1),
    }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FATAL | {type(exc).__name__}: {exc}")
        traceback.print_exc()
        raise
