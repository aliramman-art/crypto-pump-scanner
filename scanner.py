# ============================================================
# CRYPTO PUMP / DUMP SCANNER v5.5
# ============================================================
# Kraken Futures
# Closed 5m Trade OHLCV
# Real Volume
# RSI / ATR / Ichimoku
# Trendline Breakout / Breakdown
# 2-Candle Confirmation
# Entry / SL / TP1 / TP2 / TP3
# Risk / Reward
# Telegram
#
# ADDED:
# UT BOT SETUP ENGINE
# TOP 5 ONLY
# 5m / Key Value 3 / ATR 10 / Heikin Ashi OFF
# Live Setup Tracking
# Win Rate Tracking
# ============================================================

import os
import json
import time
import requests
from statistics import mean


VERSION = "v5.5"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BASE_URL = "https://futures.kraken.com/api/charts/v1"


COINS = {
    "BTC": "pf_xbtusd",
    "ETH": "pf_ethusd",
    "BNB": "pf_bnbusd",
    "SOL": "pf_solusd",
    "XRP": "pf_xrpusd",
    "DOGE": "pf_dogeusd",
    "ADA": "pf_adausd",
    "AVAX": "pf_avaxusd",
    "LINK": "pf_linkusd",
    "DOT": "pf_dotusd",
    "TRX": "pf_trxusd",
    "LTC": "pf_ltcusd",
    "BCH": "pf_bchusd",
    "ATOM": "pf_atomusd",
    "UNI": "pf_uniusd",
    "ETC": "pf_etcusd",
    "XLM": "pf_xlmusd",
    "NEAR": "pf_nearusd",
    "APT": "pf_aptusd",
    "FIL": "pf_filusd",
    "ARB": "pf_arbusd",
    "OP": "pf_opusd",
    "SUI": "pf_suiusd",
    "INJ": "pf_injusd",
    "AAVE": "pf_aaveusd",
    "MKR": "pf_mkrusd",
    "ALGO": "pf_algousd",
    "VET": "pf_vetusd",
    "SEI": "pf_seiusd",
    "TIA": "pf_tiausd",
}


STATE_FILE = "signal_state.json"


# ============================================================
# UT BOT SETTINGS
# ============================================================

UT_KEY_VALUE = 3
UT_ATR_PERIOD = 10


# ============================================================
# JSON
# ============================================================

def load_json(path, default):
    try:
        if not os.path.exists(path):
            return default

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return data

    except Exception:
        return default


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                indent=2,
                ensure_ascii=False
            )

    except Exception as e:
        print("SAVE ERROR:", e)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(text)
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:

        r = requests.post(
            url,
            json=payload,
            timeout=15
        )

        if r.status_code != 200:

            print(
                "Telegram error:",
                r.status_code,
                r.text[:500]
            )

            return False

        print("Telegram: MESSAGE SENT")

        return True

    except Exception as e:

        print(
            "Telegram exception:",
            e
        )

        return False


# ============================================================
# KRAKEN CANDLE PARSER
# ============================================================

def parse_candle(item):

    try:

        if isinstance(item, dict):

            t = (
                item.get("time")
                or item.get("timestamp")
                or item.get("ts")
            )

            o = item.get("open")
            h = item.get("high")
            l = item.get("low")
            c = item.get("close")

            v = (
                item.get("volume")
                or item.get("vol")
                or 0
            )

            if None in (t, o, h, l, c):
                return None

            t = float(t)

            if t > 10_000_000_000:
                t /= 1000

            return {
                "time": int(t),
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v),
            }

        if isinstance(item, (list, tuple)):

            if len(item) < 5:
                return None

            t = float(item[0])

            if t > 10_000_000_000:
                t /= 1000

            return {
                "time": int(t),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": (
                    float(item[5])
                    if len(item) > 5
                    else 0.0
                ),
            }

    except Exception:
        return None

    return None


def extract_candles(data):

    if isinstance(data, dict):

        candidates = []

        for key in (
            "candles",
            "data",
            "result",
            "results",
            "ohlcv",
        ):

            value = data.get(key)

            if isinstance(value, list):

                candidates.extend(value)

            elif isinstance(value, dict):

                for nested_key in (
                    "candles",
                    "data",
                    "result",
                    "results",
                    "ohlcv",
                ):

                    nested = value.get(
                        nested_key
                    )

                    if isinstance(nested, list):
                        candidates.extend(nested)

        if not candidates:

            for value in data.values():

                if (
                    isinstance(value, list)
                    and value
                ):

                    if isinstance(
                        value[0],
                        (dict, list, tuple)
                    ):
                        candidates.extend(value)

        parsed = []

        for item in candidates:

            candle = parse_candle(item)

            if candle:
                parsed.append(candle)

        return parsed

    if isinstance(data, list):

        parsed = []

        for item in data:

            candle = parse_candle(item)

            if candle:
                parsed.append(candle)

        return parsed

    return []


# ============================================================
# KRAKEN API
# ============================================================

