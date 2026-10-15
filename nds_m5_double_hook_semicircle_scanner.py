
#!/usr/bin/env python3
# NDS M5 Double Hook Semicircle Scanner
# Scanner only. No exchange orders are placed.

import os
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle, Arc

VERSION = "1.0.0"
BASE = "https://futures.kraken.com"
MAX_ASSETS = 100
CANDLE_COUNT = 500
RETRACE = 0.864
MIN_BARS = 3
MAX_BARS = 180
LEFT = 2
RIGHT = 2

OUT = Path("nds_m5_double_hook_charts")
OUT.mkdir(exist_ok=True)

S = requests.Session()
S.headers.update({"User-Agent": "NDS-M5-Double-Hook/1.0"})

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)


def api(url, params=None):
    r = S.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise ValueError("Unexpected API response")
    return data


def get_volume(t):
    for key in ("vol24h", "volume24h", "volume"):
        try:
            return float(t.get(key) or 0)
        except (TypeError, ValueError):
            pass
    return 0.0


def get_symbols():
    instruments = api(
        f"{BASE}/derivatives/api/v3/instruments"
    ).get("instruments", [])

    tickers = api(
        f"{BASE}/derivatives/api/v3/tickers"
    ).get("tickers", [])

    ticker_map = {
        t.get("symbol"): t
        for t in tickers
        if isinstance(t, dict)
    }

    markets = []

    for item in instruments:
        if not isinstance(item, dict):
            continue

        symbol = str(item.get("symbol", ""))
        tradable = item.get("tradeable", True)

        if isinstance(tradable, str):
            tradable = tradable.lower() == "true"

        if (
            symbol.startswith("PF_")
            and symbol.endswith("USD")
            and tradable
            and symbol in ticker_map
        ):
            markets.append(
                (symbol, get_volume(ticker_map[symbol]))
            )

    available = {s for s, _ in markets}

    # Required markets first, if listed by Kraken.
    selected = [
        s for s in ("PF_XBTUSD", "PF_XAUTUSD")
        if s in available
    ]

    for symbol, volume in sorted(
        markets, key=lambda x: x[1], reverse=True
    ):
        if symbol not in selected and len(selected) < MAX_ASSETS:
            selected.append(symbol)

    logging.info(
        "Markets selected=%d, BTC=%s, XAUT=%s",
        len(selected),
        "PF_XBTUSD" in selected,
        "PF_XAUTUSD" in selected
    )

    return selected[:MAX_ASSETS]


def parse_candles(payload):
    rows = payload

    if isinstance(payload, dict):
        rows = payload.get(
            "candles",
            payload.get("data", payload.get("results", []))
        )

    if not isinstance(rows, list):
        return []

    result = []

    for row in rows:
        try:
            if isinstance(row, dict):
                ts = row.get(
                    "time", row.get("timestamp", row.get("t"))
                )
                op = row.get("open", row.get("o"))
                hi = row.get("high", row.get("h"))
                lo = row.get("low", row.get("l"))
                cl = row.get("close", row.get("c"))

            elif isinstance(row, (list, tuple)) and len(row) >= 5:
                ts, op, hi, lo, cl = row[:5]

            else:
                continue

            ts = float(ts)

            if ts > 10_000_000_000:
                ts /= 1000

            result.append({
                "time": datetime.fromtimestamp(
                    ts, tz=timezone.utc
                ),
                "open": float(op),
                "high": float(hi),
                "low": float(lo),
                "close": float(cl)
            })

        except (TypeError, ValueError, OverflowError, OSError):
            continue

    result.sort(key=lambda c: c["time"])

    # Remove duplicate timestamps.
    unique = {c["time"]: c for c in result}
    return list(unique.values())


def get_candles(symbol):
    end = int(time.time())
    start = end - CANDLE_COUNT * 300

    url = f"{BASE}/api/charts/v1/trade/{symbol}/5m"

    payload = api(
        url,
        {"from": start, "to": end}
    )

    candles = parse_candles(payload)

    now = datetime.now(timezone.utc)

    # Exclude unfinished M5 candle.
    candles = [
        c for c in candles
        if (now - c["time"]).total_seconds() >= 300
    ]

    if len(candles) < 80:
        raise ValueError(
            f"Insufficient candles: {len(candles)}"
        )

    return candles


