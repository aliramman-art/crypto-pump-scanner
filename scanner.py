import os
import json
import time
from datetime import datetime, timezone

import requests


# =========================================================
# CONFIG
# =========================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"

HISTORY_FILE = "price_data.json"

# Keep approximately 8 hours of history
HISTORY_SECONDS = 8 * 60 * 60

COINS = [
    "bitcoin",
    "ethereum",
    "binancecoin",
    "solana",
    "ripple",
    "dogecoin",
    "cardano",
    "avalanche-2",
    "chainlink",
    "polkadot",
    "tron",
    "litecoin",
    "bitcoin-cash",
    "cosmos",
    "uniswap",
    "ethereum-classic",
    "stellar",
    "near",
    "aptos",
    "filecoin",
    "arbitrum",
    "optimism",
    "sui",
    "injective-protocol",
    "aave",
    "maker",
    "algorand",
    "vechain",
    "sei-network",
    "celestia",
]


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secrets are missing.")
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=20,
        )

        response.raise_for_status()

        result = response.json()

        if result.get("ok"):
            print("Telegram message sent.")
            return True

        print("Telegram rejected message:", result)
        return False

    except Exception as e:

        print("Telegram error:", e)
        return False


# =========================================================
# HISTORY
# =========================================================

def load_history():

    if not os.path.exists(HISTORY_FILE):
        return {}

    try:

        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8",
        ) as file:

            data = json.load(file)

        if isinstance(data, dict):
            return data

    except Exception as e:

        print("History read error:", e)

    return {}


def save_history(history):

    try:

        temp_file = HISTORY_FILE + ".tmp"

        with open(
            temp_file,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                history,
                file,
                ensure_ascii=False,
                separators=(",", ":"),
            )

        os.replace(
            temp_file,
            HISTORY_FILE,
        )

    except Exception as e:

        print("History save error:", e)


def update_history(history, market_data):

    now = int(time.time())

    cutoff = now - HISTORY_SECONDS

    for coin in market_data:

        coin_id = coin.get("id")

        price = coin.get("current_price")

        volume = coin.get("total_volume")

        if not coin_id or price is None:
            continue

        if coin_id not in history:
            history[coin_id] = []

        history[coin_id].append(
            {
                "time": now,
                "price": float(price),
                "volume": float(volume or 0),
            }
        )

        history[coin_id] = [
            item
            for item in history[coin_id]
            if int(item.get("time", 0)) >= cutoff
        ]

        if len(history[coin_id]) > 150:
            history[coin_id] = history[coin_id][-150:]


# =========================================================
# PRICE CHANGE
# =========================================================

def get_change(items, minutes):

    if len(items) < 2:
        return None

    latest = items[-1]

    latest_time = int(latest["time"])

    target_time = latest_time - (
        minutes * 60
    )

    candidates = [
        item
        for item in items[:-1]
        if int(item.get("time", 0))
        <= target_time
    ]

    if not candidates:
        return None

    previous = min(
        candidates,
        key=lambda x: abs(
            int(x["time"]) - target_time
        ),
    )

    previous_price = float(
        previous["price"]
    )

    latest_price = float(
        latest["price"]
    )

    if previous_price <= 0:
        return None

    return (
        (latest_price - previous_price)
        / previous_price
    ) * 100


# =========================================================
# RSI
# =========================================================

def calculate_rsi(items, period=14):

    prices = [
        float(x["price"])
        for x in items
        if x.get("price") is not None
    ]

    if len(prices) < period + 1:
        return None

    changes = [
        prices[i] - prices[i - 1]
        for i in range(1, len(prices))
    ]

    recent = changes[-period:]

    gains = [
        x if x > 0 else 0
        for x in recent
    ]

    losses = [
        abs(x) if x < 0 else 0
        for x in recent
    ]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:

        if avg_gain == 0:
            return 50.0

        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (
        100 / (1 + rs)
    )


# =========================================================
# EMA
# =========================================================

def calculate_ema(items, period):

    prices = [
        float(x["price"])
        for x in items
        if x.get("price") is not None
    ]

    if len(prices) < period:
        return None

    multiplier = 2 / (period + 1)

    ema = (
        sum(prices[:period])
        / period
    )

    for price in prices[period:]:

        ema = (
            (price - ema)
            * multiplier
        ) + ema

    return ema


# =========================================================
# VOLUME RATIO
# =========================================================

