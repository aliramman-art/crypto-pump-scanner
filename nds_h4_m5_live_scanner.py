# ============================================================
# NDS H4 -> M5 LIVE SCANNER
# VERSION 5.4.5
# PAPER TRADING ONLY - NO REAL ORDERS
# H4 direction filter -> M5 hook detection
# Sends charts for confirmed M5 hooks even when no trade signal
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

VERSION = "5.4.5"
REAL_TRADING = False

KRAKEN_FUTURES_URL = "https://futures.kraken.com/derivatives/api/v3"
KRAKEN_CHART_URL = "https://futures.kraken.com/api/charts/v1"

TARGET_ASSETS = 100
H4_INTERVAL = "4h"
M5_INTERVAL = "5m"
H4_INTERVAL_MINUTES = 240
M5_INTERVAL_MINUTES = 5
H4_CANDLES = 320
M5_CANDLES = 1500
PIVOT_LEFT = 2
PIVOT_RIGHT = 2
NDS_RETRACE = 0.864
M5_MIN_HOOK_RANGE_PCT = 0.20
MAX_HOOK_AGE_SECONDS = 6 * 60 * 60
MAX_OPEN_TRADES = 3
REQUEST_TIMEOUT = 20
SCAN_SLEEP_SECONDS = 0.25
CHART_CANDLES = 240
DB_FILE = "nds_h4_m5_v545.db"
CHART_DIR = "nds_h4_m5_charts"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or ""
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT") or ""

DIAG = {
    "assets_scanned": 0,
    "h4_requests": 0, "h4_data_ok": 0, "h4_data_error": 0,
    "h4_empty": 0, "h4_short": 0, "h4_pivots": 0,
    "h4_hooks": 0, "h4_valid_hooks": 0,
    "h4_short_direction": 0, "h4_long_direction": 0,
    "h4_filtered": 0,
    "m5_requests": 0, "m5_data_ok": 0, "m5_data_error": 0,
    "m5_empty": 0, "m5_short": 0, "m5_pivots": 0,
    "m5_hooks": 0, "m5_valid_hooks": 0,
    "hook_charts_sent": 0, "hook_chart_errors": 0,
    "signals": 0, "duplicate_signals": 0, "max_open": 0,
    "api_errors": [],
}
H4_DIRECTION = {}
H4_DATA = {}
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
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=REQUEST_TIMEOUT)
        if not r.ok:
            DIAG["api_errors"].append(f"Telegram text HTTP {r.status_code}")
        return r.ok
    except Exception as e:
        DIAG["api_errors"].append(f"Telegram text: {str(e)[:160]}")
        return False


def telegram_send_photo(photo_path, caption):
    if not telegram_enabled() or not photo_path or not os.path.exists(photo_path):
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, "rb") as photo:
            r = requests.post(
                url,
                files={"photo": photo},
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024], "parse_mode": "HTML"},
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
        left_hi = [float(df.iloc[j]["high"]) for j in range(i-PIVOT_LEFT, i)]
        right_hi = [float(df.iloc[j]["high"]) for j in range(i+1, i+PIVOT_RIGHT+1)]
        left_lo = [float(df.iloc[j]["low"]) for j in range(i-PIVOT_LEFT, i)]
        right_lo = [float(df.iloc[j]["low"]) for j in range(i+1, i+PIVOT_RIGHT+1)]
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
        p = ordered[i:i+6]
        # Positive hook / SHORT: START(L), H1, L1, H2, L2, H3
        if [x["type"] for x in p] == ["L", "H", "L", "H", "L", "H"]:
            start, h1, l1, h2, l2, h3 = p
            if not (h2["price"] > h1["price"] and l2["price"] < l1["price"] and h3["price"] > h2["price"]):
                continue
            # Positive/SHORT hook START must be the lowest point of the whole hook.
            if not (start["price"] < l1["price"] and start["price"] < l2["price"]):
                continue
            rng = h3["price"] - start["price"]
            if rng <= 0 or start["price"] <= 0:
                continue
            hooks.append({
                "direction": "SHORT", "start": start, "h1": h1, "l1": l1,
                "h2": h2, "l2": l2, "h3": h3, "final": h3,
                "entry": h3["price"], "tp": h3["price"] - NDS_RETRACE*rng,
                "range_pct": rng/start["price"]*100,
                "confirmation_time": h3["time"] + confirmation_seconds,
                "created_time": h3["time"],
            })
        # Negative hook / LONG: START(H), L1, H1, L2, H2, L3
        if [x["type"] for x in p] == ["H", "L", "H", "L", "H", "L"]:
            start, l1, h1, l2, h2, l3 = p
            if not (l2["price"] < l1["price"] and h2["price"] > h1["price"] and l3["price"] < l2["price"]):
                continue
            # Negative/LONG hook START must be the highest point of the whole hook.
            if not (start["price"] > h1["price"] and start["price"] > h2["price"]):
                continue
            rng = start["price"] - l3["price"]
            if rng <= 0 or start["price"] <= 0:
                continue
            hooks.append({
                "direction": "LONG", "start": start, "l1": l1, "h1": h1,
                "l2": l2, "h2": h2, "l3": l3, "final": l3,
                "entry": l3["price"], "tp": l3["price"] + NDS_RETRACE*rng,
                "range_pct": rng/start["price"]*100,
                "confirmation_time": l3["time"] + confirmation_seconds,
                "created_time": l3["time"],
            })
    return hooks


