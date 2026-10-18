
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NDS M5 TWO-STRUCTURE LONG/SHORT SCANNER
Version: 1.1.0
Signal detection only. No exchange orders.

LONG:
Start(L, 5 left/right) -> H1 -> L1 -> H2 -> Entry(L2, 2 left/right)
H2 > H1
TP = H2
SL = H2 - 0.864 * (H2 - Start)

SHORT (mirror):
Start(H, 5 left/right) -> L1 -> H1 -> L2 -> Entry(H2, 2 left/right)
L2 < L1
TP = L2
SL = L2 + 0.864 * (Start - L2)

Only closed M5 candles are used.
"""

import os
import time
import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
from matplotlib.patches import Rectangle

VERSION = "1.1.0"

BASE_URL = "https://futures.kraken.com"
TICKERS_URL = f"{BASE_URL}/derivatives/api/v3/tickers"
CHART_URL = f"{BASE_URL}/api/charts/v1/trade"

TIMEFRAME = "5m"
START_LEFT = 5
START_RIGHT = 5
PIVOT_LEFT = 2
PIVOT_RIGHT = 2
CANDLE_LIMIT = 600
TARGET_SYMBOLS = 100
MIN_LEG_PCT = 0.0
RETRACE = 0.864
PAPER_ONLY = True

DB_PATH = Path("nds_m5_two_structure_ls.db")
CHART_DIR = Path("nds_m5_two_structure_ls_charts")
CHART_DIR.mkdir(parents=True, exist_ok=True)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT", "")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": f"NDS-M5-Scanner/{VERSION}"})
TIMEOUT = 20

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("nds_m5_scanner")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_json(url, params=None):
    response = SESSION.get(url, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict) and data.get("result") == "error":
        raise RuntimeError(str(data.get("errors", data)))
    return data


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_time INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                start_price REAL NOT NULL,
                p1_price REAL NOT NULL,
                p2_price REAL NOT NULL,
                p3_price REAL NOT NULL,
                entry_price REAL NOT NULL,
                sl_price REAL NOT NULL,
                tp_price REAL NOT NULL,
                d_value REAL NOT NULL,
                chart_path TEXT,
                UNIQUE(symbol, side, entry_time)
            )
        """)
        conn.commit()


def fetch_tickers():
    data = get_json(TICKERS_URL)
    tickers = data.get("tickers", []) if isinstance(data, dict) else data
    if not isinstance(tickers, list):
        return []

    parsed = []
    for item in tickers:
        symbol = str(item.get("symbol", "")).upper()
        if not symbol.startswith("PF_") or not symbol.endswith("USD"):
            continue
        if any(x in symbol for x in ("PI_", "FI_", "KF_")):
            continue

        volume = 0.0
        for key in ("vol24h", "volume24h", "volume", "vol24hQuote"):
            try:
                volume = float(item.get(key) or 0)
                if volume:
                    break
            except (TypeError, ValueError):
                pass
        parsed.append((symbol, volume))

    parsed.sort(key=lambda x: x[1], reverse=True)
    ranked = [s for s, _ in parsed]
    chosen = []

    for required in ("PF_XBTUSD", "PF_XAUTUSD"):
        if required in ranked:
            chosen.append(required)

    for symbol in ranked:
        if symbol not in chosen:
            chosen.append(symbol)
        if len(chosen) >= TARGET_SYMBOLS:
            break

    return chosen[:TARGET_SYMBOLS]


def normalize_candles(payload):
    raw = payload
    if isinstance(payload, dict):
        raw = payload.get("candles", payload.get("data", payload.get("result", [])))
    if not isinstance(raw, list):
        return []

    candles = []
    for row in raw:
        try:
            if isinstance(row, dict):
                ts = row.get("time", row.get("timestamp", row.get("t")))
                o = row.get("open", row.get("o"))
                h = row.get("high", row.get("h"))
                l = row.get("low", row.get("l"))
                c = row.get("close", row.get("c"))
                v = row.get("volume", row.get("v", 0))
            elif isinstance(row, (list, tuple)) and len(row) >= 5:
                ts, o, h, l, c = row[:5]
                v = row[5] if len(row) > 5 else 0
            else:
                continue

            ts = int(float(ts))
            if ts > 10_000_000_000:
                ts //= 1000

            candle = {
                "time": ts,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v or 0),
            }
            if candle["high"] >= candle["low"] > 0 and candle["close"] > 0:
                candles.append(candle)
        except (TypeError, ValueError):
            continue

    candles.sort(key=lambda x: x["time"])
    dedup = {c["time"]: c for c in candles}
    return list(dedup.values())