def calculate_volume_ratio(
    items,
    lookback=10,
):

    if len(items) < lookback + 1:
        return None

    current_volume = float(
        items[-1].get("volume", 0)
    )

    previous_volumes = [
        float(x.get("volume", 0))
        for x in items[-lookback - 1:-1]
    ]

    previous_volumes = [
        x for x in previous_volumes
        if x > 0
    ]

    if not previous_volumes:
        return None

    average_volume = (
        sum(previous_volumes)
        / len(previous_volumes)
    )

    if average_volume <= 0:
        return None

    return (
        current_volume
        / average_volume
    )


# =========================================================
# BREAKOUT / BREAKDOWN
# =========================================================

def calculate_breakout(items):

    if len(items) < 7:

        return {
            "breakout": False,
            "breakdown": False,
        }

    current_price = float(
        items[-1]["price"]
    )

    previous = items[-7:-1]

    prices = [
        float(x["price"])
        for x in previous
    ]

    if not prices:

        return {
            "breakout": False,
            "breakdown": False,
        }

    previous_high = max(prices)
    previous_low = min(prices)

    breakout = (
        current_price
        > previous_high * 1.0005
    )

    breakdown = (
        current_price
        < previous_low * 0.9995
    )

    return {
        "breakout": breakout,
        "breakdown": breakdown,
    }


# =========================================================
# ICHIMOKU
# =========================================================

def calculate_ichimoku(items):

    if len(items) < 52:

        return {
            "ready": False,
            "bullish": False,
            "bearish": False,
        }

    prices = [
        float(x["price"])
        for x in items
    ]

    current_price = prices[-1]

    tenkan_prices = prices[-9:]
    kijun_prices = prices[-26:]
    span_b_prices = prices[-52:]

    tenkan = (
        max(tenkan_prices)
        + min(tenkan_prices)
    ) / 2

    kijun = (
        max(kijun_prices)
        + min(kijun_prices)
    ) / 2

    span_a = (
        tenkan + kijun
    ) / 2

    span_b = (
        max(span_b_prices)
        + min(span_b_prices)
    ) / 2

    cloud_top = max(
        span_a,
        span_b,
    )

    cloud_bottom = min(
        span_a,
        span_b,
    )

    bullish = (
        current_price > cloud_top
        and tenkan > kijun
    )

    bearish = (
        current_price < cloud_bottom
        and tenkan < kijun
    )

    return {
        "ready": True,
        "bullish": bullish,
        "bearish": bearish,
        "tenkan": tenkan,
        "kijun": kijun,
        "span_a": span_a,
        "span_b": span_b,
    }


# =========================================================
# SCORE PUMP
# =========================================================

def calculate_pump_score(
    c5,
    c10,
    c15,
    rsi,
    ema5,
    ema10,
    volume_ratio,
    breakout,
    ichimoku,
    btc_1h,
):

    score = 0
    reasons = []

    # 5m momentum
    if c5 is not None:

        if 0.5 <= c5 < 2.5:
            score += 15
            reasons.append("5m momentum")

        elif 2.5 <= c5 < 5:
            score += 10

        elif c5 > 0:
            score += 4

    # 10m momentum
    if c10 is not None:

        if c10 >= 2:
            score += 10
            reasons.append("10m momentum")

        elif c10 > 0:
            score += 5

    # 15m trend
    if c15 is not None:

        if c15 >= 3:
            score += 10
            reasons.append("15m trend")

        elif c15 > 0:
            score += 5

    # acceleration
    if (
        c5 is not None
        and c10 is not None
        and c5 > 0
        and c10 > 0
        and c5 > c10 / 2
    ):

        score += 10
        reasons.append("Acceleration")

    # RSI
    if rsi is not None:

        if 55 <= rsi <= 70:

            score += 10
            reasons.append("RSI healthy")

        elif 70 < rsi <= 78:

            score += 6

        elif rsi > 82:

            score -= 5
            reasons.append("RSI overheated")

    # EMA
    if (
        ema5 is not None
        and ema10 is not None
        and ema5 > ema10
    ):

        score += 10
        reasons.append("EMA bullish")

    # Volume
    if volume_ratio is not None:

        if volume_ratio >= 3:

            score += 10
            reasons.append(
                f"Volume {volume_ratio:.1f}x"
            )

        elif volume_ratio >= 2:

            score += 8
            reasons.append(
                f"Volume {volume_ratio:.1f}x"
            )

        elif volume_ratio >= 1.5:

            score += 5
            reasons.append(
                f"Volume {volume_ratio:.1f}x"
            )

    # Breakout
    if breakout:

        score += 15
        reasons.append("BREAKOUT")

    # Ichimoku
    if ichimoku.get("ready"):

        if ichimoku.get("bullish"):

            score += 15
            reasons.append(
                "Ichimoku bullish"
            )

        elif (
            ichimoku.get("tenkan")
            and ichimoku.get("kijun")
            and ichimoku["tenkan"]
            > ichimoku["kijun"]
        ):

            score += 6

    # BTC
    if btc_1h is not None:

        if btc_1h > 0:

            score += 5
            reasons.append(
                "BTC supportive"
            )

        elif btc_1h < -1:

            score -= 5

    return max(
        0,
        min(100, score)
    ), reasons


