# ============================================================
# NDS M5 LIVE SCANNER
# VERSION 5.5.4
# PAPER TRADING ONLY - NO REAL ORDERS
#
# Strategy preserved:
#   SHORT: START(L) -> H1 -> L1 -> H2 -> L2 -> H3
#   LONG : START(H) -> L1 -> H1 -> L2 -> H2 -> L3
#
# Changes in 5.5.4:
#   1) Explicitly records first-seen/new hooks in SQLite.
#   2) Reports New Hooks Since Last Run.
#   3) Telegram hook charts are sent ONLY when the 86.4% TP has
#      NOT been touched after the final H3/L3 node.
#   4) Keeps the existing DB filename for continuity.
#   5) Adds clearer hook-age diagnostics.
#   6) Keeps Heikin-Ashi charts while NDS detection uses real OHLC.
# ============================================================

import os
import time
import math
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


VERSION = "5.5.4"
REAL_TRADING = False
PAPER_ONLY = True

TARGET_ASSETS = 100
M5_INTERVAL = "5m"
M5_INTERVAL_MINUTES = 5
M5_CANDLES = 1500

PIVOT_LEFT = 2
PIVOT_RIGHT = 2
MIN_NODE_CANDLES = 5
NDS_RETRACE = 0.864
M5_MIN_HOOK_RANGE_PCT = 0.20
M5_MAX_HOOK_AGE_SECONDS = 6 * 60 * 60
MAX_OPEN_TRADES = 3

REQUEST_TIMEOUT = 20
SCAN_SLEEP_SECONDS = 0.25
CHART_CANDLES = 240

DB_FILE = "nds_h4_m5_v546.db"
CHART_DIR = "nds_h4_m5_charts"

KRAKEN_FUTURES_BASE = "https://futures.kraken.com/derivatives/api/v3"
KRAKEN_TICKERS_URL = f"{KRAKEN_FUTURES_BASE}/tickers"
KRAKEN_INSTRUMENTS_URL = f"{KRAKEN_FUTURES_BASE}/instruments"
KRAKEN_CHARTS_BASE = "https://futures.kraken.com/api/charts/v1/trade"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT")