def get_candles(
    symbol,
    resolution="5m"
):

    url = (
        f"{BASE_URL}/trade/"
        f"{symbol}/{resolution}"
    )

    try:

        r = requests.get(
            url,
            timeout=20,
            headers={
                "User-Agent":
                "crypto-pump-scanner/5.5"
            }
        )

        print(
            f"{symbol} HTTP:",
            r.status_code
        )

        if r.status_code != 200:

            print(
                f"{symbol} API ERROR:",
                r.text[:300]
            )

            return []

        try:

            data = r.json()

        except Exception as e:

            print(
                f"{symbol} JSON ERROR:",
                e
            )

            print(
                f"{symbol} RESPONSE:",
                r.text[:500]
            )

            return []

        if isinstance(data, dict):

            print(
                f"{symbol} RESPONSE KEYS:",
                list(data.keys())
            )

        candles = extract_candles(data)

        if not candles:

            print(
                f"{symbol}: NO CANDLES FOUND"
            )

            print(
                f"{symbol} SAMPLE:",
                str(data)[:700]
            )

            return []

        candles.sort(
            key=lambda x: x["time"]
        )

        unique = {}

        for candle in candles:
            unique[candle["time"]] = candle

        candles = list(
            unique.values()
        )

        candles.sort(
            key=lambda x: x["time"]
        )

        current_bucket = (
            int(time.time() // 300) * 300
        )

        closed = [
            c
            for c in candles
            if c["time"] < current_bucket
        ]

        closed = closed[-120:]

        if closed:

            print(
                f"{symbol}: "
                f"{len(closed)} closed candles | "
                f"close={closed[-1]['close']} | "
                f"volume={closed[-1]['volume']}"
            )

        return closed

    except requests.exceptions.Timeout:

        print(
            f"{symbol}: REQUEST TIMEOUT"
        )

        return []

    except requests.exceptions.RequestException as e:

        print(
            f"{symbol}: REQUEST ERROR:",
            e
        )

        return []

    except Exception as e:

        print(
            f"{symbol}: UNKNOWN ERROR:",
            e
        )

        return []


# ============================================================
# PRICE CHANGE
# ============================================================

def pct_change(
    candles,
    periods
):

    if len(candles) <= periods:
        return 0.0

    old = candles[
        -1 - periods
    ]["close"]

    new = candles[-1]["close"]

    if old == 0:
        return 0.0

    return (
        (new / old) - 1
    ) * 100


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    candles,
    period=14
):

    if len(candles) < period + 1:
        return 50.0

    closes = [
        c["close"]
        for c in candles
    ]

    gains = []
    losses = []

    for i in range(
        1,
        len(closes)
    ):

        diff = (
            closes[i]
            - closes[i - 1]
        )

        if diff >= 0:

            gains.append(diff)
            losses.append(0)

        else:

            gains.append(0)
            losses.append(
                abs(diff)
            )

    gains = gains[-period:]
    losses = losses[-period:]

    avg_gain = (
        sum(gains) / period
    )

    avg_loss = (
        sum(losses) / period
    )

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (
        100 / (1 + rs)
    )


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    candles,
    period=14
):

    if len(candles) < period + 1:
        return 0.0

    trs = []

    for i in range(
        1,
        len(candles)
    ):

        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[
            i - 1
        ]["close"]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )

        trs.append(tr)

    if len(trs) < period:
        return 0.0

    return (
        sum(trs[-period:])
        / period
    )


# ============================================================
# VOLUME RATIO
# ============================================================

def volume_ratio(
    candles,
    period=20
):

    if len(candles) < period + 1:
        return 0.0

    current = candles[-1]["volume"]

    previous = [
        c["volume"]
        for c in candles[
            -period - 1:-1
        ]
        if c["volume"] > 0
    ]

    if not previous:
        return 0.0

    avg_volume = mean(previous)

    if avg_volume <= 0:
        return 0.0

    return (
        current / avg_volume
    )


# ============================================================
# ICHIMOKU
# ============================================================

def ichimoku_signal(candles):

    if len(candles) < 60:
        return 0

    highs = [
        c["high"]
        for c in candles
    ]

    lows = [
        c["low"]
        for c in candles
    ]

    close = candles[-1]["close"]

    tenkan_high = max(
        highs[-9:]
    )

    tenkan_low = min(
        lows[-9:]
    )

    tenkan = (
        tenkan_high
        + tenkan_low
    ) / 2

    kijun_high = max(
        highs[-26:]
    )

    kijun_low = min(
        lows[-26:]
    )

    kijun = (
        kijun_high
        + kijun_low
    ) / 2

    span_a = (
        tenkan
        + kijun
    ) / 2

    span_b_high = max(
        highs[-52:]
    )

    span_b_low = min(
        lows[-52:]
    )

    span_b = (
        span_b_high
        + span_b_low
    ) / 2

    cloud_top = max(
        span_a,
        span_b
    )

    cloud_bottom = min(
        span_a,
        span_b
    )

    if (
        close > cloud_top
        and tenkan > kijun
    ):
        return 1

    if (
        close < cloud_bottom
        and tenkan < kijun
    ):
        return -1

    return 0


# ============================================================
# SWINGS
# ============================================================

def find_swing_highs(
    candles,
    window=2
):

    points = []

    if len(candles) < (
        window * 2 + 1
    ):
        return points

    for i in range(
        window,
        len(candles) - window
    ):

        high = candles[i]["high"]

        left = [
            candles[j]["high"]
            for j in range(
                i - window,
                i
            )
        ]

        right = [
            candles[j]["high"]
            for j in range(
                i + 1,
                i + window + 1
            )
        ]

        if (
            high >= max(left)
            and high >= max(right)
        ):

            points.append(
                (i, high)
            )

    return points