# =========================================================
# SCORE DUMP
# =========================================================

def calculate_dump_score(
    c5,
    c10,
    c15,
    rsi,
    ema5,
    ema10,
    volume_ratio,
    breakdown,
    ichimoku,
    btc_1h,
):

    score = 0
    reasons = []

    # 5m downside
    if c5 is not None:

        if -2.5 < c5 <= -0.5:
            score += 15
            reasons.append("5m selling")

        elif -5 < c5 <= -2.5:
            score += 10

        elif c5 < 0:
            score += 4

    # 10m downside
    if c10 is not None:

        if c10 <= -2:

            score += 10
            reasons.append("10m selling")

        elif c10 < 0:

            score += 5

    # 15m downside
    if c15 is not None:

        if c15 <= -3:

            score += 10
            reasons.append("15m bearish")

        elif c15 < 0:

            score += 5

    # acceleration
    if (
        c5 is not None
        and c10 is not None
        and c5 < 0
        and c10 < 0
        and c5 < c10 / 2
    ):

        score += 10
        reasons.append("Down acceleration")

    # RSI
    if rsi is not None:

        if 30 <= rsi <= 45:

            score += 10
            reasons.append("RSI weak")

        elif rsi < 25:

            score -= 5
            reasons.append(
                "RSI oversold"
            )

    # EMA
    if (
        ema5 is not None
        and ema10 is not None
        and ema5 < ema10
    ):

        score += 10
        reasons.append("EMA bearish")

    # Volume
    if volume_ratio is not None:

        if volume_ratio >= 3:

            score += 10
            reasons.append(
                f"Volume {volume_ratio:.1f}x"
            )

        elif volume_ratio >= 2:

            score += 8
            reasons.append(
                f"Volume {volume_ratio:.1f}x"
            )

        elif volume_ratio >= 1.5:

            score += 5
            reasons.append(
                f"Volume {volume_ratio:.1f}x"
            )

    # Breakdown
    if breakdown:

        score += 15
        reasons.append("BREAKDOWN")

    # Ichimoku
    if ichimoku.get("ready"):

        if ichimoku.get("bearish"):

            score += 15
            reasons.append(
                "Ichimoku bearish"
            )

        elif (
            ichimoku.get("tenkan")
            and ichimoku.get("kijun")
            and ichimoku["tenkan"]
            < ichimoku["kijun"]
        ):

            score += 6

    # BTC
    if btc_1h is not None:

        if btc_1h < 0:

            score += 5
            reasons.append(
                "BTC bearish"
            )

    return max(
        0,
        min(100, score)
    ), reasons


# =========================================================
# MARKET DATA
# =========================================================

def get_market_data():

    params = {
        "vs_currency": "usd",
        "ids": ",".join(COINS),
        "order": "market_cap_desc",
        "per_page": 30,
        "page": 1,
        "sparkline": "false",
    }

    headers = {
        "User-Agent":
            "early-pump-engine/3.0"
    }

    try:

        response = requests.get(
            COINGECKO_URL,
            params=params,
            headers=headers,
            timeout=30,
        )

        print(
            "CoinGecko HTTP:",
            response.status_code,
        )

        response.raise_for_status()

        data = response.json()

        if not isinstance(data, list):
            return []

        return data

    except Exception as e:

        print(
            "CoinGecko error:",
            e,
        )

        return []


# =========================================================
# FORMAT
# =========================================================