# ------------------------------------------------------------
# BASIC UTILITIES
# ------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(dt: Optional[datetime] = None) -> str:
    dt = dt or utc_now()
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 10_000_000_000:
            v /= 1000.0
        return datetime.fromtimestamp(v, tz=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        try:
            return datetime.fromtimestamp(float(s), tz=timezone.utc)
        except Exception:
            return None


def ts_key(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")


def safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        x = float(value)
        if not math.isfinite(x):
            return None
        return x
    except Exception:
        return None


def fmt_price(x: Optional[float]) -> str:
    if x is None:
        return "-"
    if x == 0:
        return "0"
    ax = abs(x)
    if ax >= 1000:
        return f"{x:.2f}"
    if ax >= 100:
        return f"{x:.3f}"
    if ax >= 1:
        return f"{x:.4f}"
    if ax >= 0.01:
        return f"{x:.6f}"
    if ax >= 0.0001:
        return f"{x:.8f}"
    return f"{x:.10f}"


def pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return abs(a - b) / abs(b) * 100.0


def age_seconds(dt: datetime) -> float:
    return max(0.0, (utc_now() - dt).total_seconds())


# ------------------------------------------------------------
# HTTP / KRAKEN
# ------------------------------------------------------------

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": f"NDS-M5-Scanner/{VERSION}"})


def get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    try:
        r = SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def extract_items(data: Dict[str, Any]) -> List[Any]:
    for key in ("tickers", "instruments", "candles", "result", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for subkey in ("tickers", "instruments", "candles", "data"):
                sub = value.get(subkey)
                if isinstance(sub, list):
                    return sub
    return []


def get_target_assets(limit: int = TARGET_ASSETS) -> List[str]:
    data = get_json(KRAKEN_INSTRUMENTS_URL)
    symbols: List[str] = []
    if data:
        for item in extract_items(data):
            if isinstance(item, dict):
                sym = item.get("symbol") or item.get("ticker") or item.get("instrument")
                if isinstance(sym, str) and sym.startswith("PF_"):
                    symbols.append(sym)

    # Fallback: use ticker endpoint if instruments endpoint is unavailable.
    if not symbols:
        data = get_json(KRAKEN_TICKERS_URL)
        if data:
            for item in extract_items(data):
                if isinstance(item, dict):
                    sym = item.get("symbol") or item.get("ticker")
                    if isinstance(sym, str) and sym.startswith("PF_"):
                        symbols.append(sym)

    # Stable order avoids needless changes in scan behavior.
    return sorted(set(symbols))[:limit]


def normalize_candle(item: Any) -> Optional[Dict[str, Any]]:
    # Common Kraken Futures OHLC response forms:
    # dict: {time, open, high, low, close, ...}
    # list : [time, open, high, low, close, ...]
    if isinstance(item, dict):
        t = item.get("time")
        o = item.get("open")
        h = item.get("high")
        l = item.get("low")
        c = item.get("close")
    elif isinstance(item, (list, tuple)) and len(item) >= 5:
        t, o, h, l, c = item[:5]
    else:
        return None

    dt = parse_time(t)
    o = safe_float(o)
    h = safe_float(h)
    l = safe_float(l)
    c = safe_float(c)
    if dt is None or None in (o, h, l, c):
        return None
    if h < l:
        return None
    return {"time": dt, "open": o, "high": h, "low": l, "close": c}


def fetch_ohlc(symbol: str, candles: int = M5_CANDLES) -> List[Dict[str, Any]]:
    """Fetch the latest M5 candles, paginating the Kraken Futures chart API."""
    end_dt = utc_now()
    start_dt = end_dt - timedelta(minutes=M5_INTERVAL_MINUTES * (candles + 20))
    url = f"{KRAKEN_CHARTS_BASE}/{symbol}/5m"

    cursor_s = int(start_dt.timestamp())
    end_s = int(end_dt.timestamp())
    rows_by_ms: Dict[int, Dict[str, Any]] = {}

    # Kraken's Futures chart endpoint can cap a response, so walk forward until
    # the requested lookback has been collected or no progress is possible.
    for _ in range(8):
        try:
            r = SESSION.get(
                url,
                params={"from": cursor_s, "to": end_s},
                timeout=REQUEST_TIMEOUT,
            )
            r.raise_for_status()
            payload = r.json()
        except Exception:
            return []

        raw = payload.get("candles") if isinstance(payload, dict) else None
        if not isinstance(raw, list) or not raw:
            break

        page_times_ms: List[int] = []
        added = 0
        for item in raw:
            c = normalize_candle(item)
            if not c:
                continue
            ms = int(c["time"].timestamp() * 1000)
            rows_by_ms[ms] = c
            page_times_ms.append(ms)
            added += 1

        if not added or not page_times_ms:
            break
        last_ms = max(page_times_ms)
        last_s = last_ms // 1000
        if last_s <= cursor_s:
            break
        cursor_s = last_s + M5_INTERVAL_MINUTES * 60
        if cursor_s >= end_s:
            break

    rows = [rows_by_ms[k] for k in sorted(rows_by_ms)]
    if len(rows) > candles:
        rows = rows[-candles:]
    return rows


# ------------------------------------------------------------
# PIVOTS / NDS
# ------------------------------------------------------------

def is_pivot_high(rows: List[Dict[str, Any]], i: int) -> bool:
    if i < PIVOT_LEFT or i + PIVOT_RIGHT >= len(rows):
        return False
    v = rows[i]["high"]
    window = [rows[j]["high"] for j in range(i - PIVOT_LEFT, i + PIVOT_RIGHT + 1)]
    return v >= max(window) and v > max(rows[j]["high"] for j in range(i - PIVOT_LEFT, i))


def is_pivot_low(rows: List[Dict[str, Any]], i: int) -> bool:
    if i < PIVOT_LEFT or i + PIVOT_RIGHT >= len(rows):
        return False
    v = rows[i]["low"]
    window = [rows[j]["low"] for j in range(i - PIVOT_LEFT, i + PIVOT_RIGHT + 1)]
    return v <= min(window) and v < min(rows[j]["low"] for j in range(i - PIVOT_LEFT, i))


def build_ordered_pivots(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    pivots: List[Dict[str, Any]] = []
    for i in range(len(rows)):
        ph = is_pivot_high(rows, i)
        pl = is_pivot_low(rows, i)
        if not ph and not pl:
            continue

        # In the rare case a candle is both, keep the more pronounced side.
        if ph and pl:
            up = rows[i]["high"] - rows[i]["open"]
            dn = rows[i]["open"] - rows[i]["low"]
            ptype = "H" if up >= dn else "L"
        else:
            ptype = "H" if ph else "L"

        price = rows[i]["high"] if ptype == "H" else rows[i]["low"]
        node = {
            "idx": i,
            "time": rows[i]["time"],
            "price": price,
            "type": ptype,
        }

        # Collapse consecutive same-type pivots, keeping the more extreme one.
        if pivots and pivots[-1]["type"] == ptype:
            if ptype == "H" and price >= pivots[-1]["price"]:
                pivots[-1] = node
            elif ptype == "L" and price <= pivots[-1]["price"]:
                pivots[-1] = node
        else:
            pivots.append(node)

    return pivots


def node_spacing_ok(nodes: List[Dict[str, Any]]) -> bool:
    for a, b in zip(nodes, nodes[1:]):
        if b["idx"] - a["idx"] < MIN_NODE_CANDLES:
            return False
    return True


def make_hook_key(symbol: str, direction: str, final_time: datetime, final_price: float) -> str:
    return f"{symbol}|{direction}|{ts_key(final_time)}|{final_price:.12g}"


def build_hook(symbol: str, rows: List[Dict[str, Any]], nodes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if len(nodes) != 6:
        return None
    types = "".join(n["type"] for n in nodes)

    # SHORT = L H L H L H
    if types == "LHLHLH":
        start, h1, l1, h2, l2, final = nodes
        if not (h2["price"] > h1["price"]):
            return None
        if not (l2["price"] < l1["price"]):
            return None
        if not (final["price"] > h2["price"]):
            return None
        direction = "SHORT"
        tp = final["price"] - NDS_RETRACE * (final["price"] - start["price"])

    # LONG = H L H L H L
    elif types == "HLHLHL":
        start, l1, h1, l2, h2, final = nodes
        if not (l2["price"] < l1["price"]):
            return None
        if not (h2["price"] > h1["price"]):
            return None
        if not (final["price"] < l2["price"]):
            return None
        direction = "LONG"
        tp = final["price"] + NDS_RETRACE * (start["price"] - final["price"])
    else:
        return None

    if not node_spacing_ok(nodes):
        return None

    start_price = start["price"]
    final_price = final["price"]
    if start_price == 0:
        return None

    range_pct = abs(final_price - start_price) / abs(start_price) * 100.0
    confirmed_time = final["time"] + timedelta(minutes=PIVOT_RIGHT * M5_INTERVAL_MINUTES)

    hook = {
        "symbol": symbol,
        "direction": direction,
        "start": start,
        "h1": h1,
        "l1": l1,
        "h2": h2,
        "l2": l2,
        "final": final,
        "tp": tp,
        "range_pct": range_pct,
        "confirmed_time": confirmed_time,
        "hook_key": make_hook_key(symbol, direction, final["time"], final_price),
        "node_indices": [n["idx"] for n in nodes],
    }
    return hook


def detect_hooks(symbol: str, rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    pivots = build_ordered_pivots(rows)
    hooks: List[Dict[str, Any]] = []
    seen = set()
    for i in range(len(pivots) - 5):
        nodes = pivots[i:i + 6]
        if nodes[-1]["idx"] + PIVOT_RIGHT >= len(rows):
            continue
        hook = build_hook(symbol, rows, nodes)
        if not hook:
            continue
        key = hook["hook_key"]
        if key in seen:
            continue
        seen.add(key)
        hooks.append(hook)
    return hooks, len(pivots)


# ------------------------------------------------------------
# TP / SL
# ------------------------------------------------------------

def tp_touch_info(rows: List[Dict[str, Any]], hook: Dict[str, Any]) -> Dict[str, Any]:
    final_idx = hook["final"]["idx"]
    tp = hook["tp"]
    direction = hook["direction"]
    for i in range(final_idx + 1, len(rows)):
        candle = rows[i]
        touched = candle["low"] <= tp if direction == "SHORT" else candle["high"] >= tp
        if touched:
            return {
                "touched": True,
                "idx": i,
                "time": candle["time"],
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
            }
    return {"touched": False}


def find_protective_sl(hook: Dict[str, Any], pivots: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    final_idx = hook["final"]["idx"]
    entry = hook["final"]["price"]
    if hook["direction"] == "SHORT":
        candidates = [
            p for p in pivots
            if p["idx"] < final_idx and p["type"] == "H" and p["price"] > entry
        ]
    else:
        candidates = [
            p for p in pivots
            if p["idx"] < final_idx and p["type"] == "L" and p["price"] < entry
        ]
    if not candidates:
        return None
    return candidates[-1]


def geometry_valid(hook: Dict[str, Any], sl: Optional[Dict[str, Any]]) -> bool:
    if sl is None:
        return False
    entry = hook["final"]["price"]
    tp = hook["tp"]
    if hook["direction"] == "SHORT":
        return tp < entry < sl["price"]
    return sl["price"] < entry < tp


# ------------------------------------------------------------
# SQLITE
# ------------------------------------------------------------

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_columns(conn: sqlite3.Connection, table: str, columns: Dict[str, str]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_db() -> None:
    conn = db_connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hook_key TEXT UNIQUE,
                symbol TEXT,
                direction TEXT,
                start_time TEXT,
                start_price REAL,
                h1_time TEXT,
                h1_price REAL,
                l1_time TEXT,
                l1_price REAL,
                h2_time TEXT,
                h2_price REAL,
                l2_time TEXT,
                l2_price REAL,
                final_time TEXT,
                final_price REAL,
                tp REAL,
                range_pct REAL,
                confirmed_time TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hook_key TEXT UNIQUE,
                symbol TEXT,
                direction TEXT,
                entry REAL,
                tp REAL,
                sl REAL,
                status TEXT,
                pnl_pct REAL,
                opened_at TEXT,
                closed_at TEXT,
                close_reason TEXT,
                duration_seconds REAL,
                last_checked_at TEXT,
                last_price REAL,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hook_chart_notifications (
                hook_key TEXT PRIMARY KEY,
                path TEXT,
                sent_at TEXT
            )
            """
        )
        ensure_columns(conn, "hooks", {
            "hook_key": "TEXT",
            "symbol": "TEXT",
            "direction": "TEXT",
            "start_time": "TEXT",
            "start_price": "REAL",
            "h1_time": "TEXT",
            "h1_price": "REAL",
            "l1_time": "TEXT",
            "l1_price": "REAL",
            "h2_time": "TEXT",
            "h2_price": "REAL",
            "l2_time": "TEXT",
            "l2_price": "REAL",
            "final_time": "TEXT",
            "final_price": "REAL",
            "tp": "REAL",
            "range_pct": "REAL",
            "confirmed_time": "TEXT",
            "created_at": "TEXT",
        })
        ensure_columns(conn, "trades", {
            "hook_key": "TEXT",
            "symbol": "TEXT",
            "direction": "TEXT",
            "entry": "REAL",
            "tp": "REAL",
            "sl": "REAL",
            "status": "TEXT",
            "pnl_pct": "REAL",
            "opened_at": "TEXT",
            "closed_at": "TEXT",
            "close_reason": "TEXT",
            "duration_seconds": "REAL",
            "last_checked_at": "TEXT",
            "last_price": "REAL",
            "created_at": "TEXT",
        })
        ensure_columns(conn, "hook_chart_notifications", {
            "hook_key": "TEXT",
            "path": "TEXT",
            "sent_at": "TEXT",
        })
        conn.commit()
    finally:
        conn.close()


def hook_exists(hook_key: str) -> bool:
    conn = db_connect()
    try:
        row = conn.execute("SELECT 1 FROM hooks WHERE hook_key=? LIMIT 1", (hook_key,)).fetchone()
        return row is not None
    finally:
        conn.close()


def save_hook(hook: Dict[str, Any]) -> bool:
    """Return True only when this hook was first inserted in persistent DB."""
    n = utc_iso()
    conn = db_connect()
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO hooks
            (hook_key,symbol,direction,start_time,start_price,h1_time,h1_price,
             l1_time,l1_price,h2_time,h2_price,l2_time,l2_price,final_time,final_price,
             tp,range_pct,confirmed_time,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                hook["hook_key"], hook["symbol"], hook["direction"],
                utc_iso(hook["start"]["time"]), hook["start"]["price"],
                utc_iso(hook["h1"]["time"]), hook["h1"]["price"],
                utc_iso(hook["l1"]["time"]), hook["l1"]["price"],
                utc_iso(hook["h2"]["time"]), hook["h2"]["price"],
                utc_iso(hook["l2"]["time"]), hook["l2"]["price"],
                utc_iso(hook["final"]["time"]), hook["final"]["price"],
                hook["tp"], hook["range_pct"], utc_iso(hook["confirmed_time"]), n,
            ),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def notification_exists(hook_key: str) -> bool:
    conn = db_connect()
    try:
        return conn.execute(
            "SELECT 1 FROM hook_chart_notifications WHERE hook_key=? LIMIT 1", (hook_key,)
        ).fetchone() is not None
    finally:
        conn.close()


def mark_notification_sent(hook_key: str, path: str) -> None:
    conn = db_connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO hook_chart_notifications(hook_key,path,sent_at) VALUES(?,?,?)",
            (hook_key, path, utc_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def trade_exists(hook_key: str) -> bool:
    conn = db_connect()
    try:
        return conn.execute("SELECT 1 FROM trades WHERE hook_key=? LIMIT 1", (hook_key,)).fetchone() is not None
    finally:
        conn.close()


def open_trade_count() -> int:
    conn = db_connect()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM trades WHERE status='OPEN'").fetchone()
        return int(row["n"] or 0)
    finally:
        conn.close()


def save_trade(hook: Dict[str, Any], sl: Dict[str, Any]) -> bool:
    conn = db_connect()
    try:
        now = utc_iso()
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO trades
            (hook_key,symbol,direction,entry,tp,sl,status,pnl_pct,opened_at,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                hook["hook_key"], hook["symbol"], hook["direction"],
                hook["final"]["price"], hook["tp"], sl["price"],
                "OPEN", 0.0, now, now,
            ),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def performance_summary() -> Dict[str, Any]:
    conn = db_connect()
    try:
        closed = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(pnl_pct),0) AS pnl FROM trades WHERE status IN ('TP','SL')"
        ).fetchone()
        tp_n = conn.execute("SELECT COUNT(*) AS n FROM trades WHERE status='TP'").fetchone()["n"]
        sl_n = conn.execute("SELECT COUNT(*) AS n FROM trades WHERE status='SL'").fetchone()["n"]
        op = conn.execute("SELECT COUNT(*) AS n FROM trades WHERE status='OPEN'").fetchone()["n"]
        return {
            "closed": int(closed["n"] or 0),
            "pnl": float(closed["pnl"] or 0.0),
            "tp": int(tp_n or 0),
            "sl": int(sl_n or 0),
            "open": int(op or 0),
        }
    finally:
        conn.close()


# ------------------------------------------------------------
# PAPER TRADE MONITOR
# ------------------------------------------------------------

def trade_pnl_pct(direction: str, entry: float, last_price: float) -> float:
    if entry == 0:
        return 0.0
    if direction == "LONG":
        return (last_price - entry) / entry * 100.0
    return (entry - last_price) / entry * 100.0


def close_trade(trade_id: int, reason: str, last_price: float, opened_at: str) -> None:
    opened = parse_time(opened_at) or utc_now()
    now = utc_now()
    duration = max(0.0, (now - opened).total_seconds())
    conn = db_connect()
    try:
        row = conn.execute(
            "SELECT direction,entry FROM trades WHERE id=?", (trade_id,)
        ).fetchone()
        if not row:
            return
        pnl = trade_pnl_pct(row["direction"], row["entry"], last_price)
        conn.execute(
            """
            UPDATE trades
            SET status=?, pnl_pct=?, closed_at=?, close_reason=?, duration_seconds=?,
                last_checked_at=?, last_price=?
            WHERE id=? AND status='OPEN'
            """,
            (reason, pnl, utc_iso(now), reason, duration, utc_iso(now), last_price, trade_id),
        )
        conn.commit()
    finally:
        conn.close()


def monitor_open_trades(rows_by_symbol: Dict[str, List[Dict[str, Any]]], stats: Dict[str, int]) -> None:
    conn = db_connect()
    try:
        trades = conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
    finally:
        conn.close()

    for tr in trades:
        symbol = tr["symbol"]
        rows = rows_by_symbol.get(symbol) or fetch_ohlc(symbol, M5_CANDLES)
        if not rows:
            continue
        rows_by_symbol[symbol] = rows
        stats["trades_checked"] += 1

        opened = parse_time(tr["opened_at"]) or utc_now()
        direction = tr["direction"]
        tp = float(tr["tp"])
        sl = float(tr["sl"])
        touched_reason = None
        touch_price = None

        # Search from the first candle that starts at/after the paper-open time.
        for candle in rows:
            if candle["time"] < opened:
                continue
            if direction == "LONG":
                hit_sl = candle["low"] <= sl
                hit_tp = candle["high"] >= tp
            else:
                hit_sl = candle["high"] >= sl
                hit_tp = candle["low"] <= tp

            if hit_sl and hit_tp:
                # Same-candle order cannot be reconstructed from OHLC.
                # Conservative choice: SL first.
                touched_reason = "SL"
                touch_price = sl
                break
            if hit_sl:
                touched_reason = "SL"
                touch_price = sl
                break
            if hit_tp:
                touched_reason = "TP"
                touch_price = tp
                break

        last_price = rows[-1]["close"]
        conn2 = db_connect()
        try:
            conn2.execute(
                "UPDATE trades SET last_checked_at=?, last_price=? WHERE id=? AND status='OPEN'",
                (utc_iso(), last_price, tr["id"]),
            )
            conn2.commit()
        finally:
            conn2.close()

        if touched_reason:
            close_trade(tr["id"], touched_reason, touch_price, tr["opened_at"])
            stats["trades_closed"] += 1


# ------------------------------------------------------------
# HEIKIN ASHI CHARTS
# ------------------------------------------------------------

def heikin_ashi(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    prev_ha_open = None
    prev_ha_close = None
    for r in rows:
        ha_close = (r["open"] + r["high"] + r["low"] + r["close"]) / 4.0
        if prev_ha_open is None:
            ha_open = (r["open"] + r["close"]) / 2.0
        else:
            ha_open = (prev_ha_open + prev_ha_close) / 2.0
        ha_high = max(r["high"], ha_open, ha_close)
        ha_low = min(r["low"], ha_open, ha_close)
        out.append({"time": r["time"], "open": ha_open, "high": ha_high, "low": ha_low, "close": ha_close})
        prev_ha_open = ha_open
        prev_ha_close = ha_close
    return out


def make_chart(symbol: str, rows: List[Dict[str, Any]], hook: Dict[str, Any], sl: Optional[Dict[str, Any]], tp_info: Dict[str, Any]) -> Optional[str]:
    try:
        final_idx = hook["final"]["idx"]
        left = max(0, final_idx - CHART_CANDLES + 1)
        chart_rows = rows[left:final_idx + CHART_CANDLES // 3]
        if len(chart_rows) < 20:
            chart_rows = rows[max(0, len(rows) - CHART_CANDLES):]

        ha = heikin_ashi(chart_rows)
        x0 = chart_rows[0]["time"]
        xs = [(r["time"] - x0).total_seconds() / 60.0 for r in chart_rows]
        width = 3.4

        fig, ax = plt.subplots(figsize=(15, 8), dpi=150)
        for i, r in enumerate(ha):
            x = xs[i]
            o, h, l, c = r["open"], r["high"], r["low"], r["close"]
            ax.plot([x, x], [l, h], linewidth=0.8)
            bottom = min(o, c)
            height = max(abs(c - o), max(abs(o), 1e-12) * 0.00001)
            rect = Rectangle((x - width / 2, bottom), width, height, fill=False, linewidth=0.8)
            ax.add_patch(rect)

        nodes = [
            (hook["start"], "START"),
            (hook["h1"], "H1"),
            (hook["l1"], "L1"),
            (hook["h2"], "H2"),
            (hook["l2"], "L2"),
            (hook["final"], "H3" if hook["direction"] == "SHORT" else "L3"),
        ]
        for node, label in nodes:
            x = (node["time"] - x0).total_seconds() / 60.0
            ax.scatter([x], [node["price"]], s=24)
            ax.annotate(label, (x, node["price"]), xytext=(4, 8), textcoords="offset points", fontsize=9)

        entry = hook["final"]["price"]
        tp = hook["tp"]
        ax.axhline(entry, linewidth=1.0, linestyle="--")
        ax.axhline(tp, linewidth=1.0)
        ax.text(xs[-1], entry, f"  ENTRY {fmt_price(entry)}", va="bottom", fontsize=9)
        ax.text(xs[-1], tp, f"  86.4% TP {fmt_price(tp)}", va="bottom", fontsize=9)

        if sl is not None:
            ax.axhline(sl["price"], linewidth=1.0, linestyle=":")
            ax.text(xs[-1], sl["price"], f"  SL {fmt_price(sl['price'])}", va="bottom", fontsize=9)

        title_status = "86.4% NOT TOUCHED"
        title = f"{symbol} | {hook['direction']} | NDS M5 | {title_status}\n"
        title += f"Range {hook['range_pct']:.2f}% | Final {utc_iso(hook['final']['time'])}"
        ax.set_title(title)
        ax.set_xlabel("Minutes")
        ax.set_ylabel("Price")
        ax.grid(alpha=0.15)
        fig.tight_layout()

        Path(CHART_DIR).mkdir(parents=True, exist_ok=True)
        name = f"{symbol}_{hook['direction']}_{ts_key(hook['final']['time'])}_{hook['final']['price']:.12g}.png".replace("/", "_")
        path = str(Path(CHART_DIR) / name)
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path
    except Exception:
        try:
            plt.close("all")
        except Exception:
            pass
        return None


# ------------------------------------------------------------
# TELEGRAM
# ------------------------------------------------------------

def telegram_configured() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def telegram_send_photo(path: str, caption: str) -> bool:
    if not telegram_configured():
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        with open(path, "rb") as f:
            r = requests.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
                files={"photo": f},
                timeout=REQUEST_TIMEOUT,
            )
        return r.ok
    except Exception:
        return False


def telegram_send_text(text: str) -> bool:
    if not telegram_configured():
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=REQUEST_TIMEOUT,
        )
        return r.ok
    except Exception:
        return False


def send_trade_alert(hook: Dict[str, Any], sl: Dict[str, Any], current_price: float) -> None:
    arrow = "🟢 LONG" if hook["direction"] == "LONG" else "🔴 SHORT"
    text = (
        f"{arrow}\n"
        f"Symbol: {hook['symbol']}\n"
        f"Entry: {fmt_price(hook['final']['price'])}\n"
        f"SL: {fmt_price(sl['price'])}\n"
        f"TP: {fmt_price(hook['tp'])}\n"
        f"Current: {fmt_price(current_price)}\n"
        f"Range: {hook['range_pct']:.2f}%\n"
        f"Confirmed: {utc_iso(hook['confirmed_time'])}\n"
        f"Mode: PAPER ONLY"
    )
    telegram_send_text(text)


# ------------------------------------------------------------
# DIAGNOSTICS
# ------------------------------------------------------------

def count_hooks_by_age(all_hooks: List[Dict[str, Any]]) -> Dict[str, int]:
    now = utc_now()
    out = {"6h": 0, "12h": 0, "24h": 0, "48h": 0, "72h": 0}
    for h in all_hooks:
        age = (now - h["confirmed_time"]).total_seconds()
        if age <= 6 * 3600:
            out["6h"] += 1
        if age <= 12 * 3600:
            out["12h"] += 1
        if age <= 24 * 3600:
            out["24h"] += 1
        if age <= 48 * 3600:
            out["48h"] += 1
        if age <= 72 * 3600:
            out["72h"] += 1
    return out


def main() -> None:
    if REAL_TRADING or not PAPER_ONLY:
        raise RuntimeError("Safety stop: this scanner is configured for paper trading only.")

    init_db()
    scan_started = time.time()

    stats = {
        "scanned": 0,
        "requests": 0,
        "data_ok": 0,
        "data_error": 0,
        "empty": 0,
        "short": 0,
        "pivot_points": 0,
        "hooks": 0,
        "confirmed_hooks": 0,
        "recent_hooks": 0,
        "range_valid": 0,
        "valid_before_tp": 0,
        "tp_touched": 0,
        "sl_found": 0,
        "geometry_valid": 0,
        "duplicate": 0,
        "max_open": 0,
        "signal_ready": 0,
        "new_hooks": 0,
        "hook_charts_sent": 0,
        "hook_chart_errors": 0,
        "signals": 0,
        "trade_duplicates": 0,
        "trades_checked": 0,
        "trades_closed": 0,
    }

    all_recent_valid: List[Dict[str, Any]] = []
    all_hooks_for_age: List[Dict[str, Any]] = []
    rows_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    target_assets = get_target_assets(TARGET_ASSETS)
    stats["scanned"] = len(target_assets)

    # Monitor DB-backed trades before scanning new hooks.
    monitor_open_trades(rows_by_symbol, stats)

    for symbol in target_assets:
        stats["requests"] += 1
        rows = fetch_ohlc(symbol, M5_CANDLES)
        if not rows:
            stats["data_error"] += 1
            time.sleep(SCAN_SLEEP_SECONDS)
            continue
        if len(rows) < PIVOT_LEFT + PIVOT_RIGHT + 20:
            stats["short"] += 1
            time.sleep(SCAN_SLEEP_SECONDS)
            continue

        stats["data_ok"] += 1
        rows_by_symbol[symbol] = rows
        hooks, pivot_count = detect_hooks(symbol, rows)
        stats["pivot_points"] += pivot_count
        stats["hooks"] += len(hooks)
        stats["confirmed_hooks"] += len(hooks)
        all_hooks_for_age.extend(hooks)

        pivots = build_ordered_pivots(rows)

        for hook in hooks:
            is_new = save_hook(hook)
            if is_new:
                stats["new_hooks"] += 1

            # The chart rule is intentionally independent from the trade rule:
            # send only charts where 86.4% has NOT been touched after H3/L3.
            touch = tp_touch_info(rows, hook)
            if touch["touched"]:
                stats["tp_touched"] += 1
            else:
                if not notification_exists(hook["hook_key"]):
                    sl_for_chart = find_protective_sl(hook, pivots)
                    chart_path = make_chart(symbol, rows, hook, sl_for_chart, touch)
                    if chart_path:
                        caption = (
                            f"NDS M5 | {symbol} | {hook['direction']}\n"
                            f"H3/L3: {fmt_price(hook['final']['price'])}\n"
                            f"86.4% TP: {fmt_price(hook['tp'])}\n"
                            f"86.4% status: NOT TOUCHED\n"
                            f"Range: {hook['range_pct']:.2f}%\n"
                            f"Confirmed: {utc_iso(hook['confirmed_time'])}"
                        )
                        if telegram_send_photo(chart_path, caption):
                            mark_notification_sent(hook["hook_key"], chart_path)
                            stats["hook_charts_sent"] += 1
                        else:
                            stats["hook_chart_errors"] += 1
                    else:
                        stats["hook_chart_errors"] += 1

            # Trade candidates are limited to recent + range valid + TP untouched.
            confirmed_age = age_seconds(hook["confirmed_time"])
            if confirmed_age <= M5_MAX_HOOK_AGE_SECONDS:
                stats["recent_hooks"] += 1
                if hook["range_pct"] >= M5_MIN_HOOK_RANGE_PCT:
                    stats["range_valid"] += 1
                    all_recent_valid.append({"hook": hook, "rows": rows, "pivots": pivots, "tp_touch": touch})
                    if not touch["touched"]:
                        stats["valid_before_tp"] += 1

        time.sleep(SCAN_SLEEP_SECONDS)

    # Create paper signals from the best/current valid hook candidates.
    # One signal per unique hook key. Existing DB prevents re-entry.
    for item in sorted(all_recent_valid, key=lambda x: x["hook"]["confirmed_time"]):
        hook = item["hook"]
        touch = item["tp_touch"]
        if touch["touched"]:
            continue

        sl = find_protective_sl(hook, item["pivots"])
        if sl is None:
            continue
        stats["sl_found"] += 1

        if not geometry_valid(hook, sl):
            continue
        stats["geometry_valid"] += 1

        if trade_exists(hook["hook_key"]):
            stats["duplicate"] += 1
            stats["trade_duplicates"] += 1
            continue

        if open_trade_count() >= MAX_OPEN_TRADES:
            stats["max_open"] += 1
            continue

        stats["signal_ready"] += 1
        if save_trade(hook, sl):
            stats["signals"] += 1
            current_price = item["rows"][-1]["close"] if item.get("rows") else hook["final"]["price"]
            send_trade_alert(hook, sl, current_price)

    # Monitor newly created trades once more using the same candle data.
    monitor_open_trades(rows_by_symbol, stats)

    age_counts = count_hooks_by_age(all_hooks_for_age)
    newest = max(all_hooks_for_age, key=lambda x: x["confirmed_time"], default=None)

    perf = performance_summary()
    runtime = time.time() - scan_started

    lines = [
        "🔎 NDS M5 DIAGNOSTIC",
        f"Version: {VERSION}",
        f"Time: {utc_iso()}",
        f"Runtime: {runtime:.1f}s",
        "",
        "━━━ ASSETS ━━━",
        f"Scanned: {stats['scanned']}",
        "",
        "━━━ M5 ━━━",
        f"Requests: {stats['requests']}",
        f"Data OK: {stats['data_ok']}",
        f"Data Error: {stats['data_error']}",
        f"Empty: {stats['empty']}",
        f"Short: {stats['short']}",
        f"Pivot Points: {stats['pivot_points']}",
        f"Hooks: {stats['hooks']}",
        f"Confirmed Hooks: {stats['confirmed_hooks']}",
        f"New Hooks Since Last Run: {stats['new_hooks']}",
        f"Recent Hooks ≤6h: {age_counts['6h']}",
        f"Recent ≤12h: {age_counts['12h']}",
        f"Recent ≤24h: {age_counts['24h']}",
        f"Recent ≤48h: {age_counts['48h']}",
        f"Recent ≤72h: {age_counts['72h']}",
        f"Range Valid Hooks ≥{M5_MIN_HOOK_RANGE_PCT:.2f}%: {stats['range_valid']}",
        f"Recent + Range Valid + TP Untouched: {stats['valid_before_tp']}",
        f"TP Already Touched After H3/L3: {stats['tp_touched']}",
        f"SL Found: {stats['sl_found']}",
        f"Geometry Valid: {stats['geometry_valid']}",
        f"Duplicate: {stats['duplicate']}",
        f"Max Open: {stats['max_open']}",
        f"Signal Ready: {stats['signal_ready']}",
        f"Node Spacing: ≥{MIN_NODE_CANDLES} candles ({MIN_NODE_CANDLES * M5_INTERVAL_MINUTES} min)",
        f"Hook Charts Sent: {stats['hook_charts_sent']}",
        f"Hook Chart Errors: {stats['hook_chart_errors']}",
        f"Signals: {stats['signals']}",
        f"Trade Duplicates: {stats['trade_duplicates']}",
        "",
        "━━━ PAPER TRADES ━━━",
        f"Open Trades: {perf['open']}",
        f"Closed Trades: {perf['closed']}",
        f"Trades checked this run: {stats['trades_checked']}",
        f"Trades closed this run: {stats['trades_closed']}",
        f"TP: {perf['tp']}  SL: {perf['sl']}  PnL: {perf['pnl']:+.2f}%",
    ]

    if newest:
        age_h = max(0.0, (utc_now() - newest["confirmed_time"]).total_seconds() / 3600.0)
        lines += [
            "",
            "━━━ NEWEST HOOK IN CURRENT WINDOW ━━━",
            f"{newest['symbol']} {newest['direction']}",
            f"Confirmed: {utc_iso(newest['confirmed_time'])}",
            f"Age: {age_h:.1f}h",
            f"H3/L3: {fmt_price(newest['final']['price'])}",
            f"TP: {fmt_price(newest['tp'])}",
        ]

    report_text = "\n".join(lines)
    print(report_text, flush=True)
    telegram_send_text(report_text)


if __name__ == "__main__":
    main()
