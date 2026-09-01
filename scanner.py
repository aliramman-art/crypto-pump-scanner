import os
import json
import time
import requests
from datetime import datetime, timezone

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

DATA_FILE = "price_data.json"

COINS = [
    "bitcoin", "ethereum", "binancecoin", "solana", "ripple",
    "dogecoin", "cardano", "avalanche-2", "chainlink", "polkadot",
    "tron", "litecoin", "bitcoin-cash", "cosmos", "uniswap",
    "ethereum-classic", "stellar", "near", "aptos", "filecoin",
    "arbitrum", "optimism", "sui", "injective-protocol", "aave",
    "maker", "algorand", "vechain", "sei-network", "celestia"
]

BASE_URL = "https://api.coingecko.com/api/v3"


def load_history():

    if not os.path.exists(DATA_FILE):
        return {}

    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)

    except Exception:
        return {}


def save_history(history):

    with open(DATA_FILE, "w") as f:
        json.dump(history, f)


def get_chart(coin):

    url = f"{BASE_URL}/coins/{coin}/market_chart"

    response = requests.get(
        url,
        params={
            "vs_currency": "usd",
            "days": "1"
        },
        timeout=20
    )

    print(
        coin,
        "HTTP:",
        response.status_code
    )

    if response.status_code == 429:
        raise Exception("RATE LIMIT")

    response.raise_for_status()

    return response.json()


def pct(a, b):

    if a <= 0:
        return 0

    return ((b - a) / a) * 100


def calculate_rsi(closes, period=14):

    if len(closes) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(closes)):

        change = closes[i] - closes[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0)

        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = sum(values[:period]) / period

    for price in values[period:]:

        result = (
            (price - result) * multiplier
        ) + result

    return result


def calculate_score(
    change5,
    change10,
    change15,
    volume_ratio,
    rsi,
    ema_fast,
    ema_slow,
    price,
    breakout
):

    score = 0

    # -------------------------
    # Price momentum 5m
    # -------------------------

    if change5 >= 2:
        score += 30

    elif change5 >= 1:
        score += 25

    elif change5 >= 0.6:
        score += 18

    elif change5 >= 0.3:
        score += 10

    # -------------------------
    # 10m momentum
    # -------------------------

    if change10 >= 3:
        score += 15

    elif change10 >= 1.5:
        score += 12

    elif change10 >= 0.8:
        score += 7

    # -------------------------
    # 15m momentum
    # -------------------------

    if change15 >= 4:
        score += 15

    elif change15 >= 2:
        score += 10

    elif change15 >= 1:
        score += 5

    # -------------------------
    # Volume
    # -------------------------

    if volume_ratio >= 3:
        score += 20

    elif volume_ratio >= 2:
        score += 15

    elif volume_ratio >= 1.5:
        score += 8

    # -------------------------
    # RSI
    # -------------------------

    if rsi is not None:

        if 60 <= rsi <= 72:
            score += 10

        elif 55 <= rsi < 60:
            score += 6

        elif 72 < rsi <= 78:
            score += 5

        elif rsi > 82:
            score -= 5

    # -------------------------
    # EMA trend
    # -------------------------

    if (
        ema_fast is not None
        and ema_slow is not None
        and price > ema_fast > ema_slow
    ):
        score += 5

    # -------------------------
    # Breakout
    # -------------------------

    if breakout:
        score += 10

    return max(
        0,
        min(score, 100)
    )


def send_message(text):

    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": text
        },
        timeout=20
    )

    print(
        "TELEGRAM:",
        response.text
    )

    response.raise_for_status()