def find_pivots(candles):
    pivots = []

    for i in range(LEFT, len(candles) - RIGHT):
        window = candles[i-LEFT:i+RIGHT+1]

        high = candles[i]["high"]
        low = candles[i]["low"]

        if (
            high == max(c["high"] for c in window)
            and sum(c["high"] == high for c in window) == 1
        ):
            pivots.append((i, "H", high))

        if (
            low == min(c["low"] for c in window)
            and sum(c["low"] == low for c in window) == 1
        ):
            pivots.append((i, "L", low))

    pivots.sort(key=lambda p: (p[0], p[1]))

    # One pivot per candle if both high and low qualify.
    cleaned = []

    for p in pivots:
        if cleaned and cleaned[-1][0] == p[0]:
            candle = candles[p[0]]
            preferred = (
                "H" if candle["close"] >= candle["open"]
                else "L"
            )

            if p[1] == preferred:
                cleaned[-1] = p
        else:
            cleaned.append(p)

    return cleaned


def find_hooks(candles):
    pivots = find_pivots(candles)
    hooks = []

    for a, (start_i, start_type, start_price) in enumerate(pivots):

        opposite = "H" if start_type == "L" else "L"
        extreme = None

        for idx, kind, price in pivots[a+1:]:
            if idx - start_i > MAX_BARS:
                break

            if kind == opposite:
                extreme = (idx, price)
                break

        if extreme is None:
            continue

        extreme_i, extreme_price = extreme

        if extreme_i - start_i < MIN_BARS:
            continue

        if start_type == "L":
            # Positive hook: low -> high.
            span = extreme_price - start_price
            direction = "POSITIVE"

            if span <= 0:
                continue

            # 86.4% retracement from the high toward the start.
            target = extreme_price - RETRACE * span

            confirmation = next(
                (
                    i for i in range(
                        extreme_i + 1,
                        min(len(candles), start_i + MAX_BARS + 1)
                    )
                    if candles[i]["low"] <= target
                ),
                None
            )

        else:
            # Negative hook: high -> low.
            span = start_price - extreme_price
            direction = "NEGATIVE"

            if span <= 0:
                continue

            target = extreme_price + RETRACE * span

            confirmation = next(
                (
                    i for i in range(
                        extreme_i + 1,
                        min(len(candles), start_i + MAX_BARS + 1)
                    )
                    if candles[i]["high"] >= target
                ),
                None
            )

        if confirmation is None:
            continue

        hooks.append({
            "direction": direction,
            "start_i": start_i,
            "extreme_i": extreme_i,
            "confirm_i": confirmation,
            "start_price": start_price,
            "extreme_price": extreme_price,
            "target": target
        })

    hooks.sort(
        key=lambda h: (h["confirm_i"], h["start_i"])
    )

    # Avoid repeated detections around the same confirmation.
    filtered = []

    for hook in hooks:
        if (
            filtered
            and hook["direction"] == filtered[-1]["direction"]
            and abs(
                hook["confirm_i"] - filtered[-1]["confirm_i"]
            ) <= 2
        ):
            if hook["start_i"] < filtered[-1]["start_i"]:
                filtered[-1] = hook
        else:
            filtered.append(hook)

    return filtered


def find_double_hook(hooks):
    for first, second in zip(hooks, hooks[1:]):
        if (
            first["direction"] == second["direction"]
            and first["confirm_i"] < second["confirm_i"]
            and second["start_i"] >= first["confirm_i"]
        ):
            return first, second

    return None


def draw_candles(ax, candles):
    width = 5 / (24 * 60) * 0.68

    for c in candles:
        x = mdates.date2num(c["time"])

        color = (
            "#15945b" if c["close"] >= c["open"]
            else "#d34b4b"
        )

        ax.vlines(
            x, c["low"], c["high"],
            color=color, linewidth=0.8, zorder=2
        )

        bottom = min(c["open"], c["close"])
        height = abs(c["close"] - c["open"])

        if height == 0:
            height = max(abs(c["close"]) * 0.00002, 1e-10)

        ax.add_patch(
            Rectangle(
                (x - width / 2, bottom),
                width,
                height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.4,
                zorder=3
            )
        )