def find_swing_lows(
    candles,
    window=2
):

    points = []

    if len(candles) < (
        window * 2 + 1
    ):
        return points

    for i in range(
        window,
        len(candles) - window
    ):

        low = candles[i]["low"]

        left = [
            candles[j]["low"]
            for j in range(
                i - window,
                i
            )
        ]

        right = [
            candles[j]["low"]
            for j in range(
                i + 1,
                i + window + 1
            )
        ]

        if (
            low <= min(left)
            and low <= min(right)
        ):

            points.append(
                (i, low)
            )

    return points


# ============================================================
# TRENDLINE
# ============================================================

def trendline_break(candles):

    if len(candles) < 30:

        return {
            "type": None,
            "price": None
        }

    window_candles = candles[-60:]

    highs = find_swing_highs(
        window_candles,
        2
    )

    lows = find_swing_lows(
        window_candles,
        2
    )

    current_close = (
        window_candles[-1]["close"]
    )

    previous_close = (
        window_candles[-2]["close"]
    )

    last_index = (
        len(window_candles) - 1
    )

    previous_index = (
        len(window_candles) - 2
    )

    if len(highs) >= 2:

        p1 = highs[-2]
        p2 = highs[-1]

        x1, y1 = p1
        x2, y2 = p2

        if (
            x2 > x1
            and y2 < y1
        ):

            slope = (
                (y2 - y1)
                / (x2 - x1)
            )

            line_prev = (
                y1
                + slope
                * (previous_index - x1)
            )

            line_now = (
                y1
                + slope
                * (last_index - x1)
            )

            buffer = (
                line_now * 0.0005
            )

            if (
                previous_close <= line_prev
                and current_close
                > line_now + buffer
            ):

                return {
                    "type": "BREAKOUT",
                    "price": line_now
                }

    if len(lows) >= 2:

        p1 = lows[-2]
        p2 = lows[-1]

        x1, y1 = p1
        x2, y2 = p2

        if (
            x2 > x1
            and y2 > y1
        ):

            slope = (
                (y2 - y1)
                / (x2 - x1)
            )

            line_prev = (
                y1
                + slope
                * (previous_index - x1)
            )

            line_now = (
                y1
                + slope
                * (last_index - x1)
            )

            buffer = (
                line_now * 0.0005
            )

            if (
                previous_close >= line_prev
                and current_close
                < line_now - buffer
            ):

                return {
                    "type": "BREAKDOWN",
                    "price": line_now
                }

    return {
        "type": None,
        "price": None
    }


# ============================================================
# SCORE
# ============================================================

def calculate_score(candles):

    p5 = pct_change(
        candles,
        1
    )

    p10 = pct_change(
        candles,
        2
    )

    p15 = pct_change(
        candles,
        3
    )

    rsi = calculate_rsi(
        candles
    )

    vol = volume_ratio(
        candles
    )

    ichi = ichimoku_signal(
        candles
    )

    tl = trendline_break(
        candles
    )

    long_score = 0
    short_score = 0

    if p5 > 0:
        long_score += 15

    if p10 > 0:
        long_score += 15

    if p15 > 0:
        long_score += 15

    if p5 < 0:
        short_score += 15

    if p10 < 0:
        short_score += 15

    if p15 < 0:
        short_score += 15

    if rsi >= 55:
        long_score += 10

    if rsi <= 45:
        short_score += 10

    if vol >= 1.5:

        if p5 > 0:
            long_score += 15

        if p5 < 0:
            short_score += 15

    if ichi == 1:
        long_score += 10

    elif ichi == -1:
        short_score += 10

    if tl["type"] == "BREAKOUT":
        long_score += 10

    elif tl["type"] == "BREAKDOWN":
        short_score += 10

    long_score = min(
        long_score,
        100
    )

    short_score = min(
        short_score,
        100
    )

    if long_score >= short_score:

        direction = "LONG"
        score = long_score

    else:

        direction = "SHORT"
        score = short_score

    return {
        "direction": direction,
        "score": score,
        "long_score": long_score,
        "short_score": short_score,
        "p5": p5,
        "p10": p10,
        "p15": p15,
        "rsi": rsi,
        "volume": vol,
        "ichimoku": ichi,
        "trendline": tl,
    }


# ============================================================
# CONFIRMATION
# ============================================================

def confirmation_ok(
    symbol,
    direction,
    candle_time
):

    state = load_json(
        STATE_FILE,
        {}
    )

    if not isinstance(
        state,
        dict
    ):
        state = {}

    if symbol not in state:
        state[symbol] = {}

    item = state[symbol]

    last_direction = item.get(
        "direction"
    )

    last_candle = item.get(
        "last_candle_time"
    )

    if last_direction != direction:

        state[symbol] = {
            "direction": direction,
            "count": 1,
            "last_candle_time": candle_time,
        }

        save_json(
            STATE_FILE,
            state
        )

        return False, 1

    if last_candle == candle_time:

        count = int(
            item.get(
                "count",
                1
            )
        )

        return (
            count >= 2,
            count
        )

    count = int(
        item.get(
            "count",
            0
        )
    ) + 1

    count = min(
        count,
        2
    )

    state[symbol] = {
        "direction": direction,
        "count": count,
        "last_candle_time": candle_time,
    }

    save_json(
        STATE_FILE,
        state
    )

    return (
        count >= 2,
        count
    )


# ============================================================
# PRICE FORMAT
# ============================================================

