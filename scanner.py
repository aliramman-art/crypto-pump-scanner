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


def get_klines(symbol, interval="5m", limit=30):
    response = requests.get(
        f"{BASE_URL}/api/v3/klines",
        params={
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        },
        timeout=10
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
    volume_ratio = volumes[-1] / avg_volume if avg_volume else 0

    score = 0
    reasons = []

    if change_5m >= 1:
        score += 25
        reasons.append("حرکت شدید 5m")
    elif change_5m >= 0.5:
        score += 15
        reasons.append("رشد قیمت")

    if change_15m >= 2:
        score += 20
        reasons.append("شتاب 15m")
    elif change_15m >= 1:
        score += 10
        reasons.append("روند مثبت")

    if volume_ratio >= 3:
        score += 30
        reasons.append("حجم بسیار بالا")
    elif volume_ratio >= 2:
        score += 20
        reasons.append("حجم بالا")
    elif volume_ratio >= 1.5:
        score += 10
        reasons.append("افزایش حجم")

    previous_high = max(closes[-21:-1])

    if price > previous_high:
        score += 25
        reasons.append("شکست سقف")

    return {
        "symbol": symbol,
        "price": price,
        "change_5m": change_5m,
        "change_15m": change_15m,
        "volume_ratio": volume_ratio,
        "score": score,
        "reasons": reasons
    }


def send_message(text):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": text
        },
        timeout=10
    )


def main():

    results = []

    for symbol in SYMBOLS:
        try:
            result = analyze(symbol)
            results.append(result)
            print(symbol, result["score"])

        except Exception as e:
            print(symbol, "ERROR:", e)

        time.sleep(0.2)

    results.sort(key=lambda x: x["score"], reverse=True)

    message = "📊 گزارش ۵ دقیقه‌ای پامپ اسکنر\n"
    message += "━━━━━━━━━━━━━━\n"

    for i, x in enumerate(results, 1):

        reasons = ", ".join(x["reasons"]) if x["reasons"] else "بدون سیگنال"

        message += (
            f"{i}. {x['symbol'].replace('USDT', '')} "
            f"⭐ {x['score']}/100\n"
            f"   5m: {x['change_5m']:+.2f}% | "
            f"15m: {x['change_15m']:+.2f}%\n"
            f"   حجم: {x['volume_ratio']:.1f}x\n"
            f"   {reasons}\n\n"
        )

    send_message(message)


if __name__ == "__main__":
    main()
