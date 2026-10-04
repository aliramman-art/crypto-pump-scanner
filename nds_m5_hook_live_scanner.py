# ============================================================
# NDS M5 HOOK LIVE SCANNER
# VERSION 5.5.2
# M5 ONLY - PAPER TRADING ONLY - NO REAL ORDERS
#
# Changes in this version:
#   1) Minimum node spacing reduced from 10 -> 5 M5 candles.
#   2) Every confirmed Hook chart reports whether TP 86.4%
#      was already touched after H3/L3.
#   3) TP-touched rule remains a hard trade-signal blocker.
#
# Strategy is otherwise unchanged.
# ============================================================

import os
import time
import sqlite3
import traceback
from datetime import datetime, timezone

import requests
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

VERSION = "5.5.2"
REAL_TRADING = False

KRAKEN_FUTURES_URL = "https://futures.kraken.com/derivatives/api/v3"
KRAKEN_CHART_URL = "https://futures.kraken.com/api/charts/v1"

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

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or ""
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT") or ""

DIAG = {
    "assets_scanned": 0,
    "m5_requests": 0,
    "m5_data_ok": 0,
    "m5_data_error": 0,
    "m5_empty": 0,
    "m5_short": 0,
    "m5_pivots": 0,
    "m5_hooks": 0,
    "m5_confirmed": 0,
    "m5_recent": 0,
    "m5_range_valid": 0,
    "m5_valid_hooks": 0,
    "m5_tp_touched": 0,
    "m5_sl_found": 0,
    "m5_geometry_valid": 0,
    "m5_duplicate": 0,
    "m5_max_open": 0,
    "signal_ready": 0,
    "hook_charts_sent": 0,
    "hook_chart_errors": 0,
    "signals": 0,
    "duplicate_signals": 0,
    "max_open": 0,
    "api_errors": [],
}

START_TIME = time.time()


def utc_now():
    return datetime.now(timezone.utc)


def utc_now_ts():
    return int(utc_now().timestamp())


def fmt_ts(ts):
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return "-"


def fmt_price(value):
    try:
        value = float(value)
        if value >= 1000:
            return f"{value:.2f}"
        if value >= 1:
            return f"{value:.5f}"
        if value >= 0.01:
            return f"{value:.7f}"
        return f"{value:.10f}"
    except Exception:
        return "-"


def telegram_enabled():
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def telegram_send(text):
    if not telegram_enabled():
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if not r.ok:
            DIAG["api_errors"].append(f"Telegram text HTTP {r.status_code}")
        return r.ok
    except Exception as e:
        DIAG["api_errors"].append(f"Telegram text: {str(e)[:160]}")
        return False


def telegram_send_photo(photo_path, caption):
    if not telegram_enabled() or not photo_path or not os.path.exists(photo_path):
        return False
    try:
        with open(photo_path, "rb") as photo:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                files={"photo": photo},
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption[:1024],
                    "parse_mode": "HTML",
                },
                timeout=REQUEST_TIMEOUT,
            )
        if not r.ok:
            DIAG["api_errors"].append(f"Telegram photo HTTP {r.status_code}")
        return r.ok
    except Exception as e:
        DIAG["api_errors"].append(f"Telegram photo: {str(e)[:160]}")
        return False