def fetch_candles(symbol):
    url = f"{CHART_URL}/{symbol}/{TIMEFRAME}"
    candles = normalize_candles(get_json(url))
    candles = candles[-CANDLE_LIMIT:]
    now = int(time.time())
    return [c for c in candles if c["time"] + 300 <= now]


def find_pivots(candles):
    """
    A pivot is detected with 2 candles on each side.
    A Start pivot must additionally be an extreme over 5 candles
    on each side. Only closed candles are passed to this function.
    """
    pivots = []
    n = len(candles)

    for i in range(START_LEFT, n - START_RIGHT):
        candle = candles[i]
        wide = candles[i-START_LEFT:i+START_RIGHT+1]
        lows5 = [x["low"] for x in wide]
        highs5 = [x["high"] for x in wide]

        is_start_low = (
            candle["low"] == min(lows5)
            and lows5.count(candle["low"]) == 1
        )
        is_start_high = (
            candle["high"] == max(highs5)
            and highs5.count(candle["high"]) == 1
        )

        # If the same candle is both high and low pivot, ignore it.
        if is_start_low and is_start_high:
            continue

        if is_start_low:
            pivots.append({
                "type": "L", "index": i, "time": candle["time"],
                "price": candle["low"], "strength": 5
            })
        elif is_start_high:
            pivots.append({
                "type": "H", "index": i, "time": candle["time"],
                "price": candle["high"], "strength": 5
            })

    # Add 2-left/2-right pivots not already identified as 5-bar pivots.
    existing = {p["index"] for p in pivots}

    for i in range(PIVOT_LEFT, n - PIVOT_RIGHT):
        if i in existing:
            continue

        candle = candles[i]
        window = candles[i-PIVOT_LEFT:i+PIVOT_RIGHT+1]
        lows = [x["low"] for x in window]
        highs = [x["high"] for x in window]

        is_low = candle["low"] == min(lows) and lows.count(candle["low"]) == 1
        is_high = candle["high"] == max(highs) and highs.count(candle["high"]) == 1

        if is_low and is_high:
            continue
        if is_low:
            pivots.append({
                "type": "L", "index": i, "time": candle["time"],
                "price": candle["low"], "strength": 2
            })
        elif is_high:
            pivots.append({
                "type": "H", "index": i, "time": candle["time"],
                "price": candle["high"], "strength": 2
            })

    pivots.sort(key=lambda p: p["index"])
    return pivots


def detect_latest(candles, side):
    pivots = find_pivots(candles)
    if len(pivots) < 5:
        return None, {"pivots": len(pivots), "reason": "not enough pivots"}

    matches = []

    for i in range(len(pivots) - 4):
        a, b, c, d, e = pivots[i:i+5]
        sequence = [a["type"], b["type"], c["type"], d["type"], e["type"]]

        if side == "LONG":
            if sequence != ["L", "H", "L", "H", "L"]:
                continue

            start, h1, l1, h2, entry_pivot = a, b, c, d, e

            if start["strength"] != 5:
                continue
            if entry_pivot["strength"] < 2:
                continue
            if not (h2["price"] > h1["price"]):
                continue
            if not all(
                start["price"] < p["price"]
                for p in (h1, l1, h2, entry_pivot)
            ):
                continue

            d_value = h2["price"] - start["price"]
            if d_value <= 0:
                continue

            d_pct = 100 * d_value / start["price"]
            if d_pct < MIN_LEG_PCT:
                continue

            entry = entry_pivot["price"]
            tp = h2["price"]
            sl = h2["price"] - RETRACE * d_value

            if not sl < entry < tp:
                continue

            points = {
                "start": start, "p1": h1, "p2": l1,
                "p3": h2, "entry_pivot": entry_pivot
            }

        else:
            if sequence != ["H", "L", "H", "L", "H"]:
                continue

            start, l1, h1, l2, entry_pivot = a, b, c, d, e

            if start["strength"] != 5:
                continue
            if entry_pivot["strength"] < 2:
                continue
            if not (l2["price"] < l1["price"]):
                continue
            if not all(
                start["price"] > p["price"]
                for p in (l1, h1, l2, entry_pivot)
            ):
                continue

            d_value = start["price"] - l2["price"]
            if d_value <= 0:
                continue

            d_pct = 100 * d_value / start["price"]
            if d_pct < MIN_LEG_PCT:
                continue

            entry = entry_pivot["price"]
            tp = l2["price"]
            sl = l2["price"] + RETRACE * d_value

            if not tp < entry < sl:
                continue

            points = {
                "start": start, "p1": l1, "p2": h1,
                "p3": l2, "entry_pivot": entry_pivot
            }

        matches.append({
            "side": side,
            **points,
            "entry": entry,
            "tp": tp,
            "sl": sl,
            "d": d_value,
            "d_pct": d_pct,
        })

    if not matches:
        return None, {"pivots": len(pivots), "reason": "no valid structure"}

    return matches[-1], {"pivots": len(pivots), "reason": "valid structure"}


