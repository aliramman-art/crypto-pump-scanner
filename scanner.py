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

# 8 hours history
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

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

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
            timeout=20
        )

        response.raise_for_status()

        result = response.json()

        if result.get("ok"):
            print("Telegram message sent successfully.")
            return True

        print(f"Telegram rejected message: {result}")
        return False

    except Exception as e:
        print(f"Telegram error: {e}")
        return False


# =========================================================
# HISTORY
# =========================================================

def load_history():

    if not os.path.exists(HISTORY_FILE):
        print("No previous history found.")
        return {}

    try:

        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        if isinstance(data, dict):
            return data

    except Exception as e:
        print(f"History read error: {e}")

    return {}


def save_history(history):

    try:

        temp_file = HISTORY_FILE + ".tmp"

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                history,
                file,
                ensure_ascii=False,
                separators=(",", ":")
            )

        os.replace(
            temp_file,
            HISTORY_FILE
        )

        print("History saved.")

    except Exception as e:
        print(f"History save error: {e}")


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

        # Keep enough snapshots for Ichimoku
        if len(history[coin_id]) > 150:
            history[coin_id] = history[coin_id][-150:]


# =========================================================
# TIME CHANGE
# =========================================================

def get_change(items, minutes):

    if not items or len(items) < 2:
        return None

    latest = items[-1]

    latest_time = int(latest["time"])

    target_time = latest_time - minutes * 60

    # We allow GitHub Actions schedule delays.
    max_distance = max(
        300,
        int(minutes * 60 * 0.80)
    )

    candidates = [
        item
        for item in items[:-1]
        if int(item.get("time", 0)) <= target_time
    ]

    if not candidates:
        return None

    previous = min(
        candidates,
        key=lambda x: abs(
            int(x["time"]) - target_time
        )
    )

    previous_time = int(previous["time"])

    if abs(previous_time - target_time) > max_distance:
        return None

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

    if len(items) < period + 1:
        return None

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

    average_gain = sum(gains) / period

    average_loss = sum(losses) / period

    if average_loss == 0:

        if average_gain == 0:
            return 50.0

        return 100.0

    rs = average_gain / average_loss

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

    ema = sum(
        prices[:period]
    ) / period

    for price in prices[period:]:

        ema = (
            (price - ema)
            * multiplier
        ) + ema

    return ema


# =========================================================
# VOLUME TREND
# =========================================================

def calculate_volume_trend(items):

    if len(items) < 4:
        return None

    latest_volume = float(
        items[-1].get("volume", 0)
    )

    old_volume = float(
        items[-4].get("volume", 0)
    )

    if old_volume <= 0:
        return None

    return (
        (latest_volume - old_volume)
        / old_volume
    ) * 100


# =========================================================
# BREAKOUT PROXY
# =========================================================

def calculate_breakout(items):

    if len(items) < 7:
        return {
            "breakout": False,
            "distance": None,
        }

    latest_price = float(
        items[-1]["price"]
    )

    # Previous ~30 minutes
    previous = items[-7:-1]

    prices = [
        float(x["price"])
        for x in previous
    ]

    if not prices:
        return {
            "breakout": False,
            "distance": None,
        }

    previous_high = max(prices)

    distance = (
        (latest_price - previous_high)
        / previous_high
    ) * 100

    breakout = latest_price > (
        previous_high * 1.0005
    )

    return {
        "breakout": breakout,
        "distance": distance,
    }


# =========================================================
# ICHIMOKU PROXY
# =========================================================