def hook_id(symbol, hook):
    final = hook["final"]
    return f"{symbol}|{hook['direction']}|{int(final['time'])}|{final['price']:.12f}"


def hook_is_confirmed(hook, now_ts=None):
    return hook["confirmation_time"] <= (utc_now_ts() if now_ts is None else now_ts)


def hook_is_recent(hook, now_ts=None):
    now_ts = utc_now_ts() if now_ts is None else now_ts
    age = now_ts - hook["confirmation_time"]
    return 0 <= age <= MAX_HOOK_AGE_SECONDS


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
        hook_key TEXT UNIQUE, symbol TEXT, direction TEXT,
        start_price REAL, h1_price REAL, l1_price REAL,
        h2_price REAL, l2_price REAL, final_price REAL,
        entry REAL, tp REAL, sl REAL, range_pct REAL,
        confirmation_time INTEGER, created_time INTEGER,
        detected_time INTEGER, chart_path TEXT
    );
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hook_key TEXT UNIQUE, symbol TEXT, direction TEXT,
        entry REAL, sl REAL, tp REAL, opened_at INTEGER,
        closed_at INTEGER, close_price REAL, status TEXT,
        pnl_pct REAL, pnl_price REAL, chart_path TEXT
    );
    CREATE TABLE IF NOT EXISTS hook_chart_notifications (
        hook_key TEXT PRIMARY KEY, sent_at INTEGER, chart_path TEXT
    );
    """)
    conn.commit()
    conn.close()


def notification_exists(key):
    conn = db_connect()
    row = conn.execute("SELECT 1 FROM hook_chart_notifications WHERE hook_key=?", (key,)).fetchone()
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
        key, symbol, hook["direction"], hook["start"]["price"],
        hook.get("h1", {}).get("price"), hook.get("l1", {}).get("price"),
        hook.get("h2", {}).get("price"), hook.get("l2", {}).get("price"),
        hook["final"]["price"], hook["entry"], hook["tp"], sl, hook["range_pct"],
        int(hook["confirmation_time"]), int(hook["created_time"]), utc_now_ts(), chart_path,
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
    """, (key, symbol, hook["direction"], hook["entry"], sl, hook["tp"], utc_now_ts(), "OPEN", chart_path))
    conn.commit()
    conn.close()


def calculate_sl(df, hook):
    highs, lows = find_pivots(df)
    final_time = hook["final"]["time"]
    if hook["direction"] == "SHORT":
        candidates = [x for x in highs if x["time"] < final_time]
        candidates.sort(key=lambda x: x["time"], reverse=True)
    else:
        candidates = [x for x in lows if x["time"] < final_time]
        candidates.sort(key=lambda x: x["time"], reverse=True)
    return candidates[0]["price"] if candidates else None


def tp_already_touched(df, hook):
    future = df[df["time"] > hook["final"]["time"]]
    if future.empty:
        return False
    if hook["direction"] == "SHORT":
        return bool((future["low"] <= hook["tp"]).any())
    return bool((future["high"] >= hook["tp"]).any())


