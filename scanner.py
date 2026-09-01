import os
import json
import time
import requests
from datetime import datetime, timezone

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

URL = "https://api.coingecko.com/api/v3/coins/markets"

COINS = [
    "bitcoin", "ethereum", "binancecoin", "solana", "ripple",
    "dogecoin", "cardano", "avalanche-2", "chainlink", "polkadot",
    "tron", "litecoin", "bitcoin-cash", "cosmos", "uniswap",
    "ethereum-classic", "stellar", "near", "aptos", "filecoin",
    "arbitrum", "optimism", "sui", "injective-protocol", "aave",
    "maker", "algorand", "vechain", "sei-network", "celestia"
]

DATA_FILE = "price_data.json"


def get_market_data():

    response = requests.get(
        URL,
        params={
            "vs_currency": "usd",
            "ids": ",".join(COINS),
            "order": "market_cap_desc",
            "per_page": 30,
            "page": 1,
            "sparkline": "false"
        },
        timeout=30
    )

    print("CoinGecko:", response.status_code)

    response.raise_for_status()

    return response.json()


def load_previous():

    if not os.path.exists(DATA_FILE):
        return {}

    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_current(data):

    with open(DATA_FILE, "w") as f:
        json.dump(data, f)


def calculate_score(change_5m, change_15m, volume_ratio):

    score = 0

    # حرکت 5 دقیقه‌ای
    if change_5m >= 2:
        score += 30
    elif change_5m >= 1:
        score += 25
    elif change_5m >= 0.5:
        score += 15
    elif change_5m >= 0.2:
        score += 8

    # شتاب 15 دقیقه‌ای
    if change_15m >= 4:
        score += 25
    elif change_15m >= 2:
        score += 18
    elif change_15m >= 1:
        score += 10

    # حجم نسبت به ارزش بازار
    if volume_ratio >= 0.50:
        score += 25
    elif volume_ratio >= 0.25:
        score += 18
    elif volume_ratio >= 0.10:
        score += 10

    # شتاب مثبت
    if change_5m > 0 and change_15m > change_5m:
        score += 20

    return min(score, 100)


def send_message(text):

    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": text
        },
        timeout=15
    )

    print("Telegram:", response.text)

    response.raise_for_status()


def main():

    coins = get_market_data()

    previous = load_previous()

    current = {}

    results = []

    now = datetime.now(timezone.utc).isoformat()

    for coin in coins:

        coin_id = coin["id"]
        price = coin.get("current_price") or 0

        current[coin_id] = {
            "price": price,
            "time": now
        }

        old = previous.get(coin_id)

        # اجرای اول
        if not old:
            change_5m = 0
            change_15m = 0

        else:

            old_price = old.get("price", price)

            if old_price > 0:
                change_5m = (
                    (price - old_price)
                    / old_price
                ) * 100
            else:
                change_5m = 0

            # فعلاً تا اجرای بعدی از همان بازه استفاده می‌کنیم
            change_15m = change_5m

        volume = coin.get("total_volume") or 0
        market_cap = coin.get("market_cap") or 1

        volume_ratio = volume / market_cap

        score = calculate_score(
            change_5m,
            change_15m,
            volume_ratio
        )

        results.append({
            "symbol": coin["symbol"].upper(),
            "price": price,
            "change_5m": change_5m,
            "volume_ratio": volume_ratio,
            "score": score
        })

    # قیمت‌های جدید را ذخیره کن
    save_current(current)

    # بیشترین امتیازها اول
    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    message = (
        "🚨 PUMP SCANNER 5M\n"
        "━━━━━━━━━━━━━━\n"
        "📊 30 ارز مهم\n"
        "⏱ مقایسه با اجرای قبلی\n\n"
    )

    for i, x in enumerate(results, 1):

        message += (
            f"{i}. {x['symbol']} "
            f"⭐ {x['score']}/100\n"
            f"   5m: {x['change_5m']:+.2f}% | "
            f"Vol: {x['volume_ratio']:.2f}x\n"
        )

    # ارسال در قطعات کوچک
    for start in range(0, len(message), 3500):

        send_message(
            message[start:start + 3500]
        )


if __name__ == "__main__":
    main()