def fmt_price(price):

    if price >= 1000:
        return f"{price:.2f}"

    if price >= 100:
        return f"{price:.3f}"

    if price >= 1:
        return f"{price:.4f}"

    if price >= 0.1:
        return f"{price:.5f}"

    if price >= 0.01:
        return f"{price:.6f}"

    return f"{price:.8f}"


# ============================================================
# ENTRY / SL / TP
# ============================================================

def calculate_levels(
    candles,
    direction
):

    entry = candles[-1]["close"]

    atr = calculate_atr(
        candles,
        14
    )

    if atr <= 0:
        return None

    recent = candles[-12:]

    if direction == "LONG":

        swing_low = min(
            c["low"]
            for c in recent
        )

        structural_risk = (
            entry - swing_low
        )

        min_risk = atr * 0.8

        risk = max(
            structural_risk,
            min_risk
        )

        max_risk = atr * 2.2

        risk = min(
            risk,
            max_risk
        )

        sl = entry - risk
        tp1 = entry + risk
        tp2 = entry + risk * 2
        tp3 = entry + risk * 3

    else:

        swing_high = max(
            c["high"]
            for c in recent
        )

        structural_risk = (
            swing_high - entry
        )

        min_risk = atr * 0.8

        risk = max(
            structural_risk,
            min_risk
        )

        max_risk = atr * 2.2

        risk = min(
            risk,
            max_risk
        )

        sl = entry + risk
        tp1 = entry - risk
        tp2 = entry - risk * 2
        tp3 = entry - risk * 3

    return {
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk": risk,
        "atr": atr,
    }


# ============================================================
# BTC REGIME
# ============================================================

def btc_regime(candles):

    if len(candles) < 20:
        return "NEUTRAL"

    p15 = pct_change(
        candles,
        3
    )

    rsi = calculate_rsi(
        candles
    )

    if (
        p15 >= 1.0
        and rsi >= 55
    ):
        return "BULLISH"

    if (
        p15 <= -1.0
        and rsi <= 45
    ):
        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def signal_message(
    symbol,
    data,
    levels,
    confirmation
):

    direction = data["direction"]

    if direction == "LONG":

        emoji = "🟢"
        title = "STRONG PUMP SIGNAL"

    else:

        emoji = "🔴"
        title = "STRONG DUMP SIGNAL"

    tl = data["trendline"]["type"]

    if tl == "BREAKOUT":

        trendline_text = (
            "🔺 Trendline BREAKOUT"
        )

    elif tl == "BREAKDOWN":

        trendline_text = (
            "🔻 Trendline BREAKDOWN"
        )

    else:

        trendline_text = (
            "➖ Trendline: None"
        )

    message = f"""
🚨 <b>{title}</b>
━━━━━━━━━━━━━━━━━━

{emoji} <b>{symbol}/USDT — {direction}</b>

⭐ <b>Score:</b> {data["score"]}/100

💰 <b>Entry:</b> {fmt_price(levels["entry"])}
🛑 <b>Stop Loss:</b> {fmt_price(levels["sl"])}

🎯 <b>TP1:</b> {fmt_price(levels["tp1"])}
🎯 <b>TP2:</b> {fmt_price(levels["tp2"])}
🚀 <b>TP3:</b> {fmt_price(levels["tp3"])}

📊 <b>Risk:</b> {fmt_price(levels["risk"])}

📈 <b>R:R:</b> 1:1 / 1:2 / 1:3

📊 <b>5m:</b> {data["p5"]:+.2f}%
📊 <b>10m:</b> {data["p10"]:+.2f}%
📊 <b>15m:</b> {data["p15"]:+.2f}%

📈 <b>RSI:</b> {data["rsi"]:.1f}
📊 <b>Volume:</b> {data["volume"]:.2f}x

{trendline_text}

✅ <b>Confirmation:</b> {confirmation}/2

📐 <b>ATR:</b> {fmt_price(levels["atr"])}

━━━━━━━━━━━━━━━━━━
🤖 <b>Engine {VERSION}</b>
"""

    return message.strip()


# ============================================================
# WATCHLIST
# ============================================================

def watchlist_line(
    rank,
    symbol,
    data
):

    direction = data["direction"]

    if direction == "LONG":
        emoji = "🟢"
    else:
        emoji = "🔴"

    features = []

    tl = data["trendline"]["type"]

    if tl == "BREAKOUT":

        features.append(
            "Trendline 🔺"
        )

    elif tl == "BREAKDOWN":

        features.append(
            "Trendline 🔻"
        )

    if data["ichimoku"] == 1:

        features.append(
            "Ichimoku 🟢"
        )

    elif data["ichimoku"] == -1:

        features.append(
            "Ichimoku 🔴"
        )

    if (
        data["p5"] > 0
        and data["p10"] > 0
        and data["p15"] > 0
    ):

        features.append(
            "MOMENTUM"
        )

    if (
        data["p5"] < 0
        and data["p10"] < 0
        and data["p15"] < 0
    ):

        features.append(
            "BREAKDOWN"
        )

    if not features:

        features.append(
            "WATCH"
        )

    feature_text = (
        " | ".join(features)
    )

    return (
        f"{rank}. {emoji} "
        f"<b>{symbol}</b> ⭐ "
        f"{data['score']}/100\n"
        f"5m {data['p5']:+.2f}% | "
        f"10m {data['p10']:+.2f}% | "
        f"15m {data['p15']:+.2f}%\n"
        f"RSI {data['rsi']:.1f} | "
        f"Vol {data['volume']:.2f}x\n"
        f"📌 {feature_text}"
    )