def create_hook_chart(symbol, df, hook, sl, path_prefix="hook", timeframe="M5"):
    try:
        chart_df = df.tail(CHART_CANDLES).copy()
        if chart_df.empty:
            return None
        fig, ax = plt.subplots(figsize=(15, 8))
        x = pd.to_datetime(chart_df["time"], unit="s", utc=True)
        ax.plot(x, chart_df["close"], linewidth=1.0, label=f"{timeframe} Close", color="dimgray")
        if hook["direction"] == "SHORT":
            points = [hook["start"], hook["h1"], hook["l1"], hook["h2"], hook["l2"], hook["h3"]]
            labels = ["START", "H1", "L1", "H2", "L2", "H3"]
        else:
            points = [hook["start"], hook["l1"], hook["h1"], hook["l2"], hook["h2"], hook["l3"]]
            labels = ["START", "L1", "H1", "L2", "H2", "L3"]
        px = [datetime.fromtimestamp(p["time"], tz=timezone.utc) for p in points]
        py = [p["price"] for p in points]
        ax.plot(px, py, marker="o", linewidth=2.0, label="Confirmed NDS Hook", color="royalblue")
        offsets = [(0, 12), (0, 18), (0, -25), (0, 18), (0, -25), (0, 18)]
        for p, label, offset in zip(points, labels, offsets):
            dt = datetime.fromtimestamp(p["time"], tz=timezone.utc)
            ax.annotate(f"{label}\n{fmt_price(p['price'])}", (dt, p["price"]),
                        xytext=offset, textcoords="offset points", ha="center", fontsize=8,
                        bbox=dict(boxstyle="round,pad=0.2", fc="white", alpha=0.75))
        entry, tp = float(hook["entry"]), float(hook["tp"])
        start_x = min(x.iloc[0], px[0])
        end_x = max(x.iloc[-1], datetime.fromtimestamp(hook["confirmation_time"], tz=timezone.utc))
        ax.hlines(entry, start_x, end_x, linestyles="--", linewidth=1.4, label=f"ENTRY {fmt_price(entry)}", color="darkorange")
        ax.hlines(tp, start_x, end_x, linestyles="--", linewidth=1.6, label=f"TP 86.4% {fmt_price(tp)}", color="seagreen")
        if sl is not None:
            ax.hlines(float(sl), start_x, end_x, linestyles="--", linewidth=1.4,
                      label=f"SL {fmt_price(sl)}", color="crimson")
        confirmation_dt = datetime.fromtimestamp(hook["confirmation_time"], tz=timezone.utc)
        ax.axvline(confirmation_dt, linestyle=":", linewidth=1.2, label="CONFIRMED", color="purple")
        ax.set_title(f"NDS {timeframe} | {symbol} | {hook['direction']} | CONFIRMED HOOK")
        ax.set_xlabel("Time UTC")
        ax.set_ylabel("Price")
        ax.grid(alpha=0.25)
        ax.legend(loc="best", fontsize=8)
        fig.autofmt_xdate()
        safe_symbol = symbol.replace("/", "_").replace(":", "_")
        path = os.path.join(CHART_DIR, f"{path_prefix}_{safe_symbol}_{hook['direction']}_{int(hook['final']['time'])}.png")
        plt.tight_layout()
        plt.savefig(path, dpi=140)
        plt.close(fig)
        return path
    except Exception as e:
        DIAG["api_errors"].append(f"Chart {symbol}: {str(e)[:180]}")
        try:
            plt.close("all")
        except Exception:
            pass
        return None


def send_detected_m5_hook_charts(symbol, df, direction=None):
    """Send charts for every newly detected, confirmed, recent M5 hook.
    If direction is None, chart both LONG and SHORT hooks regardless of H4.
    Chart reporting is independent of trade eligibility.
    """
    hooks = detect_hooks(df, M5_INTERVAL_MINUTES)
    now_ts = utc_now_ts()
    sent = 0

    for hook in hooks:
        if direction is not None and hook["direction"] != direction:
            continue
        if not hook_is_confirmed(hook, now_ts):
            continue
        if not hook_is_recent(hook, now_ts):
            continue

        key = hook_id(symbol, hook)
        if notification_exists(key):
            continue

        sl = calculate_sl(df, hook)
        chart_path = create_hook_chart(
            symbol, df, hook, sl, "detected_hook"
        )
        if not chart_path:
            DIAG["hook_chart_errors"] += 1
            continue

        caption = (
            f"🔎 <b>NDS M5 HOOK DETECTED</b>\\n"
            f"<b>{symbol}</b>\\n"
            f"Direction: <b>{hook['direction']}</b>\\n"
            f"START: {fmt_price(hook['start']['price'])}\\n"
            f"Final: {fmt_price(hook['final']['price'])}\\n"
            f"Entry reference: {fmt_price(hook['entry'])}\\n"
            f"TP 86.4%: <b>{fmt_price(hook['tp'])}</b>\\n"
            f"SL reference: {fmt_price(sl)}\\n"
            f"Hook range: {hook['range_pct']:.2f}%\\n"
            f"Confirmed: {fmt_ts(hook['confirmation_time'])}\\n"
            f"<b>Chart only; not necessarily a trade signal.</b>\\n"
            f"<b>PAPER TRADING ONLY</b>"
        )

        if telegram_send_photo(chart_path, caption):
            mark_notification_sent(key, chart_path)
            DIAG["hook_charts_sent"] += 1
            sent += 1
        else:
            DIAG["hook_chart_errors"] += 1
            DIAG["api_errors"].append(
                f"Hook chart not sent: {symbol} {hook['direction']} "
                f"(check Telegram token/chat ID, bot permissions, and photo API response)"
            )

    return sent