def calculate_ichimoku(items):

    # Standard periods:
    # Tenkan = 9
    # Kijun = 26
    # Senkou B = 52

    if len(items) < 52:
        return {
            "ready": False,
            "bullish": False,
            "strong": False,
            "distance_kijun": None,
        }

    prices = [
        float(x["price"])
        for x in items
    ]

    latest_price = prices[-1]

    tenkan_window = prices[-9:]

    kijun_window = prices[-26:]

    span_b_window = prices[-52:]

    tenkan = (
        max(tenkan_window)
        + min(tenkan_window)
    ) / 2

    kijun = (
        max(kijun_window)
        + min(kijun_window)
    ) / 2

    span_a = (
        tenkan + kijun
    ) / 2

    span_b = (
        max(span_b_window)
        + min(span_b_window)
    ) / 2

    cloud_top = max(
        span_a,
        span_b
    )

    cloud_bottom = min(
        span_a,
        span_b
    )

    distance_kijun = (
        (latest_price - kijun)
        / kijun
    ) * 100 if kijun else None

    bullish = (
        latest_price > cloud_top
        and tenkan > kijun
    )

    strong = (
        bullish
        and span_a > span_b
    )

    return {
        "ready": True,
        "bullish": bullish,
        "strong": strong,
        "tenkan": tenkan,
        "kijun": kijun,
        "span_a": span_a,
        "span_b": span_b,
        "cloud_top": cloud_top,
        "cloud_bottom": cloud_bottom,
        "distance_kijun": distance_kijun,
    }


# =========================================================
# SCORE
# =========================================================