def draw_chart(symbol, candles, signal=None):
    if not candles:
        return None

    path = CHART_DIR / f"{symbol.lower()}_m5.png"
    dates = [datetime.fromtimestamp(c["time"], timezone.utc) for c in candles]
    fig, ax = plt.subplots(figsize=(14, 7))
    width_days = (5 / (24 * 60)) * 0.68

    for x, candle in zip(dates, candles):
        color = "#16a34a" if candle["close"] >= candle["open"] else "#dc2626"
        ax.vlines(x, candle["low"], candle["high"], color=color, linewidth=0.8)
        body_low = min(candle["open"], candle["close"])
        body_height = max(
            abs(candle["close"] - candle["open"]),
            candle["close"] * 0.00002
        )
        ax.add_patch(Rectangle(
            (mdates.date2num(x) - width_days / 2, body_low),
            width_days, body_height,
            facecolor=color, edgecolor=color, linewidth=0.5
        ))

    pivots = find_pivots(candles)
    high_n = low_n = 0

    for p in pivots:
        x = dates[p["index"]]
        if p["type"] == "H":
            high_n += 1
            ax.annotate(
                str(high_n), (x, p["price"]), xytext=(0, 8),
                textcoords="offset points", ha="center",
                color="#15803d", fontsize=9, fontweight="bold"
            )
        else:
            label = "0" if low_n == 0 else str(low_n)
            low_n += 1
            ax.annotate(
                label, (x, p["price"]), xytext=(0, -10),
                textcoords="offset points", ha="center", va="top",
                color="#dc2626", fontsize=9, fontweight="bold"
            )

    if signal:
        for key, color in (
            ("start", "#7c3aed"),
            ("p1", "#15803d" if signal["side"] == "LONG" else "#dc2626"),
            ("p2", "#dc2626" if signal["side"] == "LONG" else "#15803d"),
            ("p3", "#15803d" if signal["side"] == "LONG" else "#dc2626"),
            ("entry_pivot", "#dc2626"),
        ):
            p = signal[key]
            x = datetime.fromtimestamp(p["time"], timezone.utc)
            ax.scatter(
                x, p["price"], color=color, s=55,
                zorder=6, edgecolors="white", linewidths=0.6
            )

        ax.axhline(signal["tp"], color="#15803d", linestyle="--", label="TP")
        ax.axhline(signal["sl"], color="#dc2626", linestyle="--", label="SL")
        ax.axhline(signal["entry"], color="#2563eb", linestyle=":", label="Entry")

    side_title = signal["side"] if signal else "LONG/SHORT"
    ax.set_title(f"{symbol} | NDS M5 {side_title} | v{VERSION}")
    ax.set_ylabel("Price")
    ax.grid(True, alpha=0.2)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M", tz=timezone.utc))
    ax.yaxis.set_major_formatter(mticker.ScalarFormatter(useOffset=False))
    ax.ticklabel_format(axis="y", style="plain", useOffset=False)
    ax.legend(loc="best", fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return str(path)


def telegram_send(message, photo_path=None):
    if not BOT_TOKEN or not CHAT_ID:
        log.info("Telegram not configured; message follows:\n%s", message)
        return False

    try:
        if photo_path and Path(photo_path).exists():
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
            with open(photo_path, "rb") as photo:
                response = requests.post(
                    url,
                    data={"chat_id": CHAT_ID, "caption": message[:1024]},
                    files={"photo": photo},
                    timeout=30,
                )
        else:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            response = requests.post(
                url,
                json={
                    "chat_id": CHAT_ID,
                    "text": message,
                    "disable_web_page_preview": True
                },
                timeout=30,
            )
        response.raise_for_status()
        return True
    except Exception as exc:
        log.error("Telegram send failed: %s", exc)
        return False


def save_signal(symbol, signal, chart_path):
    entry_time = signal["entry_pivot"]["time"]

    with sqlite3.connect(DB_PATH) as conn:
        try:
            conn.execute("""
                INSERT INTO signals (
                    symbol, side, entry_time, created_at,
                    start_price, p1_price, p2_price, p3_price,
                    entry_price, sl_price, tp_price, d_value, chart_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, signal["side"], entry_time, utc_now(),
                signal["start"]["price"], signal["p1"]["price"],
                signal["p2"]["price"], signal["p3"]["price"],
                signal["entry"], signal["sl"], signal["tp"],
                signal["d"], chart_path
            ))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def fmt_price(value):
    return f"{value:.10g}"


def make_message(symbol, signal):
    if signal["side"] == "LONG":
        reward = signal["tp"] - signal["entry"]
        risk = signal["entry"] - signal["sl"]
        icon = "🟢"
        pattern = "Start(L) → H1 → L1 → H2 → Entry(L2)"
    else:
        reward = signal["entry"] - signal["tp"]
        risk = signal["sl"] - signal["entry"]
        icon = "🔴"
        pattern = "Start(H) → L1 → H1 → L2 → Entry(H2)"

    rr = reward / risk if risk > 0 else 0
    stamp = datetime.fromtimestamp(
        signal["entry_pivot"]["time"], timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")

    return (
        f"{icon} NDS {signal['side']} | M5 | {symbol}\n"
        f"Confirmed entry pivot: {stamp}\n"
        f"Entry: {fmt_price(signal['entry'])}\n"
        f"TP: {fmt_price(signal['tp'])}\n"
        f"SL: {fmt_price(signal['sl'])}\n"
        f"d: {fmt_price(signal['d'])} ({signal['d_pct']:.2f}%)\n"
        f"Reward/Risk: {rr:.2f}\n"
        f"Pattern: {pattern}\n"
        f"Start pivot: 5 candles left/right\n"
        f"Entry pivot: 2 candles left/right\n"
        f"Paper only: no exchange orders"
    )


def main():
    init_db()
    log.info("NDS M5 LONG/SHORT Scanner v%s | PAPER_ONLY=%s", VERSION, PAPER_ONLY)

    try:
        symbols = fetch_tickers()
    except Exception as exc:
        log.exception("Could not fetch Kraken Futures tickers: %s", exc)
        raise SystemExit(1)

    if not symbols:
        log.error("No eligible PF_*USD contracts returned by Kraken.")
        raise SystemExit(1)

    log.info(
        "Universe: %s | XBT=%s | XAUT=%s",
        len(symbols), "PF_XBTUSD" in symbols, "PF_XAUTUSD" in symbols
    )

    stats = {
        "scanned": 0, "candle_ok": 0, "errors": 0,
        "long_structures": 0, "short_structures": 0,
        "new": 0, "duplicates": 0
    }
    notifications = []

    for index, symbol in enumerate(symbols, 1):
        stats["scanned"] += 1

        try:
            candles = fetch_candles(symbol)
            if len(candles) < 20:
                log.info("%s: not enough closed candles (%s)", symbol, len(candles))
                continue

            stats["candle_ok"] += 1
            found = []

            for side in ("LONG", "SHORT"):
                signal, _ = detect_latest(candles, side)
                if signal:
                    found.append(signal)
                    if side == "LONG":
                        stats["long_structures"] += 1
                    else:
                        stats["short_structures"] += 1

            found.sort(key=lambda s: s["entry_pivot"]["time"])
            chart_signal = found[-1] if found else None
            chart_path = draw_chart(symbol, candles, chart_signal)

            for signal in found:
                if save_signal(symbol, signal, chart_path):
                    stats["new"] += 1
                    notifications.append((symbol, signal, chart_path))
                    log.info(
                        "NEW %s %s | Entry=%s TP=%s SL=%s",
                        signal["side"], symbol,
                        signal["entry"], signal["tp"], signal["sl"]
                    )
                else:
                    stats["duplicates"] += 1

        except Exception as exc:
            stats["errors"] += 1
            log.warning("%s scan error: %s", symbol, exc)

        time.sleep(0.08)

    for symbol, signal, chart_path in notifications:
        telegram_send(make_message(symbol, signal), chart_path)

    summary = (
        f"🔎 NDS M5 LONG/SHORT Scanner v{VERSION} | {utc_now()}\n"
        f"Universe: {len(symbols)} | Scanned: {stats['scanned']}\n"
        f"M5 OK: {stats['candle_ok']} | Errors: {stats['errors']}\n"
        f"Valid LONG structures: {stats['long_structures']}\n"
        f"Valid SHORT structures: {stats['short_structures']}\n"
        f"New signals: {stats['new']} | Duplicates: {stats['duplicates']}\n"
        f"Start: 5 candles left/right\n"
        f"Entry: 2 candles left/right\n"
        f"LONG TP=H2; SL=H2-86.4%*(H2-Start)\n"
        f"SHORT TP=L2; SL=L2+86.4%*(Start-L2)\n"
        f"Paper only. No exchange orders placed."
    )

    log.info("\n%s", summary)
    telegram_send(summary)


if __name__ == "__main__":
    main()