def fmt_percent(value):

    if value is None:
        return "N/A"

    return f"{value:+.2f}%"


def fmt_rsi(value):

    if value is None:
        return "N/A"

    return f"{value:.1f}"


def fmt_volume(value):

    if value is None:
        return "N/A"

    return f"{value:.1f}x"


# =========================================================
# MAIN SCANNER
# =========================================================

def scan():

    print(
        "EARLY PUMP/DUMP ENGINE v3 START"
    )

    market_data = get_market_data()

    if not market_data:

        send_telegram(
            "🔴 <b>SCANNER ERROR</b>\n\n"
            "CoinGecko data unavailable."
        )

        return

    history = load_history()

    update_history(
        history,
        market_data
    )

    save_history(history)

    btc_history = history.get(
        "bitcoin",
        []
    )

    btc_1h = get_change(
        btc_history,
        60
    )

    results = []

    # =====================================================
    # ANALYZE
    # =====================================================

    for coin in market_data:

        coin_id = coin.get("id")

        symbol = coin.get(
            "symbol",
            ""
        ).upper()

        if not coin_id:
            continue

        items = history.get(
            coin_id,
            []
        )

        if len(items) < 3:
            continue

        c5 = get_change(
            items,
            5
        )

        c10 = get_change(
            items,
            10
        )

        c15 = get_change(
            items,
            15
        )

        rsi = calculate_rsi(
            items
        )

        ema5 = calculate_ema(
            items,
            5
        )

        ema10 = calculate_ema(
            items,
            10
        )

        volume_ratio = calculate_volume_ratio(
            items
        )

        levels = calculate_breakout(
            items
        )

        ichimoku = calculate_ichimoku(
            items
        )

        pump_score, pump_reasons = (
            calculate_pump_score(
                c5,
                c10,
                c15,
                rsi,
                ema5,
                ema10,
                volume_ratio,
                levels["breakout"],
                ichimoku,
                btc_1h,
            )
        )

        dump_score, dump_reasons = (
            calculate_dump_score(
                c5,
                c10,
                c15,
                rsi,
                ema5,
                ema10,
                volume_ratio,
                levels["breakdown"],
                ichimoku,
                btc_1h,
            )
        )

        if pump_score >= dump_score:

            direction = "PUMP"
            score = pump_score
            reasons = pump_reasons

        else:

            direction = "DUMP"
            score = dump_score
            reasons = dump_reasons

        results.append(
            {
                "symbol": symbol,
                "score": score,
                "direction": direction,
                "pump_score": pump_score,
                "dump_score": dump_score,
                "c5": c5,
                "c10": c10,
                "c15": c15,
                "rsi": rsi,
                "volume_ratio": volume_ratio,
                "breakout": levels["breakout"],
                "breakdown": levels["breakdown"],
                "ichimoku": ichimoku,
                "samples": len(items),
                "reasons": reasons,
            }
        )

    # =====================================================
    # WATCHLIST
    # =====================================================

    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    watchlist = results[:5]

    # =====================================================
    # STRONG SIGNALS
    # =====================================================

    strong_pumps = [
        x
        for x in results
        if (
            x["direction"] == "PUMP"
            and x["score"] >= 75
            and x["c5"] is not None
            and x["c10"] is not None
            and x["c15"] is not None
            and x["c5"] > 0
            and x["c10"] > 0
            and x["c15"] > 0
            and x["c5"] < 5
        )
    ]

    strong_dumps = [
        x
        for x in results
        if (
            x["direction"] == "DUMP"
            and x["score"] >= 75
            and x["c5"] is not None
            and x["c10"] is not None
            and x["c15"] is not None
            and x["c5"] < 0
            and x["c10"] < 0
            and x["c15"] < 0
        )
    ]

    # =====================================================
    # MESSAGE
    # =====================================================

    lines = [
        "🚨 <b>EARLY PUMP/DUMP ENGINE v3</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]

    # =====================================================
    # STRONG PUMP
    # =====================================================

    if strong_pumps:

        lines.append(
            "🔥 <b>STRONG EARLY PUMP</b>"
        )

        for item in strong_pumps[:3]:

            lines.append(
                f"🟢 <b>{item['symbol']}</b> "
                f"⭐ <b>{item['score']}/100</b>"
            )

            lines.append(
                f"5m {fmt_percent(item['c5'])} | "
                f"10m {fmt_percent(item['c10'])} | "
                f"15m {fmt_percent(item['c15'])}"
            )

            lines.append(
                f"RSI {fmt_rsi(item['rsi'])} | "
                f"Vol {fmt_volume(item['volume_ratio'])}"
            )

            lines.append(
                "💥 Breakout: "
                + (
                    "YES"
                    if item["breakout"]
                    else "NO"
                )
            )

            lines.append(
                "☁️ Ichimoku: "
                + (
                    "BULLISH"
                    if item["ichimoku"].get(
                        "bullish"
                    )
                    else "NO"
                )
            )

            if item["reasons"]:

                lines.append(
                    "📌 "
                    + ", ".join(
                        item["reasons"][:6]
                    )
                )

            lines.append("")

    # =====================================================
    # STRONG DUMP
    # =====================================================

    if strong_dumps:

        lines.append(
            "🔴 <b>STRONG EARLY DUMP</b>"
        )

        for item in strong_dumps[:3]:

            lines.append(
                f"🔴 <b>{item['symbol']}</b> "
                f"⭐ <b>{item['score']}/100</b>"
            )

            lines.append(
                f"5m {fmt_percent(item['c5'])} | "
                f"10m {fmt_percent(item['c10'])} | "
                f"15m {fmt_percent(item['c15'])}"
            )

            lines.append(
                f"RSI {fmt_rsi(item['rsi'])} | "
                f"Vol {fmt_volume(item['volume_ratio'])}"
            )

            lines.append(
                "💥 Breakdown: "
                + (
                    "YES"
                    if item["breakdown"]
                    else "NO"
                )
            )

            lines.append(
                "☁️ Ichimoku: "
                + (
                    "BEARISH"
                    if item["ichimoku"].get(
                        "bearish"
                    )
                    else "NO"
                )
            )

            if item["reasons"]:

                lines.append(
                    "📌 "
                    + ", ".join(
                        item["reasons"][:6]
                    )
                )

            lines.append("")

    if (
        not strong_pumps
        and not strong_dumps
    ):

        lines.append(
            "🟢 فعلاً سیگنال قوی نداریم."
        )

    # =====================================================
    # WATCHLIST
    # =====================================================

    lines += [
        "",
        "👀 <b>TOP 5 WATCHLIST</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]

    for index, item in enumerate(
        watchlist,
        1
    ):

        emoji = (
            "🟢"
            if item["direction"] == "PUMP"
            else "🔴"
        )

        lines.append(
            f"{index}. {emoji} "
            f"<b>{item['symbol']}</b> "
            f"⭐ {item['score']}/100"
        )

        lines.append(
            f"5m {fmt_percent(item['c5'])} | "
            f"10m {fmt_percent(item['c10'])} | "
            f"15m {fmt_percent(item['c15'])}"
        )

        lines.append(
            f"RSI {fmt_rsi(item['rsi'])} | "
            f"Vol {fmt_volume(item['volume_ratio'])}"
        )

        features = []

        if item["breakout"]:
            features.append(
                "Breakout"
            )

        if item["breakdown"]:
            features.append(
                "Breakdown"
            )

        if item["ichimoku"].get(
            "bullish"
        ):

            features.append(
                "Ichimoku 🟢"
            )

        elif item["ichimoku"].get(
            "bearish"
        ):

            features.append(
                "Ichimoku 🔴"
            )

        if features:

            lines.append(
                "📌 "
                + " | ".join(features)
            )

    # =====================================================
    # FOOTER
    # =====================================================

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━",
    ]

    if btc_1h is None:

        lines.append(
            "₿ BTC 1H: N/A"
        )

    else:

        btc_regime = "🟢" if btc_1h > 0 else "🔴"

        lines.append(
            f"₿ BTC 1H: "
            f"{btc_regime} "
            f"{btc_1h:+.2f}%"
        )

    total_samples = sum(
        len(v)
        for v in history.values()
    )

    lines.append(
        f"📊 Scanned: "
        f"{len(market_data)}/30"
    )

    lines.append(
        f"🧠 History: "
        f"{total_samples} snapshots"
    )

    lines.append(
        "📡 Source: CoinGecko"
    )

    now = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    lines.append(
        f"🕐 {now}"
    )

    message = "\n".join(lines)

    print(message)

    send_telegram(
        message
    )

    print(
        "Scanner finished."
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    scan()
