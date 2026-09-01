import os
import json
import time
from datetime import datetime, timezone

import requests


# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"

HISTORY_FILE = "price_data.json"

# نگهداری 3 ساعت تاریخچه
HISTORY_SECONDS = 3 * 60 * 60

# حداکثر 30 ارز
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


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secrets are missing.")
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
        response = requests.post(
            url,
            json=payload,
            timeout=20,
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


# ============================================================
# HISTORY
# ============================================================

def load_history():
    if not os.path.exists(HISTORY_FILE):
        print("No previous history found. Starting new history.")
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
        print(f"History read error: {e}")

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

        os.replace(temp_file, HISTORY_FILE)

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

        if not coin_id:
            continue

        if price is None:
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

        # حذف داده‌های قدیمی
        history[coin_id] = [
            item
            for item in history[coin_id]
            if int(item.get("time", 0)) >= cutoff
        ]

        # جلوگیری از رکوردهای خیلی زیاد
        if len(history[coin_id]) > 50:
            history[coin_id] = history[coin_id][-50:]


# ============================================================
# PRICE CHANGE
# ============================================================

def get_change(items, minutes):
    if not items:
        return None

    if len(items) < 2:
        return None

    latest = items[-1]

    latest_time = int(latest["time"])

    target_time = latest_time - (minutes * 60)

    # نزدیک‌ترین snapshot به زمان هدف
    previous = min(
        items,
        key=lambda x: abs(
            int(x["time"]) - target_time
        ),
    )

    previous_time = int(previous["time"])

    # اگر داده خیلی دور باشد معتبر نیست
    max_distance = max(120, minutes * 60 * 0.40)

    if abs(previous_time - target_time) > max_distance:
        return None

    previous_price = float(previous["price"])

    latest_price = float(latest["price"])

    if previous_price <= 0:
        return None

    return (
        (latest_price - previous_price)
        / previous_price
    ) * 100


# ============================================================
# RSI
# ============================================================

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

    changes = []

    for i in range(1, len(prices)):
        changes.append(
            prices[i] - prices[i - 1]
        )

    recent = changes[-period:]

    gains = []
    losses = []

    for change in recent:
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

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


# ============================================================
# EMA
# ============================================================

def calculate_ema(items, period):
    prices = [
        float(x["price"])
        for x in items
        if x.get("price") is not None
    ]

    if len(prices) < period:
        return None

    multiplier = 2 / (period + 1)

    ema = sum(prices[:period]) / period

    for price in prices[period:]:
        ema = (
            (price - ema)
            * multiplier
        ) + ema

    return ema


# ============================================================
# VOLUME TREND
# ============================================================

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


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    c5,
    c10,
    c15,
    rsi,
    ema5,
    ema10,
    volume_trend,
):
    score = 0
    reasons = []

    # --------------------------------------------------------
    # 5 MIN MOMENTUM
    # --------------------------------------------------------

    if c5 is not None:

        if c5 >= 2.0:
            score += 18
            reasons.append("5m momentum 🔥")

        elif c5 >= 1.0:
            score += 13
            reasons.append("5m momentum")

        elif c5 >= 0.5:
            score += 7
            reasons.append("5m positive")

    # --------------------------------------------------------
    # 10 MIN MOMENTUM
    # --------------------------------------------------------

    if c10 is not None:

        if c10 >= 3.0:
            score += 15
            reasons.append("10m acceleration 🔥")

        elif c10 >= 1.5:
            score += 10
            reasons.append("10m acceleration")

        elif c10 > 0:
            score += 4

    # --------------------------------------------------------
    # 15 MIN TREND
    # --------------------------------------------------------

    if c15 is not None:

        if c15 >= 4.0:
            score += 15
            reasons.append("15m trend 🔥")

        elif c15 >= 2.0:
            score += 10
            reasons.append("15m trend")

        elif c15 > 0:
            score += 5

    # --------------------------------------------------------
    # ACCELERATION
    # --------------------------------------------------------

    if c5 is not None and c10 is not None:

        expected_5m = c10 / 2

        if c5 > 0 and c10 > 0:

            if c5 > expected_5m:
                score += 10
                reasons.append("Acceleration")

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    if rsi is not None:

        if 52 <= rsi <= 68:
            score += 10
            reasons.append("RSI healthy")

        elif 68 < rsi <= 76:
            score += 7
            reasons.append("RSI strong")

        elif 76 < rsi <= 82:
            score += 3

        elif rsi > 82:
            score -= 5
            reasons.append("RSI overheated")

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

    if ema5 is not None and ema10 is not None:

        if ema5 > ema10:
            score += 10
            reasons.append("EMA bullish")

    # --------------------------------------------------------
    # MARKET VOLUME TREND
    # --------------------------------------------------------

    if volume_trend is not None:

        if volume_trend >= 10:
            score += 7
            reasons.append("Volume rising")

        elif volume_trend >= 3:
            score += 3

    # --------------------------------------------------------
    # LIMIT SCORE
    # --------------------------------------------------------

    score = max(0, min(100, score))

    return score, reasons