def draw_semicircle(ax, candles, hook):
    start = hook["start_i"]
    end = hook["confirm_i"]

    segment = candles[start:end+1]

    x0 = mdates.date2num(candles[start]["time"])
    x1 = mdates.date2num(candles[end]["time"])

    if x1 <= x0:
        return

    highest = max(c["high"] for c in segment)
    lowest = min(c["low"] for c in segment)

    span = max(highest - lowest, abs(highest) * 0.002, 1e-9)

    if hook["direction"] == "POSITIVE":
        center_y = highest + span * 0.35
        height = span * 0.8
        theta1, theta2 = 0, 180
        color = "#16834a"
    else:
        center_y = lowest - span * 0.35
        height = span * 0.8
        theta1, theta2 = 180, 360
        color = "#b82e2e"

    ax.add_patch(
        Arc(
            ((x0 + x1) / 2, center_y),
            max(x1 - x0, 0.0005),
            height,
            theta1=theta1,
            theta2=theta2,
            color=color,
            linewidth=2.2,
            zorder=5
        )
    )


def make_chart(symbol, candles, pair):
    first, second = pair

    left = max(0, first["start_i"] - 20)
    right = min(len(candles), second["confirm_i"] + 20)

    view = candles[left:right]
    adjusted = []

    for hook in pair:
        adjusted.append({
            **hook,
            "start_i": hook["start_i"] - left,
            "extreme_i": hook["extreme_i"] - left,
            "confirm_i": hook["confirm_i"] - left
        })

    fig, ax = plt.subplots(figsize=(13, 7), dpi=150)

    draw_candles(ax, view)

    for hook in adjusted:
        draw_semicircle(ax, view, hook)

    ax.set_title(
        f"{symbol} | M5 | Two consecutive "
        f"{first['direction'].lower()} hooks"
    )
    ax.set_ylabel("Price")
    ax.grid(True, alpha=0.12)

    ax.xaxis.set_major_formatter(
        mdates.DateFormatter("%m-%d %H:%M", tz=timezone.utc)
    )

    fig.autofmt_xdate()
    fig.tight_layout()

    path = OUT / (
        f"{symbol}_{first['direction'].lower()}_double_hook.png"
    )

    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)

    return path


def send_telegram(path, caption):
    token = (
        os.getenv("TELEGRAM_BOT_TOKEN")
        or os.getenv("TELEGRAM_TOKEN")
    )
    chat_id = (
        os.getenv("TELEGRAM_CHAT_ID")
        or os.getenv("TELEGRAM_CHAT")
    )

    if not token or not chat_id:
        return False

    with open(path, "rb") as photo:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={
                "chat_id": chat_id,
                "caption": caption[:900]
            },
            files={"photo": photo},
            timeout=45
        )

    response.raise_for_status()

    if not response.json().get("ok"):
        raise RuntimeError("Telegram rejected the chart")

    return True


def main():
    logging.info("NDS M5 Double Hook Scanner v%s", VERSION)

    try:
        symbols = get_symbols()
    except Exception:
        logging.exception("Could not load Kraken Futures markets")
        return 1

    matches = 0
    errors = 0

    for symbol in symbols:
        try:
            candles = get_candles(symbol)
            hooks = find_hooks(candles)
            pair = find_double_hook(hooks)

            if pair is None:
                logging.info("NO_MATCH %s", symbol)
            else:
                path = make_chart(symbol, candles, pair)
                direction = pair[0]["direction"].lower()

                sent = send_telegram(
                    path,
                    f"{symbol} | M5 | Two completed "
                    f"{direction} hooks | 86.4% retracement"
                )

                matches += 1

                logging.info(
                    "MATCH %s direction=%s chart=%s telegram=%s",
                    symbol, direction, path, sent
                )

        except Exception as exc:
            errors += 1
            logging.warning("ASSET_ERROR %s: %s", symbol, exc)

        time.sleep(0.2)

    logging.info(
        "SCAN_COMPLETE scanned=%d matches=%d errors=%d",
        len(symbols), matches, errors
    )

    return 0 if symbols else 1


if __name__ == "__main__":
    raise SystemExit(main())