def get_h4_direction(symbol):
    DIAG["h4_requests"] += 1
    df = get_candles(symbol, H4_INTERVAL, H4_CANDLES)
    if df is None:
        DIAG["h4_data_error"] += 1
        return None
    if df.empty:
        DIAG["h4_empty"] += 1
        return None
    if len(df) < 50:
        DIAG["h4_short"] += 1
        return None
    DIAG["h4_data_ok"] += 1
    H4_DATA[symbol] = df.copy()
    highs, lows = find_pivots(df)
    DIAG["h4_pivots"] += len(highs) + len(lows)
    hooks = detect_hooks(df, H4_INTERVAL_MINUTES)
    DIAG["h4_hooks"] += len(hooks)
    now_ts = utc_now_ts()
    valid = [h for h in hooks if hook_is_confirmed(h, now_ts) and hook_is_recent(h, now_ts)]
    DIAG["h4_valid_hooks"] += len(valid)
    if not valid:
        return None
    valid.sort(key=lambda h: (h["confirmation_time"], h["final"]["time"]), reverse=True)
    latest = valid[0]
    H4_DIRECTION[symbol] = {"direction": latest["direction"], "hook": latest}
    if latest["direction"] == "SHORT":
        DIAG["h4_short_direction"] += 1
    else:
        DIAG["h4_long_direction"] += 1
    return latest["direction"]


def build_signal_message(symbol, hook, sl):
    direction = hook["direction"]
    emoji = "🔴" if direction == "SHORT" else "🟢"
    entry, tp, sl = float(hook["entry"]), float(hook["tp"]), float(sl)
    if direction == "SHORT":
        risk_pct = (sl-entry)/entry*100
        reward_pct = (entry-tp)/entry*100
    else:
        risk_pct = (entry-sl)/entry*100
        reward_pct = (tp-entry)/entry*100
    return (
        f"{emoji} <b>NDS {direction}</b>\n<b>{symbol}</b>\n\n"
        f"Entry: <b>{fmt_price(entry)}</b>\n"
        f"SL: <b>{fmt_price(sl)}</b> ({risk_pct:.2f}%)\n"
        f"TP 86.4%: <b>{fmt_price(tp)}</b> ({reward_pct:.2f}%)\n\n"
        f"Hook Range: {hook['range_pct']:.2f}%\n"
        f"Confirmed: {fmt_ts(hook['confirmation_time'])}\n"
        f"<b>H4 direction confirmed</b>\n<b>Paper Trading</b>"
    )