# ============================================================
# ============================================================
# UT BOT
# ============================================================
# ============================================================

def calculate_utbot_atr(candles, period=10):

    if len(candles) < period + 1:
        return []

    tr = [None] * len(candles)

    for i in range(1, len(candles)):

        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]

        tr[i] = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )

    atr = [None] * len(candles)

    valid_tr = [
        tr[i]
        for i in range(1, len(candles))
        if tr[i] is not None
    ]

    if len(valid_tr) < period:
        return atr

    first_atr = sum(
        valid_tr[:period]
    ) / period

    seed_index = period

    atr[seed_index] = first_atr

    # Wilder RMA / Pine ATR
    for i in range(
        seed_index + 1,
        len(candles)
    ):

        if tr[i] is None:
            continue

        atr[i] = (
            (
                atr[i - 1]
                * (period - 1)
            )
            + tr[i]
        ) / period

    return atr


def calculate_utbot_signal(candles):

    if len(candles) < UT_ATR_PERIOD + 5:
        return None

    atr_values = calculate_utbot_atr(
        candles,
        UT_ATR_PERIOD
    )

    src = [
        c["close"]
        for c in candles
    ]

    trailing = [0.0] * len(candles)
    pos = [0] * len(candles)

    for i in range(
        1,
        len(candles)
    ):

        if atr_values[i] is None:
            continue

        nloss = (
            UT_KEY_VALUE
            * atr_values[i]
        )

        prev_stop = trailing[i - 1]

        current_src = src[i]
        previous_src = src[i - 1]

        if (
            current_src > prev_stop
            and previous_src > prev_stop
        ):

            trailing[i] = max(
                prev_stop,
                current_src - nloss
            )

        elif (
            current_src < prev_stop
            and previous_src < prev_stop
        ):

            trailing[i] = min(
                prev_stop,
                current_src + nloss
            )

        elif current_src > prev_stop:

            trailing[i] = (
                current_src - nloss
            )

        else:

            trailing[i] = (
                current_src + nloss
            )

        if (
            previous_src < prev_stop
            and current_src > prev_stop
        ):

            pos[i] = 1

        elif (
            previous_src > prev_stop
            and current_src < prev_stop
        ):

            pos[i] = -1

        else:

            pos[i] = pos[i - 1]

    # Only the LAST CLOSED candle
    i = len(candles) - 1

    if (
        atr_values[i] is None
        or trailing[i] == 0
    ):
        return None

    previous_stop = trailing[i - 1]
    current_stop = trailing[i]

    previous_src = src[i - 1]
    current_src = src[i]

    # EMA(1) == source
    above = (
        current_src > current_stop
        and previous_src <= previous_stop
    )

    below = (
        current_stop > current_src
        and previous_stop <= previous_src
    )

    buy = (
        current_src > current_stop
        and above
    )

    sell = (
        current_src < current_stop
        and below
    )

    if buy:

        return {
            "signal": "BUY",
            "direction": "LONG",
            "price": current_src,
            "candle_time": candles[i]["time"],
            "trailing_stop": current_stop,
            "atr": atr_values[i],
        }

    if sell:

        return {
            "signal": "SELL",
            "direction": "SHORT",
            "price": current_src,
            "candle_time": candles[i]["time"],
            "trailing_stop": current_stop,
            "atr": atr_values[i],
        }

    return {
        "signal": None,
        "direction": None,
        "price": current_src,
        "candle_time": candles[i]["time"],
        "trailing_stop": current_stop,
        "atr": atr_values[i],
    }


# ============================================================
# UT SETUP LEVELS
# ============================================================

def calculate_ut_setup_levels(
    candles,
    direction
):

    entry = candles[-1]["close"]

    atr = calculate_atr(
        candles,
        14
    )

    if atr <= 0:
        return None

    recent = candles[-12:]

    if direction == "LONG":

        swing_low = min(
            c["low"]
            for c in recent
        )

        structural_risk = (
            entry - swing_low
        )

        min_risk = atr * 0.8

        risk = max(
            structural_risk,
            min_risk
        )

        max_risk = atr * 2.2

        risk = min(
            risk,
            max_risk
        )

        sl = entry - risk

        tp1 = entry + risk
        tp15 = entry + risk * 1.5
        tp2 = entry + risk * 2

    else:

        swing_high = max(
            c["high"]
            for c in recent
        )

        structural_risk = (
            swing_high - entry
        )

        min_risk = atr * 0.8

        risk = max(
            structural_risk,
            min_risk
        )

        max_risk = atr * 2.2

        risk = min(
            risk,
            max_risk
        )

        sl = entry + risk

        tp1 = entry - risk
        tp15 = entry - risk * 1.5
        tp2 = entry - risk * 2

    return {
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp15": tp15,
        "tp2": tp2,
        "risk": risk,
        "atr": atr,
    }


# ============================================================
# UT SETUP MESSAGE
# ============================================================