def get_futures_instruments():
    try:
        r = requests.get(f"{KRAKEN_FUTURES_URL}/instruments", timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        instruments = r.json().get("instruments", [])
        symbols = []
        for item in instruments:
            symbol = str(item.get("symbol") or item.get("instrument") or "")
            if not symbol.startswith("PF_") or "USD" not in symbol:
                continue
            if item.get("tradeable", True) is False:
                continue
            symbols.append(symbol)
        return list(dict.fromkeys(symbols))[:TARGET_ASSETS]
    except Exception as e:
        DIAG["api_errors"].append(f"Instruments: {str(e)[:180]}")
        return []


def get_candles(symbol, interval, count):
    url = f"{KRAKEN_CHART_URL}/trade/{symbol}/{interval}"
    try:
        r = requests.get(url, params={"count": int(count)}, timeout=REQUEST_TIMEOUT)
        if not r.ok:
            DIAG["api_errors"].append(f"{symbol} {interval} HTTP {r.status_code}")
            return None

        candles = r.json().get("candles", [])
        rows = []
        for c in candles:
            if isinstance(c, dict):
                ts = c.get("time", c.get("timestamp", c.get("t")))
                op = c.get("open", c.get("o"))
                hi = c.get("high", c.get("h"))
                lo = c.get("low", c.get("l"))
                cl = c.get("close", c.get("c"))
                vol = c.get("volume", c.get("v", 0))
            elif isinstance(c, (list, tuple)) and len(c) >= 5:
                ts, op, hi, lo, cl = c[:5]
                vol = c[5] if len(c) > 5 else 0
            else:
                continue
            rows.append([ts, op, hi, lo, cl, vol])

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
        for col in ["time", "open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["time", "open", "high", "low", "close"])
        if df.empty:
            return df

        if df["time"].max() > 10_000_000_000:
            df["time"] = df["time"] / 1000.0
        df["time"] = df["time"].astype(float)
        return df.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    except Exception as e:
        DIAG["api_errors"].append(f"{symbol} {interval}: {str(e)[:180]}")
        return None


def find_pivots(df):
    highs, lows = [], []
    if df is None or df.empty or len(df) < PIVOT_LEFT + PIVOT_RIGHT + 1:
        return highs, lows

    for i in range(PIVOT_LEFT, len(df) - PIVOT_RIGHT):
        hi = float(df.iloc[i]["high"])
        lo = float(df.iloc[i]["low"])
        left_hi = [float(df.iloc[j]["high"]) for j in range(i - PIVOT_LEFT, i)]
        right_hi = [float(df.iloc[j]["high"]) for j in range(i + 1, i + PIVOT_RIGHT + 1)]
        left_lo = [float(df.iloc[j]["low"]) for j in range(i - PIVOT_LEFT, i)]
        right_lo = [float(df.iloc[j]["low"]) for j in range(i + 1, i + PIVOT_RIGHT + 1)]

        if all(hi > x for x in left_hi) and all(hi >= x for x in right_hi):
            highs.append({"type": "H", "index": i, "time": float(df.iloc[i]["time"]), "price": hi})
        if all(lo < x for x in left_lo) and all(lo <= x for x in right_lo):
            lows.append({"type": "L", "index": i, "time": float(df.iloc[i]["time"]), "price": lo})

    return highs, lows


def build_ordered_pivots(highs, lows):
    pivots = sorted(highs + lows, key=lambda p: (p["time"], p["index"]))
    result = []
    for p in pivots:
        if not result or p["type"] != result[-1]["type"]:
            result.append(p)
        elif (p["type"] == "H" and p["price"] >= result[-1]["price"]) or (
            p["type"] == "L" and p["price"] <= result[-1]["price"]
        ):
            result[-1] = p
    return result


def nodes_spaced(pivots):
    if len(pivots) < 2:
        return False
    return all((pivots[i]["index"] - pivots[i - 1]["index"]) >= MIN_NODE_CANDLES for i in range(1, len(pivots)))


def detect_hooks(df, interval_minutes):
    if df is None or df.empty:
        return []

    highs, lows = find_pivots(df)
    ordered = build_ordered_pivots(highs, lows)
    hooks = []
    confirmation_seconds = interval_minutes * 60 * PIVOT_RIGHT

    if len(ordered) < 6:
        return hooks

    for i in range(len(ordered) - 5):
        p = ordered[i:i + 6]

        if not nodes_spaced(p):
            continue

        kinds = [x["type"] for x in p]

        # SHORT: START(L) -> H1 -> L1 -> H2 -> L2 -> H3
        if kinds == ["L", "H", "L", "H", "L", "H"]:
            start, h1, l1, h2, l2, h3 = p
            if not (h2["price"] > h1["price"] and l2["price"] < l1["price"] and h3["price"] > h2["price"]):
                continue
            if not (start["price"] < l1["price"] and start["price"] < l2["price"]):
                continue

            rng = h3["price"] - start["price"]
            if rng <= 0 or start["price"] <= 0:
                continue

            hooks.append({
                "direction": "SHORT",
                "start": start,
                "h1": h1,
                "l1": l1,
                "h2": h2,
                "l2": l2,
                "h3": h3,
                "final": h3,
                "entry": h3["price"],
                "tp": h3["price"] - NDS_RETRACE * rng,
                "range_pct": rng / start["price"] * 100.0,
                "confirmation_time": h3["time"] + confirmation_seconds,
                "created_time": h3["time"],
            })

        # LONG: START(H) -> L1 -> H1 -> L2 -> H2 -> L3
        if kinds == ["H", "L", "H", "L", "H", "L"]:
            start, l1, h1, l2, h2, l3 = p
            if not (l2["price"] < l1["price"] and h2["price"] > h1["price"] and l3["price"] < l2["price"]):
                continue
            if not (start["price"] > h1["price"] and start["price"] > h2["price"]):
                continue

            rng = start["price"] - l3["price"]
            if rng <= 0 or start["price"] <= 0:
                continue

            hooks.append({
                "direction": "LONG",
                "start": start,
                "l1": l1,
                "h1": h1,
                "l2": l2,
                "h2": h2,
                "l3": l3,
                "final": l3,
                "entry": l3["price"],
                "tp": l3["price"] + NDS_RETRACE * rng,
                "range_pct": rng / start["price"] * 100.0,
                "confirmation_time": l3["time"] + confirmation_seconds,
                "created_time": l3["time"],
            })

    return hooks


def hook_id(symbol, hook):
    final = hook["final"]
    return f"{symbol}|{hook['direction']}|{int(final['time'])}|{float(final['price']):.12f}"


def hook_is_confirmed(hook, now_ts=None):
    if now_ts is None:
        now_ts = utc_now_ts()
    return float(hook["confirmation_time"]) <= float(now_ts)


def hook_is_recent(hook, now_ts=None):
    if now_ts is None:
        now_ts = utc_now_ts()
    age = float(now_ts) - float(hook["confirmation_time"])
    return 0 <= age <= M5_MAX_HOOK_AGE_SECONDS


def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(CHART_DIR, exist_ok=True)
    conn = db_connect()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS hooks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hook_key TEXT UNIQUE,
        symbol TEXT,
        direction TEXT,
        start_price REAL,
        h1_price REAL,
        l1_price REAL,
        h2_price REAL,
        l2_price REAL,
        final_price REAL,
        entry REAL,
        tp REAL,
        sl REAL,
        range_pct REAL,
        confirmation_time INTEGER,
        created_time INTEGER,
        detected_time INTEGER,
        chart_path TEXT
    );

    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hook_key TEXT UNIQUE,
        symbol TEXT,
        direction TEXT,
        entry REAL,
        sl REAL,
        tp REAL,
        opened_at INTEGER,
        closed_at INTEGER,
        close_price REAL,
        status TEXT,
        pnl_pct REAL,
        pnl_price REAL,
        chart_path TEXT
    );

    CREATE TABLE IF NOT EXISTS hook_chart_notifications (
        hook_key TEXT PRIMARY KEY,
        sent_at INTEGER,
        chart_path TEXT
    );
    """)
    conn.commit()
    conn.close()


def notification_exists(key):
    conn = db_connect()
    row = conn.execute(
        "SELECT 1 FROM hook_chart_notifications WHERE hook_key=? LIMIT 1", (key,)
    ).fetchone()
    conn.close()
    return row is not None


def mark_notification_sent(key, path):
    conn = db_connect()
    conn.execute(
        "INSERT OR IGNORE INTO hook_chart_notifications(hook_key,sent_at,chart_path) VALUES(?,?,?)",
        (key, utc_now_ts(), path),
    )
    conn.commit()
    conn.close()


def hook_exists(key):
    conn = db_connect()
    row = conn.execute("SELECT 1 FROM hooks WHERE hook_key=? LIMIT 1", (key,)).fetchone()
    conn.close()
    return row is not None


def trade_exists(key):
    conn = db_connect()
    row = conn.execute("SELECT 1 FROM trades WHERE hook_key=? LIMIT 1", (key,)).fetchone()
    conn.close()
    return row is not None


def open_trade_count():
    conn = db_connect()
    row = conn.execute("SELECT COUNT(*) AS c FROM trades WHERE status='OPEN'").fetchone()
    conn.close()
    return int(row["c"])


def save_hook(symbol, hook, sl, chart_path=None):
    key = hook_id(symbol, hook)
    conn = db_connect()
    conn.execute("""
        INSERT OR IGNORE INTO hooks (
            hook_key,symbol,direction,start_price,h1_price,l1_price,h2_price,l2_price,
            final_price,entry,tp,sl,range_pct,confirmation_time,created_time,detected_time,chart_path
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        key,
        symbol,
        hook["direction"],
        hook["start"]["price"],
        hook.get("h1", {}).get("price"),
        hook.get("l1", {}).get("price"),
        hook.get("h2", {}).get("price"),
        hook.get("l2", {}).get("price"),
        hook["final"]["price"],
        hook["entry"],
        hook["tp"],
        sl,
        hook["range_pct"],
        int(hook["confirmation_time"]),
        int(hook["created_time"]),
        utc_now_ts(),
        chart_path,
    ))
    conn.commit()
    conn.close()
    return key