# ============================================================
# COINGECKO
# ============================================================

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
        "User-Agent": "crypto-pump-scanner/1.0"
    }

    try:

        response = requests.get(
            COINGECKO_URL,
            params=params,
            headers=headers,
            timeout=30,
        )

        print(
            f"CoinGecko HTTP: {response.status_code}"
        )

        response.raise_for_status()

        data = response.json()

        if not isinstance(data, list):
            print("CoinGecko returned invalid data.")
            return []

        return data

    except Exception as e:

        print(
            f"CoinGecko request failed: {e}"
        )

        return []


# ============================================================
# FORMAT
# ============================================================

def fmt_percent(value):
    if value is None:
        return "N/A"

    return f"{value:+.2f}%"


def fmt_rsi(value):
    if value is None:
        return "N/A"

    return f"{value:.1f}"


# ============================================================
# MAIN SCAN
# ============================================================

def scan():

    print("=" * 60)
    print("EARLY PUMP SCANNER START")
    print("=" * 60)

    market_data = get_market_data()

    if not market_data:

        error_message = (
            "🔴 <b>PUMP SCANNER ERROR</b>\n\n"
            "CoinGecko data unavailable.\n"
            "Scanner stopped safely."
        )

        send_telegram(error_message)

        return False

    print(
        f"Market data received: "
        f"{len(market_data)} coins"
    )

    history = load_history()

    update_history(
        history,
        market_data,
    )

    save_history(history)

    results = []

    # --------------------------------------------------------
    # BTC 1H FILTER
    # --------------------------------------------------------

    btc_history = history.get(
        "bitcoin",
        [],
    )

    btc_1h = get_change(
        btc_history,
        60,
    )

    # --------------------------------------------------------
    # SCAN COINS
    # --------------------------------------------------------

    for coin in market_data:

        coin_id = coin.get("id")

        symbol = (
            coin.get("symbol", "")
            .upper()
        )

        name = coin.get(
            "name",
            symbol,
        )

        if not coin_id:
            continue

        items = history.get(
            coin_id,
            [],
        )

        if not items:
            continue

        c5 = get_change(
            items,
            5,
        )

        c10 = get_change(
            items,
            10,
        )

        c15 = get_change(
            items,
            15,
        )

        rsi = calculate_rsi(
            items,
            14,
        )

        ema5 = calculate_ema(
            items,
            5,
        )

        ema10 = calculate_ema(
            items,
            10,
        )

        volume_trend = calculate_volume_trend(
            items
        )

        score, reasons = calculate_score(
            c5=c5,
            c10=c10,
            c15=c15,
            rsi=rsi,
            ema5=ema5,
            ema10=ema10,
            volume_trend=volume_trend,
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
                "reasons": reasons,
            }
        )

    # --------------------------------------------------------
    # SORT
    # --------------------------------------------------------

    results.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    # --------------------------------------------------------
    # STRONG SIGNAL
    # --------------------------------------------------------

    strong = []

    for item in results:

        if item["score"] < 75:
            continue

        if item["c5"] is None:
            continue

        if item["c10"] is None:
            continue

        if item["c15"] is None:
            continue

        if item["c5"] <= 0:
            continue

        if item["c10"] <= 0:
            continue

        # جلوگیری از هشدارهای خیلی دیر
        if item["c5"] >= 5:
            continue

        strong.append(item)

    # --------------------------------------------------------
    # EARLY WATCH
    # --------------------------------------------------------

    watchlist = [
        item
        for item in results
        if item["score"] >= 45
    ][:5]

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    lines = []

    lines.append(
        "🚨 <b>EARLY PUMP SCANNER 5M</b>"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    if strong:

        lines.append(
            "🔥 <b>STRONG EARLY PUMP</b>"
        )

        lines.append("")

        for index, item in enumerate(
            strong[:3],
            1,
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
                f"RSI {fmt_rsi(item['rsi'])}"
            )

            if item["reasons"]:

                reasons_text = ", ".join(
                    item["reasons"][:4]
                )

                lines.append(
                    f"📌 {reasons_text}"
                )

            lines.append("")

    else:

        lines.append(
            "🟢 <b>فعلاً Strong Early Pump نداریم</b>"
        )

        lines.append("")

    # --------------------------------------------------------
    # WATCHLIST
    # --------------------------------------------------------

    lines.append(
        "👀 <b>TOP 5 WATCHLIST</b>"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    if watchlist:

        for index, item in enumerate(
            watchlist,
            1,
        ):

            lines.append(
                f"{index}. "
                f"<b>{item['symbol']}</b> "
                f"⭐ {item['score']}/100"
            )

            lines.append(
                f"5m {fmt_percent(item['c5'])} | "
                f"15m {fmt_percent(item['c15'])} | "
                f"RSI {fmt_rsi(item['rsi'])}"
            )

    else:

        lines.append(
            "هنوز داده کافی برای Watchlist نداریم."
        )

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    lines.append("")
    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    if btc_1h is None:

        lines.append(
            "₿ BTC 1H: N/A"
        )

    else:

        lines.append(
            f"₿ BTC 1H: {btc_1h:+.2f}%"
        )

    lines.append(
        f"📊 Scanned: "
        f"{len(market_data)}/30"
    )

    lines.append(
        f"🧠 History: "
        f"{sum(len(v) for v in history.values())} snapshots"
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

    print("")
    print(message)
    print("")

    send_telegram(message)

    print(
        "EARLY PUMP SCANNER FINISHED."
    )

    return True


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    scan()