def process_symbol(symbol):
    # H4 direction is used ONLY to authorize paper-trade signals.
    # M5 hook discovery and chart reporting run independently.
    direction = get_h4_direction(symbol)

    DIAG["m5_requests"] += 1
    df = get_candles(symbol, M5_INTERVAL, M5_CANDLES)

    if df is None:
        DIAG["m5_data_error"] += 1
        if direction is None:
            DIAG["h4_filtered"] += 1
        return
    if df.empty:
        DIAG["m5_empty"] += 1
        if direction is None:
            DIAG["h4_filtered"] += 1
        return
    if len(df) < 100:
        DIAG["m5_short"] += 1
        if direction is None:
            DIAG["h4_filtered"] += 1
        return

    DIAG["m5_data_ok"] += 1

    hooks = detect_hooks(df, M5_INTERVAL_MINUTES)
    DIAG["m5_hooks"] += len(hooks)

    # Only signals that pass the H4 direction filter and M5 validation are charted.
    # Without a valid H4 direction, do not create a trade signal.
    if direction is None:
        DIAG["h4_filtered"] += 1
        return

    now_ts = utc_now_ts()
    candidates = []
    for hook in hooks:
        if hook["direction"] != direction:
            continue
        if not hook_is_confirmed(hook, now_ts) or not hook_is_recent(hook, now_ts):
            continue
        if hook["range_pct"] < M5_MIN_HOOK_RANGE_PCT:
            continue
        if tp_already_touched(df, hook):
            continue
        candidates.append(hook)

    DIAG["m5_valid_hooks"] += len(candidates)
    if not candidates:
        return

    candidates.sort(
        key=lambda h: (h["confirmation_time"], h["final"]["time"]),
        reverse=True
    )
    hook = candidates[0]
    sl = calculate_sl(df, hook)
    if sl is None:
        return

    entry, tp, sl = float(hook["entry"]), float(hook["tp"]), float(sl)
    if direction == "SHORT" and not (sl > entry > tp):
        return
    if direction == "LONG" and not (sl < entry < tp):
        return

    key = hook_id(symbol, hook)
    if hook_exists(key) or trade_exists(key):
        DIAG["duplicate_signals"] += 1
        return
    if open_trade_count() >= MAX_OPEN_TRADES:
        DIAG["max_open"] += 1
        return

    # M5 signal chart plus the corresponding H4 direction-hook chart.
    chart_path = create_hook_chart(symbol, df, hook, sl, "signal_m5", timeframe="M5")
    save_hook(symbol, hook, sl, chart_path)
    save_trade(key, symbol, hook, sl, chart_path)
    DIAG["signals"] += 1
    telegram_send(build_signal_message(symbol, hook, sl))
    if chart_path:
        telegram_send_photo(
            chart_path,
            f"📍 <b>NDS {direction} SIGNAL | M5</b>\n<b>{symbol}</b>\nEntry: {fmt_price(hook['entry'])}\nTP 86.4%: {fmt_price(hook['tp'])}\nSL: {fmt_price(sl)}\nPAPER TRADING ONLY"
        )

    h4_info = H4_DIRECTION.get(symbol)
    h4_df = H4_DATA.get(symbol)
    if h4_info and h4_df is not None:
        h4_hook = h4_info.get("hook")
        h4_sl = calculate_sl(h4_df, h4_hook) if h4_hook else None
        if h4_hook:
            h4_chart_path = create_hook_chart(
                symbol, h4_df, h4_hook, h4_sl, "signal_h4", timeframe="H4"
            )
            if h4_chart_path:
                telegram_send_photo(
                    h4_chart_path,
                    f"🧭 <b>H4 DIRECTION CONFIRMATION</b>\n<b>{symbol}</b>\nDirection: <b>{direction}</b>\nThis H4 hook authorized the M5 signal.\nPAPER TRADING ONLY"
                )


def get_open_trades():
    conn = db_connect()
    rows = conn.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY opened_at ASC").fetchall()
    conn.close()
    return rows


def close_trade(trade, close_price, pnl_pct, pnl_price, reason):
    conn = db_connect()
    conn.execute("""
        UPDATE trades SET closed_at=?,close_price=?,status=?,pnl_pct=?,pnl_price=?
        WHERE id=?
    """, (utc_now_ts(), close_price, reason, pnl_pct, pnl_price, trade["id"]))
    conn.commit()
    conn.close()