def save_trade(key, symbol, hook, sl, chart_path=None):
    conn = db_connect()
    conn.execute("""
        INSERT OR IGNORE INTO trades (
            hook_key,symbol,direction,entry,sl,tp,opened_at,status,chart_path
        ) VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        key,
        symbol,
        hook["direction"],
        hook["entry"],
        sl,
        hook["tp"],
        utc_now_ts(),
        "OPEN",
        chart_path,
    ))
    conn.commit()
    conn.close()


def calculate_sl(df, hook):
    """Nearest previous valid M5 pivot on the protective side, before H3/L3."""
    highs, lows = find_pivots(df)
    final_time = float(hook["final"]["time"])
    entry = float(hook["entry"])

    if hook["direction"] == "SHORT":
        candidates = [
            p for p in highs
            if float(p["time"]) < final_time and float(p["price"]) > entry
        ]
    else:
        candidates = [
            p for p in lows
            if float(p["time"]) < final_time and float(p["price"]) < entry
        ]

    candidates.sort(key=lambda p: float(p["time"]), reverse=True)
    return float(candidates[0]["price"]) if candidates else None


def tp_first_touch(df, hook):
    future = df[df["time"] > float(hook["final"]["time"])]
    if future.empty:
        return None

    if hook["direction"] == "SHORT":
        hits = future[future["low"] <= float(hook["tp"])]
    else:
        hits = future[future["high"] >= float(hook["tp"])]

    if hits.empty:
        return None
    return float(hits.iloc[0]["time"])


def tp_already_touched(df, hook):
    return tp_first_touch(df, hook) is not None


def tp_status_text(df, hook):
    touch_ts = tp_first_touch(df, hook)
    node_label = "H3" if hook["direction"] == "SHORT" else "L3"
    if touch_ts is None:
        return f"{node_label} → TP 86.4%: ✅ NOT TOUCHED", None
    return f"{node_label} → TP 86.4%: ❌ TOUCHED", touch_ts


def calculate_heikin_ashi(df):
    ha = df[["time", "open", "high", "low", "close"]].copy().reset_index(drop=True)
    ha["ha_close"] = (ha["open"] + ha["high"] + ha["low"] + ha["close"]) / 4.0

    ha_open = []
    for i in range(len(ha)):
        if i == 0:
            value = (float(ha.iloc[i]["open"]) + float(ha.iloc[i]["close"])) / 2.0
        else:
            value = (float(ha_open[i - 1]) + float(ha.iloc[i - 1]["ha_close"])) / 2.0
        ha_open.append(value)

    ha["ha_open"] = ha_open
    ha["ha_high"] = ha[["high", "ha_open", "ha_close"]].max(axis=1)
    ha["ha_low"] = ha[["low", "ha_open", "ha_close"]].min(axis=1)
    return ha


def create_hook_chart(symbol, df, hook, sl, path_prefix="hook", timeframe="M5"):
    try:
        chart_df = df.tail(CHART_CANDLES).copy().reset_index(drop=True)
        if chart_df.empty:
            return None

        ha = calculate_heikin_ashi(chart_df)
        fig, ax = plt.subplots(figsize=(15, 8))
        x_num = matplotlib.dates.date2num(
            pd.to_datetime(ha["time"], unit="s", utc=True).dt.to_pydatetime()
        )
        width = max((M5_INTERVAL_MINUTES / 1440.0) * 0.72, 0.0008)

        for i, row in ha.iterrows():
            x0 = x_num[i]
            o = float(row["ha_open"])
            c = float(row["ha_close"])
            hi = float(row["ha_high"])
            lo = float(row["ha_low"])
            up = c >= o
            body_low = min(o, c)
            body_high = max(o, c)
            body_height = max(body_high - body_low, max(abs(c), 1.0) * 1e-7)
            candle_color = "#26a69a" if up else "#ef5350"

            ax.vlines(x0, lo, hi, color="black", linewidth=0.8, alpha=0.9, zorder=2)
            rect = plt.Rectangle(
                (x0 - width / 2.0, body_low),
                width,
                body_height,
                facecolor=candle_color,
                edgecolor="black",
                linewidth=0.6,
                zorder=3,
            )
            ax.add_patch(rect)

        if hook["direction"] == "SHORT":
            points = [hook["start"], hook["h1"], hook["l1"], hook["h2"], hook["l2"], hook["h3"]]
            labels = ["START", "H1", "L1", "H2", "L2", "H3"]
            offsets = [(0, -28), (0, 18), (0, -30), (0, 18), (0, -30), (0, 22)]
        else:
            points = [hook["start"], hook["l1"], hook["h1"], hook["l2"], hook["h2"], hook["l3"]]
            labels = ["START", "L1", "H1", "L2", "H2", "L3"]
            offsets = [(0, 24), (0, -30), (0, 18), (0, -30), (0, 18), (0, -32)]

        px = [datetime.fromtimestamp(p["time"], tz=timezone.utc) for p in points]
        py = [p["price"] for p in points]
        ax.plot(px, py, marker="o", linewidth=2.1, color="royalblue", label="NDS Hook", zorder=5)

        for p, label, offset in zip(points, labels, offsets):
            dt = datetime.fromtimestamp(p["time"], tz=timezone.utc)
            emphasis = label in {"START", "H3", "L3"}
            ax.annotate(
                f"{label}\n{fmt_price(p['price'])}",
                (dt, p["price"]),
                xytext=offset,
                textcoords="offset points",
                ha="center",
                va="center",
                fontsize=9 if emphasis else 8,
                fontweight="bold" if emphasis else "normal",
                bbox=dict(
                    boxstyle="round,pad=0.22",
                    fc="white",
                    ec="black" if emphasis else "gray",
                    alpha=0.9,
                ),
                arrowprops=dict(arrowstyle="-", color="gray", linewidth=0.7, alpha=0.7),
                zorder=10,
            )

        entry = float(hook["entry"])
        tp = float(hook["tp"])
        chart_start_dt = pd.to_datetime(ha["time"].iloc[0], unit="s", utc=True).to_pydatetime()
        chart_end_dt = pd.to_datetime(ha["time"].iloc[-1], unit="s", utc=True).to_pydatetime()
        confirmation_dt = datetime.fromtimestamp(hook["confirmation_time"], tz=timezone.utc)
        start_x = min(chart_start_dt, px[0])
        end_x = max(chart_end_dt, confirmation_dt)

        ax.hlines(entry, start_x, end_x, linestyles="--", linewidth=1.5,
                  label=f"ENTRY {fmt_price(entry)}", color="darkorange", zorder=4)
        ax.hlines(tp, start_x, end_x, linestyles="--", linewidth=1.6,
                  label=f"TP 86.4% {fmt_price(tp)}", color="seagreen", zorder=4)
        if sl is not None:
            ax.hlines(float(sl), start_x, end_x, linestyles="--", linewidth=1.5,
                      label=f"SL {fmt_price(sl)}", color="crimson", zorder=4)
        ax.axvline(confirmation_dt, linestyle=":", linewidth=1.2,
                   label="CONFIRMED", color="purple", zorder=4)

        ax.set_title(f"NDS M5 | {symbol} | {hook['direction']} | CONFIRMED HOOK | HEIKIN ASHI")
        ax.set_xlabel("Time UTC")
        ax.set_ylabel("Price")
        ax.grid(alpha=0.22)
        ax.legend(loc="best", fontsize=8)
        ax.set_xlim(min(start_x, px[0]), max(end_x, px[-1]))
        fig.autofmt_xdate()

        safe_symbol = symbol.replace("/", "_").replace(":", "_")
        path = os.path.join(
            CHART_DIR,
            f"{path_prefix}_{safe_symbol}_{hook['direction']}_{int(hook['final']['time'])}.png",
        )
        plt.tight_layout()
        plt.savefig(path, dpi=150)
        plt.close(fig)
        return path
    except Exception as e:
        DIAG["api_errors"].append(f"Chart {symbol}: {str(e)[:180]}")
        try:
            plt.close("all")
        except Exception:
            pass
        return None


def send_detected_m5_hook_charts(symbol, df, hooks):
    now_ts = utc_now_ts()

    for hook in hooks:
        if not hook_is_confirmed(hook, now_ts):
            continue

        DIAG["m5_confirmed"] += 1
        key = hook_id(symbol, hook)

        status_text, touch_ts = tp_status_text(df, hook)
        sl = calculate_sl(df, hook)

        chart_path = create_hook_chart(symbol, df, hook, sl, "detected_hook", timeframe="M5")
        if not chart_path:
            DIAG["hook_chart_errors"] += 1
            continue

        if hook_is_recent(hook, now_ts):
            DIAG["m5_recent"] += 1
        if hook["range_pct"] >= M5_MIN_HOOK_RANGE_PCT:
            DIAG["m5_range_valid"] += 1

        if touch_ts is None:
            tp_line = f"{status_text}"
            touch_line = "TP touch time: -"
        else:
            tp_line = status_text
            touch_line = f"TP first touched: {fmt_ts(touch_ts)}"

        final_label = "H3" if hook["direction"] == "SHORT" else "L3"
        caption = (
            f"🔎 <b>NDS M5 CONFIRMED HOOK</b>\n"
            f"<b>{symbol}</b>\n"
            f"Direction: <b>{hook['direction']}</b>\n"
            f"START: {fmt_price(hook['start']['price'])}\n"
            f"{final_label}: {fmt_price(hook['final']['price'])}\n"
            f"TP 86.4%: <b>{fmt_price(hook['tp'])}</b>\n"
            f"{tp_line}\n"
            f"{touch_line}\n"
            f"SL reference: {fmt_price(sl)}\n"
            f"Hook range: {hook['range_pct']:.2f}%\n"
            f"Confirmed: {fmt_ts(hook['confirmation_time'])}\n"
            f"Node spacing: ≥ {MIN_NODE_CANDLES} M5 candles\n"
            f"<b>Heikin Ashi chart; NDS values use real OHLC.</b>\n"
            f"<b>Chart only; trade signal has separate filters.</b>\n"
            f"<b>PAPER TRADING ONLY</b>"
        )

        if notification_exists(key):
            continue

        if telegram_send_photo(chart_path, caption):
            mark_notification_sent(key, chart_path)
            DIAG["hook_charts_sent"] += 1
        else:
            DIAG["hook_chart_errors"] += 1
            DIAG["api_errors"].append(f"Hook chart not sent: {symbol} {hook['direction']}")


def build_signal_message(symbol, hook, sl):
    direction = hook["direction"]
    emoji = "🔴" if direction == "SHORT" else "🟢"
    entry = float(hook["entry"])
    tp = float(hook["tp"])
    sl = float(sl)

    if direction == "SHORT":
        risk_pct = (sl - entry) / entry * 100.0
        reward_pct = (entry - tp) / entry * 100.0
    else:
        risk_pct = (entry - sl) / entry * 100.0
        reward_pct = (tp - entry) / entry * 100.0

    return (
        f"{emoji} <b>NDS {direction}</b>\n"
        f"<b>{symbol}</b>\n\n"
        f"Entry: <b>{fmt_price(entry)}</b>\n"
        f"SL: <b>{fmt_price(sl)}</b> ({risk_pct:+.2f}%)\n"
        f"TP 86.4%: <b>{fmt_price(tp)}</b> ({reward_pct:+.2f}%)\n\n"
        f"Hook Range: {hook['range_pct']:.2f}%\n"
        f"Confirmed: {fmt_ts(hook['confirmation_time'])}\n"
        f"After {'H3' if direction == 'SHORT' else 'L3'} → TP: ✅ NOT TOUCHED\n"
        f"Node spacing: ≥ {MIN_NODE_CANDLES} M5 candles\n"
        f"<b>Heikin Ashi chart; NDS values use real OHLC.</b>\n"
        f"<b>PAPER TRADING ONLY</b>"
    )


def process_symbol(symbol):
    DIAG["m5_requests"] += 1
    df = get_candles(symbol, M5_INTERVAL, M5_CANDLES)

    if df is None:
        DIAG["m5_data_error"] += 1
        return
    if df.empty:
        DIAG["m5_empty"] += 1
        return
    if len(df) < 100:
        DIAG["m5_short"] += 1
        return

    DIAG["m5_data_ok"] += 1

    highs, lows = find_pivots(df)
    DIAG["m5_pivots"] += len(highs) + len(lows)

    hooks = detect_hooks(df, M5_INTERVAL_MINUTES)
    DIAG["m5_hooks"] += len(hooks)

    # Every confirmed hook gets a chart, independent of signal filters.
    send_detected_m5_hook_charts(symbol, df, hooks)

    now_ts = utc_now_ts()
    candidates = []

    for hook in hooks:
        if not hook_is_confirmed(hook, now_ts):
            continue
        if not hook_is_recent(hook, now_ts):
            continue
        if hook["range_pct"] < M5_MIN_HOOK_RANGE_PCT:
            continue

        DIAG["m5_valid_hooks"] += 1

        # HARD RULE: if 86.4% TP was already touched after H3/L3,
        # the Hook is never allowed to create a trade signal.
        if tp_already_touched(df, hook):
            DIAG["m5_tp_touched"] += 1
            continue

        candidates.append(hook)

    if not candidates:
        return

    candidates.sort(
        key=lambda h: (h["confirmation_time"], h["final"]["time"]),
        reverse=True,
    )

    for hook in candidates:
        sl = calculate_sl(df, hook)
        if sl is None:
            continue
        DIAG["m5_sl_found"] += 1

        entry = float(hook["entry"])
        tp = float(hook["tp"])
        sl = float(sl)

        if hook["direction"] == "SHORT":
            geometry_ok = sl > entry > tp
        else:
            geometry_ok = sl < entry < tp

        if not geometry_ok:
            continue
        DIAG["m5_geometry_valid"] += 1

        key = hook_id(symbol, hook)
        if hook_exists(key) or trade_exists(key):
            DIAG["m5_duplicate"] += 1
            DIAG["duplicate_signals"] += 1
            continue

        if open_trade_count() >= MAX_OPEN_TRADES:
            DIAG["m5_max_open"] += 1
            DIAG["max_open"] += 1
            return

        DIAG["signal_ready"] += 1

        chart_path = create_hook_chart(symbol, df, hook, sl, "signal_m5", timeframe="M5")
        save_hook(symbol, hook, sl, chart_path)
        save_trade(key, symbol, hook, sl, chart_path)
        DIAG["signals"] += 1

        telegram_send(build_signal_message(symbol, hook, sl))

        if chart_path:
            telegram_send_photo(
                chart_path,
                f"📍 <b>NDS {hook['direction']} SIGNAL | M5</b>\n"
                f"<b>{symbol}</b>\n"
                f"Entry: {fmt_price(entry)}\n"
                f"TP 86.4%: {fmt_price(tp)}\n"
                f"SL: {fmt_price(sl)}\n"
                f"After {'H3' if hook['direction'] == 'SHORT' else 'L3'} → TP: ✅ NOT TOUCHED\n"
                f"Heikin Ashi chart; NDS values use real OHLC.\n"
                f"PAPER TRADING ONLY"
            )

        # One signal per symbol per scan.
        return


def get_open_trades():
    conn = db_connect()
    rows = conn.execute(
        "SELECT * FROM trades WHERE status='OPEN' ORDER BY opened_at ASC"
    ).fetchall()
    conn.close()
    return rows


def get_current_price(symbol):
    try:
        r = requests.get(f"{KRAKEN_FUTURES_URL}/tickers", timeout=REQUEST_TIMEOUT)
        if r.ok:
            for ticker in r.json().get("tickers", []):
                if not isinstance(ticker, dict):
                    continue
                tsymbol = str(
                    ticker.get("symbol")
                    or ticker.get("pair")
                    or ticker.get("instrument")
                    or ""
                )
                if tsymbol != symbol:
                    continue
                for key in ("last", "lastPrice", "markPrice", "price"):
                    if ticker.get(key) is not None:
                        return float(ticker[key])
    except Exception as e:
        DIAG["api_errors"].append(f"Ticker {symbol}: {str(e)[:160]}")

    df = get_candles(symbol, M5_INTERVAL, 2)
    if df is None or df.empty:
        return None
    return float(df.iloc[-1]["close"])


def trade_metrics(entry, sl, tp, direction, current):
    if direction == "LONG":
        pnl_pct = (current - entry) / entry * 100.0
        tp_pct = (tp - entry) / entry * 100.0
        sl_pct = (sl - entry) / entry * 100.0
    else:
        pnl_pct = (entry - current) / entry * 100.0
        tp_pct = (entry - tp) / entry * 100.0
        sl_pct = (entry - sl) / entry * 100.0
    return pnl_pct, tp_pct, sl_pct


def open_trades_report_lines():
    rows = get_open_trades()
    lines = ["━━━ <b>OPEN TRADES</b> ━━━"]
    if not rows:
        return lines + ["None"]

    for trade in rows:
        direction = trade["direction"]
        symbol = trade["symbol"]
        entry, sl, tp = map(float, [trade["entry"], trade["sl"], trade["tp"]])
        current = get_current_price(symbol)
        icon = "🟢" if direction == "LONG" else "🔴"

        if current is None:
            lines.extend([
                f"{icon} <b>{symbol} {direction}</b>",
                f"Entry: {fmt_price(entry)}",
                "Current: -",
                f"TP: {fmt_price(tp)}",
                f"SL: {fmt_price(sl)}",
                "",
            ])
            continue

        pnl_pct, tp_pct, sl_pct = trade_metrics(entry, sl, tp, direction, current)
        lines.extend([
            f"{icon} <b>{symbol} {direction}</b>",
            f"Entry: {fmt_price(entry)}",
            f"Current: <b>{fmt_price(current)} ({pnl_pct:+.2f}%)</b>",
            f"TP: <b>{fmt_price(tp)} ({tp_pct:+.2f}%)</b>",
            f"SL: <b>{fmt_price(sl)} ({sl_pct:+.2f}%)</b>",
            "",
        ])

    if lines[-1] == "":
        lines.pop()
    return lines


def close_trade(trade, close_price, pnl_pct, pnl_price, reason):
    conn = db_connect()
    conn.execute("""
        UPDATE trades
        SET closed_at=?,close_price=?,status=?,pnl_pct=?,pnl_price=?
        WHERE id=?
    """, (
        utc_now_ts(),
        close_price,
        reason,
        pnl_pct,
        pnl_price,
        trade["id"],
    ))
    conn.commit()
    conn.close()


def monitor_open_trades():
    for trade in get_open_trades():
        symbol = trade["symbol"]
        direction = trade["direction"]
        entry = float(trade["entry"])
        sl = float(trade["sl"])
        tp = float(trade["tp"])

        try:
            df = get_candles(symbol, M5_INTERVAL, 30)
            if df is None or df.empty:
                continue

            future = df[df["time"] >= float(trade["opened_at"])]
            if future.empty:
                continue

            close_price = None
            reason = None

            for _, candle in future.iterrows():
                high = float(candle["high"])
                low = float(candle["low"])

                if direction == "SHORT":
                    hit_sl = high >= sl
                    hit_tp = low <= tp
                else:
                    hit_sl = low <= sl
                    hit_tp = high >= tp

                if hit_sl:
                    close_price, reason = sl, "SL"
                    break
                if hit_tp:
                    close_price, reason = tp, "TP"
                    break

            if reason is None:
                continue

            pnl_price = entry - close_price if direction == "SHORT" else close_price - entry
            pnl_pct = pnl_price / entry * 100.0 if entry else 0.0
            close_trade(trade, close_price, pnl_pct, pnl_price, reason)

            emoji = "✅" if pnl_pct >= 0 else "❌"
            telegram_send(
                f"{emoji} <b>NDS {direction} CLOSED</b>\n"
                f"<b>{symbol}</b>\n\n"
                f"Entry: <b>{fmt_price(entry)}</b>\n"
                f"Close: <b>{fmt_price(close_price)}</b>\n"
                f"Result: <b>{reason}</b>\n"
                f"PnL: <b>{pnl_pct:+.2f}%</b>"
            )
        except Exception as e:
            DIAG["api_errors"].append(f"Monitor {symbol}: {str(e)[:180]}")


def performance_summary():
    conn = db_connect()
    total = conn.execute("SELECT COUNT(*) c FROM trades WHERE status!='OPEN'").fetchone()["c"]
    wins = conn.execute("SELECT COUNT(*) c FROM trades WHERE status='TP'").fetchone()["c"]
    losses = conn.execute("SELECT COUNT(*) c FROM trades WHERE status='SL'").fetchone()["c"]
    pnl = conn.execute("SELECT COALESCE(SUM(pnl_pct),0) p FROM trades WHERE status!='OPEN'").fetchone()["p"]
    conn.close()
    return {
        "total": int(total or 0),
        "wins": int(wins or 0),
        "losses": int(losses or 0),
        "pnl": float(pnl or 0),
    }


def diagnostic_text():
    perf = performance_summary()
    spacing_minutes = MIN_NODE_CANDLES * M5_INTERVAL_MINUTES

    lines = [
        "🔎 <b>NDS M5 DIAGNOSTIC</b>",
        f"Version: <b>{VERSION}</b>",
        f"Time: {utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Runtime: <b>{time.time() - START_TIME:.1f}s</b>",
        "",
        "━━━ <b>ASSETS</b> ━━━",
        f"Scanned: <b>{DIAG['assets_scanned']}</b>",
        "",
        "━━━ <b>M5</b> ━━━",
        f"Requests: {DIAG['m5_requests']}",
        f"Data OK: {DIAG['m5_data_ok']}",
        f"Data Error: {DIAG['m5_data_error']}",
        f"Empty: {DIAG['m5_empty']}",
        f"Short: {DIAG['m5_short']}",
        f"Pivot Points: {DIAG['m5_pivots']}",
        f"Hooks: {DIAG['m5_hooks']}",
        f"Confirmed Hooks: {DIAG['m5_confirmed']}",
        f"Recent Hooks ≤6h: {DIAG['m5_recent']}",
        f"Range Valid Hooks ≥{M5_MIN_HOOK_RANGE_PCT:.2f}%: {DIAG['m5_range_valid']}",
        f"Valid Hooks Before TP: {DIAG['m5_valid_hooks']}",
        f"TP Already Touched After H3/L3: {DIAG['m5_tp_touched']}",
        f"SL Found: {DIAG['m5_sl_found']}",
        f"Geometry Valid: {DIAG['m5_geometry_valid']}",
        f"Duplicate: {DIAG['m5_duplicate']}",
        f"Max Open: {DIAG['m5_max_open']}",
        f"Signal Ready: {DIAG['signal_ready']}",
        f"Node Spacing: ≥{MIN_NODE_CANDLES} candles ({spacing_minutes} min)",
        f"Hook Charts Sent: {DIAG['hook_charts_sent']}",
        f"Hook Chart Errors: {DIAG['hook_chart_errors']}",
        "",
        "━━━ <b>SIGNALS</b> ━━━",
        f"Signals: {DIAG['signals']}",
        f"Duplicates: {DIAG['duplicate_signals']}",
        f"Max Open Limit: {DIAG['max_open']}",
        f"Open Trades: <b>{open_trade_count()}</b>",
        "",
        *open_trades_report_lines(),
        "",
        "━━━ <b>PAPER PERFORMANCE</b> ━━━",
        f"Closed Trades: {perf['total']}",
        f"TP: {perf['wins']}",
        f"SL: {perf['losses']}",
        f"PnL: <b>{perf['pnl']:+.2f}%</b>",
    ]

    if DIAG["api_errors"]:
        lines += ["", "━━━ <b>ERRORS</b> ━━━"] + [f"• {e}" for e in DIAG["api_errors"][-5:]]

    lines += ["", "<b>PAPER TRADING ONLY - NO REAL ORDERS</b>"]
    return "\n".join(lines)


def main():
    try:
        init_db()
        print(f"NDS M5 Hook Scanner {VERSION}")
        print("M5 ONLY | NDS Hook | TP 86.4% | TP-touch blocker | PAPER ONLY")
        print(f"M5 pivot confirmation: {M5_INTERVAL_MINUTES}m x {PIVOT_RIGHT}")
        print(f"Minimum node spacing: {MIN_NODE_CANDLES} candles = {MIN_NODE_CANDLES * M5_INTERVAL_MINUTES} minutes")

        symbols = get_futures_instruments()
        if not symbols:
            report = "❌ <b>NDS Scanner</b>\n\nNo futures instruments found."
            print(report)
            telegram_send(report)
            return

        DIAG["assets_scanned"] = len(symbols)

        monitor_open_trades()

        for symbol in symbols:
            try:
                process_symbol(symbol)
            except Exception as e:
                DIAG["api_errors"].append(f"Process {symbol}: {str(e)[:180]}")
                traceback.print_exc()
            time.sleep(SCAN_SLEEP_SECONDS)

        monitor_open_trades()

        report = diagnostic_text()
        print("\n" + report + "\n")
        telegram_send(report)

    except Exception as e:
        traceback.print_exc()
        telegram_send(
            f"❌ <b>NDS Scanner Error</b>\n\n"
            f"{type(e).__name__}: {str(e)[:500]}"
        )


if __name__ == "__main__":
    main()