def calculate_score(
    c5,
    c10,
    c15,
    rsi,
    ema5,
    ema10,
    volume_trend,
    breakout,
    ichimoku,
    btc_change,
):

    score = 0

    reasons = []

    # -----------------------------------------------------
    # 5M MOMENTUM = 15
    # -----------------------------------------------------

    if c5 is not None:

        if c5 >= 2:
            score += 15
            reasons.append("5m momentum 🔥")

        elif c5 >= 1:
            score += 11
            reasons.append("5m momentum")

        elif c5 >= 0.5:
            score += 7

        elif c5 > 0:
            score += 3

    # -----------------------------------------------------
    # 10M = 10
    # -----------------------------------------------------

    if c10 is not None:

        if c10 >= 3:
            score += 10
            reasons.append("10m acceleration 🔥")

        elif c10 >= 1.5:
            score += 7
            reasons.append("10m acceleration")

        elif c10 > 0:
            score += 3

    # -----------------------------------------------------
    # 15M = 10
    # -----------------------------------------------------

    if c15 is not None:

        if c15 >= 4:
            score += 10
            reasons.append("15m trend 🔥")

        elif c15 >= 2:
            score += 7
            reasons.append("15m trend")

        elif c15 > 0:
            score += 3

    # -----------------------------------------------------
    # ACCELERATION = 10
    # -----------------------------------------------------

    if (
        c5 is not None
        and c10 is not None
        and c5 > 0
        and c10 > 0
    ):

        expected_5m = c10 / 2

        if c5 > expected_5m:

            score += 10

            reasons.append(
                "Acceleration ⚡"
            )

        elif c5 > expected_5m * 0.75:

            score += 5

    # -----------------------------------------------------
    # RSI = 10
    # -----------------------------------------------------

    if rsi is not None:

        if 52 <= rsi <= 68:

            score += 10

            reasons.append(
                "RSI healthy"
            )

        elif 68 < rsi <= 76:

            score += 7

            reasons.append(
                "RSI strong"
            )

        elif 76 < rsi <= 82:

            score += 3

        elif rsi > 82:

            score -= 5

            reasons.append(
                "RSI overheated"
            )

    # -----------------------------------------------------
    # EMA = 10
    # -----------------------------------------------------

    if (
        ema5 is not None
        and ema10 is not None
    ):

        if ema5 > ema10:

            score += 10

            reasons.append(
                "EMA bullish"
            )

    # -----------------------------------------------------
    # BREAKOUT = 15
    # -----------------------------------------------------

    if breakout:

        score += 15

        reasons.append(
            "BREAKOUT 💥"
        )

    # -----------------------------------------------------
    # ICHIMOKU = 15
    # -----------------------------------------------------

    if ichimoku.get("ready"):

        if ichimoku.get("strong"):

            score += 15

            reasons.append(
                "Ichimoku bullish ☁️"
            )

        elif ichimoku.get("bullish"):

            score += 10

            reasons.append(
                "Ichimoku bullish"
            )

        elif (
            ichimoku.get("tenkan")
            and ichimoku.get("kijun")
            and ichimoku["tenkan"]
            > ichimoku["kijun"]
        ):

            score += 5

    # -----------------------------------------------------
    # BTC FILTER = 5
    # -----------------------------------------------------

    if btc_change is not None:

        if btc_change > 0:

            score += 5

            reasons.append(
                "BTC supportive"
            )

        elif btc_change < -1:

            score -= 5

            reasons.append(
                "BTC bearish"
            )

    # -----------------------------------------------------
    # VOLUME = 10
    # -----------------------------------------------------

    if volume_trend is not None:

        if volume_trend >= 20:

            score += 10

            reasons.append(
                "Volume spike"
            )

        elif volume_trend >= 10:

            score += 7

            reasons.append(
                "Volume rising"
            )

        elif volume_trend >= 3:

            score += 3

    score = max(
        0,
        min(100, score)
    )

    return score, reasons


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
            "crypto-pump-scanner/2.0"
    }

    try:

        response = requests.get(
            COINGECKO_URL,
            params=params,
            headers=headers,
            timeout=30,
        )

        print(
            f"CoinGecko HTTP: "
            f"{response.status_code}"
        )

        response.raise_for_status()

        data = response.json()

        if not isinstance(data, list):

            print(
                "CoinGecko returned invalid data."
            )

            return []

        return data

    except Exception as e:

        print(
            f"CoinGecko request failed: {e}"
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


# =========================================================
# SCANNER
# =========================================================

def scan():

    print("=" * 60)

    print(
        "EARLY PUMP ENGINE v2 START"
    )

    print("=" * 60)

    market_data = get_market_data()

    if not market_data:

        send_telegram(
            "🔴 <b>PUMP SCANNER ERROR</b>\n\n"
            "CoinGecko data unavailable.\n"
            "Scanner stopped safely."
        )

        return False

    print(
        f"Market data received: "
        f"{len(market_data)} coins"
    )

    history = load_history()

    update_history(
        history,
        market_data
    )

    save_history(history)

    results = []

    btc_history = history.get(
        "bitcoin",
        []
    )

    btc_1h = get_change(
        btc_history,
        60
    )

    # =====================================================
    # ANALYZE COINS
    # =====================================================

    for coin in market_data:

        coin_id = coin.get("id")

        symbol = coin.get(
            "symbol",
            ""
        ).upper()

        name = coin.get(
            "name",
            symbol
        )

        if not coin_id:
            continue

        items = history.get(
            coin_id,
            []
        )

        if not items:
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
            items,
            14
        )

        ema5 = calculate_ema(
            items,
            5
        )

        ema10 = calculate_ema(
            items,
            10
        )

        volume_trend = calculate_volume_trend(
            items
        )

        breakout_data = calculate_breakout(
            items
        )

        breakout = breakout_data[
            "breakout"
        ]

        ichimoku = calculate_ichimoku(
            items
        )

        score, reasons = calculate_score(
            c5,
            c10,
            c15,
            rsi,
            ema5,
            ema10,
            volume_trend,
            breakout,
            ichimoku,
            btc_1h,
        )

        results.append(
            {
                "id": coin_id,
                "symbol": symbol,
                "name": name,
                "score": score,
                "c5": c5,
                "c10": c10,
                "c15": c15,
                "rsi": rsi,
                "volume_trend": volume_trend,
                "breakout": breakout,
                "ichimoku": ichimoku,
                "samples": len(items),
                "reasons": reasons,
            }
        )

    # Highest score first
    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    # =====================================================
    # STRONG SIGNAL
    # =====================================================

    strong = []

    for item in results:

        if item["score"] < 75:
            continue

        if (
            item["c5"] is None
            or item["c10"] is None
            or item["c15"] is None
        ):
            continue

        if (
            item["c5"] <= 0
            or item["c10"] <= 0
            or item["c15"] <= 0
        ):
            continue

        # Avoid chasing a candle that already exploded
        if item["c5"] >= 5:
            continue

        # Need either breakout or acceleration
        has_acceleration = False

        if (
            item["c5"] is not None
            and item["c10"] is not None
        ):

            if (
                item["c5"] > 0
                and item["c10"] > 0
                and item["c5"]
                > item["c10"] / 2
            ):

                has_acceleration = True

        if (
            not item["breakout"]
            and not has_acceleration
        ):
            continue

        strong.append(item)

    # =====================================================
    # TOP 5 ALWAYS
    # =====================================================

    watchlist = results[:5]

    # =====================================================
    # TELEGRAM MESSAGE
    # =====================================================

    lines = [
        "🚨 <b>EARLY PUMP ENGINE v2</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]

    # -----------------------------------------------------
    # STRONG
    # -----------------------------------------------------

    if strong:

        lines += [
            "🔥 <b>STRONG EARLY PUMP</b>",
            "",
        ]

        for index, item in enumerate(
            strong[:3],
            1
        ):

            lines.append(
                f"{index}. "
                f"<b>{item['symbol']}</b> "
                f"⭐ <b>{item['score']}/100</b>"
            )

            lines.append(
                f"5m {fmt_percent(item['c5'])} | "
                f"10m {fmt_percent(item['c10'])} | "
                f"15m {fmt_percent(item['c15'])}"
            )

            lines.append(
                f"RSI {fmt_rsi(item['rsi'])} | "
                f"Samples {item['samples']}"
            )

            features = []

            if item["breakout"]:
                features.append(
                    "💥 Breakout"
                )

            if item["ichimoku"].get("bullish"):
                features.append(
                    "☁️ Ichimoku"
                )

            if features:
                lines.append(
                    " | ".join(features)
                )

            if item["reasons"]:
                lines.append(
                    "📌 "
                    + ", ".join(
                        item["reasons"][:5]
                    )
                )

            lines.append("")

    else:

        lines += [
            "🟢 <b>فعلاً Strong Early Pump نداریم</b>",
            "",
        ]

    # -----------------------------------------------------
    # TOP 5
    # -----------------------------------------------------

    lines += [
        "👀 <b>TOP 5 WATCHLIST</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]

    for index, item in enumerate(
        watchlist,
        1
    ):

        readiness = ""

        if item["samples"] < 15:
            readiness = " 🕐"

        elif item["samples"] < 52:
            readiness = " 📊"

        elif not item["ichimoku"].get(
            "ready"
        ):
            readiness = " ☁️"

        lines.append(
            f"{index}. "
            f"<b>{item['symbol']}</b> "
            f"⭐ {item['score']}/100"
            f"{readiness}"
        )

        lines.append(
            f"5m {fmt_percent(item['c5'])} | "
            f"10m {fmt_percent(item['c10'])} | "
            f"15m {fmt_percent(item['c15'])}"
        )

        lines.append(
            f"RSI {fmt_rsi(item['rsi'])} | "
            f"Samples {item['samples']}"
        )

        if item["reasons"]:

            lines.append(
                "📌 "
                + ", ".join(
                    item["reasons"][:3]
                )
            )

    # -----------------------------------------------------
    # FOOTER
    # -----------------------------------------------------

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━",
    ]

    if btc_1h is None:

        lines.append(
            "₿ BTC 1H: N/A"
        )

    else:

        lines.append(
            f"₿ BTC 1H: "
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
        "☁️ Ichimoku: "
        "5m price proxy"
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
        "EARLY PUMP ENGINE v2 FINISHED."
    )

    return True


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":
    scan()
