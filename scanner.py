import os
import requests

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


def get_data():

    params = {
        "vs_currency": "usd",
        "ids": ",".join(COINS),
        "order": "market_cap_desc",
        "per_page": 30,
        "page": 1,
        "sparkline": "false"
    }

    response = requests.get(
        URL,
        params=params,
        timeout=20
    )

    print("COINGECKO:", response.status_code)

    response.raise_for_status()

    return response.json()


def calculate_score(coin):

    change = coin.get("price_change_percentage_24h") or 0
    volume = coin.get("total_volume") or 0
    market_cap = coin.get("market_cap") or 1

    volume_ratio = volume / market_cap

    score = 0

    if change >= 10:
        score += 50
    elif change >= 5:
        score += 40
    elif change >= 2:
        score += 25
    elif change >= 1:
        score += 15

    if volume_ratio >= 0.5:
        score += 30
    elif volume_ratio >= 0.25:
        score += 20
    elif volume_ratio >= 0.10:
        score += 10

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

    print("TELEGRAM:", response.text)

    response.raise_for_status()


def main():

    coins = get_data()

    coins.sort(
        key=calculate_score,
        reverse=True
    )

    message = "📊 پامپ اسکنر\n"
    message += "⏱ گزارش دوره‌ای\n"
    message += "🔎 ۳۰ ارز مهم\n\n"

    for i, coin in enumerate(coins, 1):

        name = coin["symbol"].upper()

        price = coin.get("current_price") or 0

        change = coin.get(
            "price_change_percentage_24h"
        ) or 0

        score = calculate_score(coin)

        message += (
            f"{i}. {name} ⭐ {score}/100\n"
            f"💰 ${price:.6f}\n"
            f"📈 24h: {change:+.2f}%\n\n"
        )

    # تقسیم پیام برای جلوگیری از محدودیت تلگرام
    for start in range(0, len(message), 3500):

        send_message(
            message[start:start + 3500]
        )


if __name__ == "__main__":
    main()
