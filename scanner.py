import os
import requests
import statistics
import time

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

BASE_URL = "https://api.binance.com"

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
    "TRXUSDT", "LTCUSDT", "BCHUSDT", "ATOMUSDT", "UNIUSDT",
    "ETCUSDT", "XLMUSDT", "NEARUSDT", "APTUSDT", "FILUSDT",
    "ARBUSDT", "OPUSDT", "SUIUSDT", "INJUSDT", "AAVEUSDT",
    "MKRUSDT", "ALGOUSDT", "VETUSDT", "SEIUSDT", "TIAUSDT"
]


def get_klines(symbol):
    response = requests.get(
        f"{BASE_URL}/api/v3/klines",
        params={
            "symbol": symbol,
            "interval": "5m",
            "limit": 30
        },
        timeout=15
    )

    response.raise_for_status()
    return response.json()


def analyze(symbol):

    candles = get_klines(symbol)

    closes = [float(c[4]) for c in candles]
    volumes = [float(c[5]) for c in candles]

    price = closes[-1]

    change_5m = ((closes[-1] - closes[-2]) / closes[-2]) * 100
    change_15m = ((closes[-1] - closes[-4]) / closes[-4]) * 100

    avg_volume = statistics.mean(volumes[-21:-1])

    volume_ratio = (
        volumes[-1] / avg_volume
        if avg_volume > 0 else 0
    )

    score = 0

    if change_5m >= 1:
        score += 25
    elif change_5m >= 0.5:
        score += 15

    if change_15m >= 2:
        score += 20
    elif change_15m >= 1:
        score += 10

    if volume_ratio >= 3:
        score += 30
    elif volume_ratio >= 2:
        score += 20
    elif volume_ratio >= 1.5:
        score += 10

    previous_high = max(closes[-21:-1])

    if price > previous_high:
        score += 25

    return {
        "symbol": symbol,
        "price": price,
        "change_5m": change_5m,
        "change_15m": change_15m,
        "volume_ratio": volume_ratio,
        "score": score
    }


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

    results = []

    for symbol in SYMBOLS:

        try:

            result = analyze(symbol)
            results.append(result)

            print(
                symbol,
                "Score:",
                result["score"]
            )

        except Exception as e:

            print(
                symbol,
                "ERROR:",
                e
            )

        time.sleep(0.2)

    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    # پیام اول
    send_message(
        "📊 گزارش پامپ اسکنر\n"
        "⏱ تایم‌فریم: 5 دقیقه\n"
        "🔎 تعداد ارز: 30"
    )

    # هر 10 ارز یک پیام
    for start in range(0, len(results), 10):

        group = results[start:start + 10]

        message = ""

        for i, x in enumerate(
            group,
            start=start + 1
        ):

            message += (
                f"{i}. {x['symbol'].replace('USDT', '')} "
                f"⭐ {x['score']}/100\n"
                f"   5m: {x['change_5m']:+.2f}% | "
                f"15m: {x['change_15m']:+.2f}%\n"
                f"   Volume: {x['volume_ratio']:.1f}x\n\n"
            )

        send_message(message)


if __name__ == "__main__":
    main()