def monitor_open_trades():
    for trade in get_open_trades():
        symbol, direction = trade["symbol"], trade["direction"]
        entry, sl, tp = float(trade["entry"]), float(trade["sl"]), float(trade["tp"])
        try:
            df = get_candles(symbol, M5_INTERVAL, 30)
            if df is None or df.empty:
                continue
            # Ignore candles that opened before the paper trade was recorded.
            future = df[df["time"] >= trade["opened_at"]]
            if future.empty:
                continue
            close_price, reason = None, None
            for _, candle in future.iterrows():
                high, low = float(candle["high"]), float(candle["low"])
                if direction == "SHORT":
                    hit_sl, hit_tp = high >= sl, low <= tp
                else:
                    hit_sl, hit_tp = low <= sl, high >= tp
                # Conservative: assume SL first if both levels occur in one candle.
                if hit_sl:
                    close_price, reason = sl, "SL"
                    break
                if hit_tp:
                    close_price, reason = tp, "TP"
                    break
            if reason is None:
                continue
            pnl_price = entry-close_price if direction == "SHORT" else close_price-entry
            pnl_pct = pnl_price/entry*100 if entry else 0.0
            close_trade(trade, close_price, pnl_pct, pnl_price, reason)
            emoji = "✅" if pnl_pct >= 0 else "❌"
            telegram_send(
                f"{emoji} <b>NDS {direction} CLOSED</b>\n<b>{symbol}</b>\n\n"
                f"Entry: <b>{fmt_price(entry)}</b>\nClose: <b>{fmt_price(close_price)}</b>\n"
                f"Result: <b>{reason}</b>\nPnL: <b>{pnl_pct:+.2f}%</b>"
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
    return {"total": int(total or 0), "wins": int(wins or 0), "losses": int(losses or 0), "pnl": float(pnl or 0)}


def diagnostic_text():
    perf = performance_summary()
    lines = [
        "🔎 <b>NDS H4 → M5 DIAGNOSTIC</b>",
        f"Version: <b>{VERSION}</b>",
        f"Time: {utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Runtime: <b>{time.time()-START_TIME:.1f}s</b>",
        "",
        "━━━ <b>ASSETS</b> ━━━",
        f"Scanned: <b>{DIAG['assets_scanned']}</b>",
        "",
        "━━━ <b>H4</b> ━━━",
        f"Requests: {DIAG['h4_requests']}",
        f"Data OK: {DIAG['h4_data_ok']}",
        f"Data Error: {DIAG['h4_data_error']}",
        f"Empty: {DIAG['h4_empty']}",
        f"Short: {DIAG['h4_short']}",
        f"Pivot Points: {DIAG['h4_pivots']}",
        f"Hooks: {DIAG['h4_hooks']}",
        f"Valid Hooks: {DIAG['h4_valid_hooks']}",
        f"SHORT Direction: {DIAG['h4_short_direction']}",
        f"LONG Direction: {DIAG['h4_long_direction']}",
        f"Filtered: {DIAG['h4_filtered']}",
        "",
        "━━━ <b>M5</b> ━━━",
        f"Requests: {DIAG['m5_requests']}",
        f"Data OK: {DIAG['m5_data_ok']}",
        f"Data Error: {DIAG['m5_data_error']}",
        f"Empty: {DIAG['m5_empty']}",
        f"Short: {DIAG['m5_short']}",
        f"Pivot Points: {DIAG['m5_pivots']}",
        f"Hooks: {DIAG['m5_hooks']}",
        f"Valid Hooks: {DIAG['m5_valid_hooks']}",
        f"Hook Charts Sent: {DIAG['hook_charts_sent']}",
        f"Hook Chart Errors: {DIAG['hook_chart_errors']}",
        "",
        "━━━ <b>SIGNALS</b> ━━━",
        f"Signals: {DIAG['signals']}",
        f"Duplicates: {DIAG['duplicate_signals']}",
        f"Max Open Limit: {DIAG['max_open']}",
        f"Open Trades: <b>{open_trade_count()}</b>",
        "",
        "━━━ <b>PAPER PERFORMANCE</b> ━━━",
        f"Closed Trades: {perf['total']}",
        f"TP: {perf['wins']}",
        f"SL: {perf['losses']}",
        f"PnL: <b>{perf['pnl']:+.2f}%</b>",
    ]
    if DIAG["api_errors"]:
        lines += ["", "━━━ <b>ERRORS</b> ━━━"] + [f"• {e}" for e in DIAG["api_errors"][-5:]]
    lines += ["", "<b>PAPER TRADING ONLY</b>"]
    return "\n".join(lines)


def main():
    try:
        init_db()
        print(f"NDS H4 -> M5 Scanner {VERSION}")
        print("Charts are sent only for M5 signals approved by H4; both H4 and M5 charts are sent.")
        print("PAPER TRADING ONLY - NO REAL ORDERS")
        print(f"H4 pivot confirmation: {H4_INTERVAL_MINUTES}m x {PIVOT_RIGHT}")
        print(f"M5 pivot confirmation: {M5_INTERVAL_MINUTES}m x {PIVOT_RIGHT}")
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
        telegram_send(f"❌ <b>NDS Scanner Error</b>\n\n{type(e).__name__}: {str(e)[:500]}")


if __name__ == "__main__":
    main()
