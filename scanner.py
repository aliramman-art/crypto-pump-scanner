import os
import requests
import statistics

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

    change_5m = ((closes[-1] - closes[-2]) / closes[-2]) * 100
    change_15m = ((closes[-1] - closes[-4]) / closes[-4]) * 100

    avg_volume = statistics.mean(volumes[-21:-1])
    volume_ratio = volumes[-1] / avg_volume

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

    if closes[-1] > previous_high:
        score += 25

    return score, change_5m, change_15m, volume_ratio


def send_message(text):

    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": text
        },
        timeout=15
    )

    print("TELEGRAM RESPONSE:", response.text)

    response.raise_for_status()


def main():

    results = []

    for symbol in SYMBOLS:

        try:
            score, c5, c15, vr = analyze(symbol)

            results.append(
                (symbol, score, c5, c15, vr)
            )

            print(
                symbol,
                "score=", score,
                "5m=", round(c5, 2),
                "15m=", round(c15, 2)
            )

        except Exception as e:
            print(symbol, "ERROR:", e)

    results.sort(
        key=lambda x: x[1],
        reverse=True
    )

    message = "📊 پامپ اسکنر | گزارش 5 دقیقه‌ای\n\n"

    for i, (symbol, score, c5, c15, vr) in enumerate(results, 1):

        coin = symbol.replace("USDT", "")

        message += (
            f"{i}. {coin} ⭐{score}/100 | "
            f"5m {c5:+.2f}% | "
            f"15m {c15:+.2f}% | "
            f"Vol {vr:.1f}x\n"
        )

    send_message(message)


if __name__ == "__main__":
    main()