def ut_setup_message(
    symbol,
    data,
    ut,
    levels,
    rank
):

    if ut["signal"] == "BUY":

        emoji = "🟢"
        title = "UT BOT BUY SETUP"

    else:

        emoji = "🔴"
        title = "UT BOT SELL SETUP"

    direction = ut["direction"]

    return f"""
🚨 <b>{title}</b>
━━━━━━━━━━━━━━━━━━

{emoji} <b>{symbol}/USDT — {direction}</b>

🏆 <b>TOP 5 Rank:</b> #{rank}
⭐ <b>Scanner Score:</b> {data["score"]}/100

📡 <b>UT Bot:</b> {ut["signal"]}
⏱ <b>Timeframe:</b> 5m

💰 <b>Entry:</b> {fmt_price(levels["entry"])}
🛑 <b>Stop Loss:</b> {fmt_price(levels["sl"])}

🎯 <b>TP1 — 1R:</b> {fmt_price(levels["tp1"])}
🎯 <b>TP2 — 1.5R:</b> {fmt_price(levels["tp15"])}
🚀 <b>TP3 — 2R:</b> {fmt_price(levels["tp2"])}

📊 <b>Risk:</b> {fmt_price(levels["risk"])}
📐 <b>ATR:</b> {fmt_price(ut["atr"])}

📈 <b>RSI:</b> {data["rsi"]:.1f}
📊 <b>Volume:</b> {data["volume"]:.2f}x

🛑 <b>UT Stop:</b> {fmt_price(ut["trailing_stop"])}

📌 <b>Closed Candle Signal</b>

━━━━━━━━━━━━━━━━━━
🤖 <b>UT Setup Engine</b>
""".strip()


# ============================================================
# UT STATE
# ============================================================

def get_ut_state():

    state = load_json(
        STATE_FILE,
        {}
    )

    if not isinstance(
        state,
        dict
    ):
        state = {}

    if not isinstance(
        state.get("_utbot"),
        dict
    ):
        state["_utbot"] = {}

    ut = state["_utbot"]

    if not isinstance(
        ut.get("signals"),
        dict
    ):
        ut["signals"] = {}

    if not isinstance(
        ut.get("stats"),
        dict
    ):
        ut["stats"] = {
            "total": 0,
            "closed": 0,
            "wins_1r": 0,
            "wins_15r": 0,
            "wins_2r": 0,
            "losses": 0,
        }

    return state


# ============================================================
# UT WIN RATE
# ============================================================

def calculate_win_rate(
    wins,
    losses
):

    total = wins + losses

    if total <= 0:
        return 0.0

    return (
        wins / total
    ) * 100


def ut_stats_message():

    state = get_ut_state()

    stats = state["_utbot"]["stats"]

    total = int(
        stats.get(
            "total",
            0
        )
    )

    closed = int(
        stats.get(
            "closed",
            0
        )
    )

    wins_1r = int(
        stats.get(
            "wins_1r",
            0
        )
    )

    wins_15r = int(
        stats.get(
            "wins_15r",
            0
        )
    )

    wins_2r = int(
        stats.get(
            "wins_2r",
            0
        )
    )

    losses = int(
        stats.get(
            "losses",
            0
        )
    )

    wr1 = calculate_win_rate(
        wins_1r,
        losses
    )

    wr15 = calculate_win_rate(
        wins_15r,
        losses
    )

    wr2 = calculate_win_rate(
        wins_2r,
        losses
    )

    open_count = max(
        total - closed,
        0
    )

    return f"""
📊 <b>UT BOT PERFORMANCE</b>
━━━━━━━━━━━━━━━━━━

📌 <b>Total Setups:</b> {total}
📂 <b>Closed:</b> {closed}
⏳ <b>Open:</b> {open_count}

🏆 <b>1R Win Rate:</b> {wr1:.1f}%
🏆 <b>1.5R Win Rate:</b> {wr15:.1f}%
🏆 <b>2R Win Rate:</b> {wr2:.1f}%

🟢 <b>1R Wins:</b> {wins_1r}
🟢 <b>1.5R Wins:</b> {wins_15r}
🟢 <b>2R Wins:</b> {wins_2r}

🔴 <b>Losses:</b> {losses}

━━━━━━━━━━━━━━━━━━
🤖 <b>UT Setup Engine</b>
""".strip()


# ============================================================
# UPDATE UT SETUPS
# ============================================================