def analyze_coin(coin):

    data = get_chart(coin)

    prices = [
        float(x[1])
        for x in data.get("prices", [])
    ]

    volumes = [
        float(x[1])
        for x in data.get("total_volumes", [])
    ]

    if len(prices) < 10:

        raise Exception(
            "Not enough price data"
        )

    price = prices[-1]

    # تعداد تقریبی نقاط 5 دقیقه‌ای
    # از آخرین نقاط موجود استفاده می‌کنیم

    p5 = prices[-2]
    p10 = prices[-3]
    p15 = prices[-4]

    change5 = pct(p5, price)
    change10 = pct(p10, price)
    change15 = pct(p15, price)

    # -------------------------
    # Volume acceleration
    # -------------------------

    volume_ratio = 1

    if len(volumes) >= 8:

        recent = volumes[-1]
        baseline = sum(
            volumes[-8:-1]
        ) / 7

        if baseline > 0:

            volume_ratio = (
                recent / baseline
            )

    # -------------------------
    # RSI
    # -------------------------

    rsi = calculate_rsi(
        prices[-30:]
    )

    # -------------------------
    # EMA
    # -------------------------

    ema_fast = ema(
        prices[-20:],
        5
    )

    ema_slow = ema(
        prices[-20:],
        10
    )

    # -------------------------
    # Breakout
    # -------------------------

    lookback = prices[-13:-3]

    breakout = False

    if lookback:

        previous_high = max(
            lookback
        )

        if price > previous_high:

            breakout = True

    score = calculate_score(
        change5,
        change10,
        change15,
        volume_ratio,
        rsi,
        ema_fast,
        ema_slow,
        price,
        breakout
    )

    return {
        "symbol": coin,
        "price": price,
        "change5": change5,
        "change10": change10,
        "change15": change15,
        "volume_ratio": volume_ratio,
        "rsi": rsi,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "breakout": breakout,
        "score": score
    }


def main():

    results = []

    failed = []

    for coin in COINS:

        try:

            result = analyze_coin(
                coin
            )

            results.append(
                result
            )

            print(
                coin,
                "SCORE:",
                result["score"],
                "5m:",
                round(
                    result["change5"],
                    2
                )
            )

        except Exception as e:

            print(
                coin,
                "ERROR:",
                e
            )

            failed.append(
                coin
            )

        # کمی فاصله برای Rate Limit
        time.sleep(1)

    if not results:

        raise Exception(
            "No market data received"
        )

    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    # --------------------------------
    # پیام اصلی
    # --------------------------------

    message = (
        "🚨 PUMP SCANNER 5M\n"
        "━━━━━━━━━━━━━━\n\n"
    )

    # --------------------------------
    # هشدارهای قوی
    # --------------------------------

    alerts = [
        x for x in results
        if x["score"] >= 70
    ]

    if alerts:

        message += "🔥 STRONG SIGNALS\n\n"

        for x in alerts[:5]:

            rsi = (
                f"{x['rsi']:.0f}"
                if x["rsi"] is not None
                else "N/A"
            )

            message += (
                f"🚨 {x['symbol'].upper()}\n"
                f"⭐ Score: {x['score']}/100\n"
                f"5m: {x['change5']:+.2f}%\n"
                f"10m: {x['change10']:+.2f}%\n"
                f"15m: {x['change15']:+.2f}%\n"
                f"Volume: {x['volume_ratio']:.1f}x\n"
                f"RSI: {rsi}\n"
                f"Breakout: "
                f"{'✅' if x['breakout'] else '❌'}\n\n"
            )

    else:

        message += (
            "⚪ فعلاً سیگنال قوی نداریم.\n\n"
        )

    # --------------------------------
    # TOP 5
    # --------------------------------

    message += "🏆 TOP 5\n\n"

    for i, x in enumerate(
        results[:5],
        1
    ):

        message += (
            f"{i}. "
            f"{x['symbol'].upper()} "
            f"⭐{x['score']}\n"
            f"5m {x['change5']:+.2f}% | "
            f"15m {x['change15']:+.2f}% | "
            f"RSI "
            f"{x['rsi']:.0f}"
            if x["rsi"] is not None
            else
            f"{i}. "
            f"{x['symbol'].upper()} "
            f"⭐{x['score']}\n"
            f"5m {x['change5']:+.2f}% | "
            f"15m {x['change15']:+.2f}% | "
            f"RSI N/A"
        )

        message += "\n"

    # --------------------------------
    # وضعیت سیستم
    # --------------------------------

    message += (
        "\n📡 Source: CoinGecko\n"
        f"✅ Successful: {len(results)}/30\n"
        f"❌ Failed: {len(failed)}/30\n"
        "⏱ Timeframe: ~5m\n"
    )

    # --------------------------------
    # ارسال
    # --------------------------------

    for start in range(
        0,
        len(message),
        3500
    ):

        send_message(
            message[
                start:start + 3500
            ]
        )


if __name__ == "__main__":
    main()
