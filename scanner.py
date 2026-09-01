import os
import requests
import statistics

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

BASE_URL = "https://api.bybit.com"

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
        f"{BASE_URL}/v5/market/kline",
        params={
            "category": "linear",
            "symbol": symbol,
            "interval": "5",
            "limit": 30
        },
        timeout=15
    )

    response.raise_for_status()

    data = response.json()

    if data["retCode"] != 0:
        raise Exception(data["retMsg"])

    # Bybit داده‌ها را جدید به قدیم می‌دهد
    return list(reversed(data["result"]["list"]))


def analyze(symbol):

    candles = get_klines(symbol)

    closes = [float(c[4]) for c in candles]
    volumes = [float(c[5]) for c in candles]

    change_5m = (
        (closes[-1] - closes[-2])
        / closes[-2]
    ) * 100

    change_15m = (
        (closes[-1] - closes[-4])
        / closes[-4]
    ) * 100

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

    print("TELEGRAM:", response.text)

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
                "Score:", score,
                "5m:", round(c5, 2),
                "15m:", round(c15, 2),
                "Volume:", round(vr, 2)
            )

        except Exception as e:

            print(symbol, "ERROR:", e)

    results.sort(
        key=lambda x: x[1],
        reverse=True
    )

    message = "📊 پامپ اسکنر | گزارش 5 دقیقه‌ای\n\n"

    for i, (symbol, score, c5, c15, vr) in enumerate(
        results, 1
    ):

        coin = symbol.replace("USDT", "")

        message += (
            f"{i}. {coin} ⭐ {score}/100\n"
            f"5m: {c5:+.2f}% | "
            f"15m: {c15:+.2f}% | "
            f"Vol: {vr:.1f}x\n\n"
        )

    send_message(message)


if __name__ == "__main__":
    main()