def update_ut_setups(results):

    state = get_ut_state()

    ut_state = state["_utbot"]
    signals = ut_state["signals"]
    stats = ut_state["stats"]

    changed = False

    for key in list(signals.keys()):

        setup = signals[key]

        if not isinstance(
            setup,
            dict
        ):
            continue

        if setup.get("status") != "OPEN":
            continue

        symbol = setup.get("symbol")

        if not symbol:
            continue

        candles = None

        for item in results:

            if item["symbol"] == symbol:

                candles = item["data"].get(
                    "candles"
                )

                break

        if not candles:
            continue

        entry = float(
            setup["entry"]
        )

        sl = float(
            setup["sl"]
        )

        tp1 = float(
            setup["tp1"]
        )

        tp15 = float(
            setup["tp15"]
        )

        tp2 = float(
            setup["tp2"]
        )

        direction = setup["direction"]

        signal_time = int(
            setup["candle_time"]
        )

        future_candles = [
            c
            for c in candles
            if c["time"] > signal_time
        ]

        if not future_candles:
            continue

        result = None
        result_level = None

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if direction == "LONG":

            for candle in future_candles:

                high = candle["high"]
                low = candle["low"]

                hit_sl = low <= sl
                hit_tp2 = high >= tp2
                hit_tp15 = high >= tp15
                hit_tp1 = high >= tp1

                # Conservative rule:
                # if SL and target are both touched
                # in the same candle, count LOSS.
                if hit_sl and (
                    hit_tp2
                    or hit_tp15
                    or hit_tp1
                ):

                    result = "LOSS"
                    result_level = "SL"
                    break

                if hit_sl:

                    result = "LOSS"
                    result_level = "SL"
                    break

                if hit_tp2:

                    result = "WIN_2R"
                    result_level = "2R"
                    break

                if hit_tp15:

                    result = "WIN_15R"
                    result_level = "1.5R"
                    break

                if hit_tp1:

                    result = "WIN_1R"
                    result_level = "1R"
                    break

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        else:

            for candle in future_candles:

                high = candle["high"]
                low = candle["low"]

                hit_sl = high >= sl
                hit_tp2 = low <= tp2
                hit_tp15 = low <= tp15
                hit_tp1 = low <= tp1

                # Conservative same-candle rule
                if hit_sl and (
                    hit_tp2
                    or hit_tp15
                    or hit_tp1
                ):

                    result = "LOSS"
                    result_level = "SL"
                    break

                if hit_sl:

                    result = "LOSS"
                    result_level = "SL"
                    break

                if hit_tp2:

                    result = "WIN_2R"
                    result_level = "2R"
                    break

                if hit_tp15:

                    result = "WIN_15R"
                    result_level = "1.5R"
                    break

                if hit_tp1:

                    result = "WIN_1R"
                    result_level = "1R"
                    break

        if not result:
            continue

        setup["status"] = "CLOSED"
        setup["result"] = result
        setup["result_level"] = result_level
        setup["closed_time"] = int(time.time())

        stats["closed"] = int(
            stats.get(
                "closed",
                0
            )
        ) + 1

        if result == "LOSS":

            stats["losses"] = int(
                stats.get(
                    "losses",
                    0
                )
            ) + 1

        elif result == "WIN_1R":

            stats["wins_1r"] = int(
                stats.get(
                    "wins_1r",
                    0
                )
            ) + 1

        elif result == "WIN_15R":

            stats["wins_1r"] = int(
                stats.get(
                    "wins_1r",
                    0
                )
            ) + 1

            stats["wins_15r"] = int(
                stats.get(
                    "wins_15r",
                    0
                )
            ) + 1

        elif result == "WIN_2R":

            stats["wins_1r"] = int(
                stats.get(
                    "wins_1r",
                    0
                )
            ) + 1

            stats["wins_15r"] = int(
                stats.get(
                    "wins_15r",
                    0
                )
            ) + 1

            stats["wins_2r"] = int(
                stats.get(
                    "wins_2r",
                    0
                )
            ) + 1

        changed = True

        print(
            f"UT RESULT: "
            f"{symbol} "
            f"{direction} "
            f"{result}"
        )

    if changed:

        save_json(
            STATE_FILE,
            state
        )

    return changed


# ============================================================
# CREATE UT SETUP
# ============================================================

def process_ut_top5(top5):

    if not top5:
        return

    # --------------------------------------------------------
    # Find ALL fresh UT signals inside TOP 5
    # --------------------------------------------------------

    candidates = []

    for rank, item in enumerate(
        top5,
        1
    ):

        symbol = item["symbol"]
        data = item["data"]
        candles = data["candles"]

        ut = calculate_utbot_signal(
            candles
        )

        if not ut:
            continue

        if not ut.get("signal"):
            continue

        candidates.append({
            "rank": rank,
            "symbol": symbol,
            "data": data,
            "candles": candles,
            "ut": ut,
        })

    if not candidates:

        print(
            "UT TOP 5: NO FRESH SIGNAL"
        )

        return

    # --------------------------------------------------------
    # Best = highest scanner score
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x: (
            x["data"]["score"],
            -x["rank"]
        ),
        reverse=True
    )

    best = candidates[0]

    rank = best["rank"]
    symbol = best["symbol"]
    data = best["data"]
    candles = best["candles"]
    ut = best["ut"]

    print(
        f"UT SIGNAL FOUND: "
        f"{symbol} "
        f"{ut['signal']} "
        f"Score={data['score']} "
        f"Rank={rank}"
    )

    # --------------------------------------------------------
    # Build unique signal key
    # --------------------------------------------------------

    signal_key = (
        f"{symbol}_"
        f"{ut['signal']}_"
        f"{ut['candle_time']}"
    )

    state = get_ut_state()

    signals = state["_utbot"]["signals"]

    # Already processed
    if signal_key in signals:

        print(
            f"UT: already processed "
            f"{signal_key}"
        )

        return

    # --------------------------------------------------------
    # Levels
    # --------------------------------------------------------

    levels = calculate_ut_setup_levels(
        candles,
        ut["direction"]
    )

    if not levels:

        print(
            f"UT: cannot calculate levels "
            f"for {symbol}"
        )

        return

    # --------------------------------------------------------
    # Save setup
    # --------------------------------------------------------

    setup = {
        "symbol": symbol,
        "signal": ut["signal"],
        "direction": ut["direction"],
        "rank": rank,
        "score": data["score"],
        "candle_time": ut["candle_time"],
        "entry": levels["entry"],
        "sl": levels["sl"],
        "tp1": levels["tp1"],
        "tp15": levels["tp15"],
        "tp2": levels["tp2"],
        "risk": levels["risk"],
        "atr": levels["atr"],
        "ut_stop": ut["trailing_stop"],
        "status": "OPEN",
        "created_time": int(time.time()),
    }

    signals[signal_key] = setup

    stats = state["_utbot"]["stats"]

    stats["total"] = int(
        stats.get(
            "total",
            0
        )
    ) + 1

    save_json(
        STATE_FILE,
        state
    )

    # --------------------------------------------------------
    # Send setup
    # --------------------------------------------------------

    message = ut_setup_message(
        symbol,
        data,
        ut,
        levels,
        rank
    )

    print(
        message
    )

    send_telegram(
        message
    )

    # --------------------------------------------------------
    # Send current stats
    # --------------------------------------------------------

    stats_message = ut_stats_message()

    print(
        stats_message
    )

    send_telegram(
        stats_message
    )


# ============================================================
# MAIN
# ============================================================

def main():

    # ========================================================
    # TELEGRAM CONNECTION TEST
    # ========================================================

    print(
        "TELEGRAM TOKEN:",
        "SET" if TELEGRAM_BOT_TOKEN else "MISSING"
    )

    print(
        "TELEGRAM CHAT ID:",
        "SET" if TELEGRAM_CHAT_ID else "MISSING"
    )

    print("=" * 60)

    print(
        f"CRYPTO PUMP / DUMP SCANNER {VERSION}"
    )

    print("=" * 60)

    results = []

    # --------------------------------------------------------
    # BTC
    # --------------------------------------------------------

    btc_candles = get_candles(
        COINS["BTC"]
    )

    regime = btc_regime(
        btc_candles
    )

    print(
        "BTC regime:",
        regime
    )

    # --------------------------------------------------------
    # ALL COINS
    # --------------------------------------------------------

    for symbol, kraken_symbol in COINS.items():

        candles = get_candles(
            kraken_symbol
        )

        if len(candles) < 60:

            print(
                f"{symbol}: "
                f"not enough candles "
                f"({len(candles)})"
            )

            continue

        try:

            data = calculate_score(
                candles
            )

            data["candles"] = candles

            results.append({
                "symbol": symbol,
                "data": data,
            })

        except Exception as e:

            print(
                f"{symbol}: "
                f"CALCULATION ERROR:",
                e
            )

    # --------------------------------------------------------
    # WATCHLIST
    # --------------------------------------------------------

    results.sort(
        key=lambda x: x["data"]["score"],
        reverse=True
    )

    top5 = results[:5]

    watchlist = [
        "👀 <b>TOP 5 WATCHLIST v5.5</b>",
        "━━━━━━━━━━━━━━━━━━"
    ]

    if not top5:

        watchlist.append(
            "⚠️ <b>NO MARKET DATA</b>"
        )

        watchlist.append(
            "Kraken candle parser returned 0 valid candles."
        )

    else:

        for i, item in enumerate(
            top5,
            1
        ):

            watchlist.append(
                watchlist_line(
                    i,
                    item["symbol"],
                    item["data"]
                )
            )

    watchlist.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    watchlist.append(
        f"₿ BTC Regime: <b>{regime}</b>"
    )

    watchlist.append(
        "📡 Closed 5M TRADE OHLCV"
    )

    watchlist.append(
        "📊 REAL VOLUME"
    )

    watchlist.append(
        f"🤖 Engine {VERSION}"
    )

    watchlist_message = (
        "\n".join(watchlist)
    )

    print(
        watchlist_message
    )

    send_telegram(
        watchlist_message
    )

    # ========================================================
    # NEW: UPDATE PREVIOUS UT SETUPS
    # ========================================================

    update_ut_setups(
        results
    )

    # ========================================================
    # NEW: UT BOT ON TOP 5
    # ========================================================

    process_ut_top5(
        top5
    )

    # --------------------------------------------------------
    # FINAL SIGNAL
    # --------------------------------------------------------

    for item in results:

        symbol = item["symbol"]

        data = item["data"]

        candles = data["candles"]

        score = data["score"]

        volume = data["volume"]

        direction = data["direction"]

        # ----------------------------------------------------
        # SCORE
        # ----------------------------------------------------

        if score < 75:
            continue

        # ----------------------------------------------------
        # VOLUME
        # ----------------------------------------------------

        if volume < 1.5:
            continue

        # ----------------------------------------------------
        # MOMENTUM
        # ----------------------------------------------------

        if direction == "LONG":

            if not (
                data["p5"] > 0
                and data["p10"] > 0
                and data["p15"] > 0
            ):
                continue

        else:

            if not (
                data["p5"] < 0
                and data["p10"] < 0
                and data["p15"] < 0
            ):
                continue

        # ----------------------------------------------------
        # TRENDLINE
        # ----------------------------------------------------

        tl = data["trendline"]["type"]

        if (
            direction == "LONG"
            and tl != "BREAKOUT"
        ):
            continue

        if (
            direction == "SHORT"
            and tl != "BREAKDOWN"
        ):
            continue

        # ----------------------------------------------------
        # CONFIRMATION
        # ----------------------------------------------------

        candle_time = (
            candles[-1]["time"]
        )

        confirmed, count = confirmation_ok(
            symbol,
            direction,
            candle_time
        )

        if not confirmed:

            print(
                f"{symbol}: "
                f"waiting confirmation "
                f"{count}/2"
            )

            continue

        # ----------------------------------------------------
        # LEVELS
        # ----------------------------------------------------

        levels = calculate_levels(
            candles,
            direction
       
