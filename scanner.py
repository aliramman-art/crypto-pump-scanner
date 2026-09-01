import os
import json
import requests
from datetime import datetime, timezone

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

DATA_FILE = "price_data.json"

API_URL = "https://api.coingecko.com/api/v3/coins/markets"

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
    "celestia"
]


# --------------------------------------------------
# زمان فعلی
# --------------------------------------------------

def now():
    return int(datetime.now(timezone.utc).timestamp())


# --------------------------------------------------
# خواندن تاریخچه
# --------------------------------------------------

def load_history():

    if not os.path.exists(DATA_FILE):
        return {}

    try:

        with open(DATA_FILE, "r") as f:
            return json.load(f)

    except Exception:

        return {}


# --------------------------------------------------
# ذخیره تاریخچه
# --------------------------------------------------

def save_history(history):

    with open(DATA_FILE, "w") as f:
        json.dump(history, f)


# --------------------------------------------------
# دریافت ۳۰ ارز در یک درخواست
# --------------------------------------------------

def get_market():

    response = requests.get(
        API_URL,
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

    print("MARKET STATUS:", response.status_code)

    response.raise_for_status()

    return response.json()


# --------------------------------------------------
# درصد تغییر
# --------------------------------------------------

def percent_change(old, new):

    if old <= 0:
        return 0.0

    return ((new - old) / old) * 100


# --------------------------------------------------
# پیدا کردن قیمت حدود N دقیقه قبل
# --------------------------------------------------

def historical_change(records, minutes, current_price, current_time):

    if not records:
        return None

    target = current_time - minutes * 60

    candidate = None

    for item in records:

        if item["time"] <= target:
            candidate = item
        else:
            break

    if candidate is None:
        return None

    return percent_change(
        candidate["price"],
        current_price
    )


# --------------------------------------------------
# RSI
# --------------------------------------------------

def calculate_rsi(prices, period=14):

    if len(prices) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(prices)):

        diff = prices[i] - prices[i - 1]

        if diff > 0:

            gains.append(diff)
            losses.append(0)

        else:

            gains.append(0)
            losses.append(abs(diff))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


# --------------------------------------------------
# EMA
# --------------------------------------------------

def calculate_ema(prices, period):

    if len(prices) < period:
        return None

    multiplier = 2 / (period + 1)

    ema = sum(prices[:period]) / period

    for price in prices[period:]:

        ema = (
            (price - ema) * multiplier
        ) + ema

    return ema


# --------------------------------------------------
# محاسبه امتیاز
# --------------------------------------------------

def calculate_score(
    change5,
    change10,
    change15,
    rsi,
    ema5,
    ema10,
    price,
    breakout
):

    score = 0

    # -----------------------------
    # 5 دقیقه
    # -----------------------------

    if change5 is not None:

        if change5 >= 2.0:
            score += 30

        elif change5 >= 1.2:
            score += 25

        elif change5 >= 0.8:
            score += 20

        elif change5 >= 0.5:
            score += 14

        elif change5 >= 0.25:
            score += 7

    # -----------------------------
    # 10 دقیقه
    # -----------------------------

    if change10 is not None:

        if change10 >= 3.0:
            score += 18

        elif change10 >= 2.0:
            score += 14

        elif change10 >= 1.0:
            score += 9

        elif change10 >= 0.5:
            score += 5

    # -----------------------------
    # 15 دقیقه
    # -----------------------------

    if change15 is not None:

        if change15 >= 4.0:
            score += 15

        elif change15 >= 2.5:
            score += 12

        elif change15 >= 1.5:
            score += 8

        elif change15 >= 0.7:
            score += 4

    # -----------------------------
    # شتاب
    # -----------------------------

    if (
        change5 is not None
        and change10 is not None
        and change15 is not None
    ):

        if change5 > 0 and change10 > 0:

            if change5 >= change10 * 0.45:
                score += 8

        if change10 > 0 and change15 > 0:

            if change10 >= change15 * 0.45:
                score += 5

    # -----------------------------
    # RSI
    # -----------------------------

    if rsi is not None:

        if 60 <= rsi <= 72:
            score += 10

        elif 55 <= rsi < 60:
            score += 6

        elif 72 < rsi <= 78:
            score += 4

        elif rsi > 82:
            score -= 5

    # -----------------------------
    # EMA
    # -----------------------------

    if (
        ema5 is not None
        and ema10 is not None
        and price > ema5 > ema10
    ):

        score += 5

    # -----------------------------
    # Breakout
    # -----------------------------

    if breakout:
        score += 9

    return max(0, min(100, score))


# --------------------------------------------------
# تلگرام
# --------------------------------------------------

def send_telegram(message):

    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": message
        },
        timeout=20
    )

    print("TELEGRAM:", response.text)

    response.raise_for_status()


# --------------------------------------------------
# تحلیل هر ارز
# --------------------------------------------------

def analyze_coin(coin, history, current_time):

    coin_id = coin["id"]
    symbol = coin["symbol"].upper()

    price = coin.get("current_price")

    if not price:
        return None

    records = history.get(coin_id, [])

    # اضافه کردن نمونه فعلی
    records.append({
        "time": current_time,
        "price": price
    })

    # فقط حدود 2 ساعت تاریخچه نگه می‌داریم
    cutoff = current_time - (2 * 60 * 60)

    records = [
        x for x in records
        if x["time"] >= cutoff
    ]

    history[coin_id] = records

    # -----------------------------
    # تغییرات زمانی
    # -----------------------------

    change5 = historical_change(
        records,
        5,
        price,
        current_time
    )

    change10 = historical_change(
        records,
        10,
        price,
        current_time
    )

    change15 = historical_change(
        records,
        15,
        price,
        current_time
    )

    # -----------------------------
    # RSI
    # -----------------------------

    prices = [
        x["price"]
        for x in records
    ]

    rsi = calculate_rsi(prices)

    # -----------------------------
    # EMA
    # -----------------------------

    ema5 = calculate_ema(
        prices,
        5
    )

    ema10 = calculate_ema(
        prices,
        10
    )

    # -----------------------------
    # Breakout
    # -----------------------------

    breakout = False

    if len(prices) >= 10:

        previous_prices = prices[-10:-1]

        previous_high = max(
            previous_prices
        )

        if price > previous_high:
            breakout = True

    # -----------------------------
    # Score
    # -----------------------------

    score = calculate_score(
        change5,
        change10,
        change15,
        rsi,
        ema5,
        ema10,
        price,
        breakout
    )

    return {
        "symbol": symbol,
        "price": price,
        "change5": change5,
        "change10": change10,
        "change15": change15,
        "rsi": rsi,
        "ema5": ema5,
        "ema10": ema10,
        "breakout": breakout,
        "score": score,
        "samples": len(records)
    }


# --------------------------------------------------
# برنامه اصلی
# --------------------------------------------------

def main():

    current_time = now()

    history = load_history()

    market = get_market()

    results = []

    failed = []

    for coin in market:

        try:

            result = analyze_coin(
                coin,
                history,
                current_time
            )

            if result:
                results.append(result)

        except Exception as e:

            print(
                coin["id"],
                "ERROR:",
                e
            )

            failed.append(
                coin["id"]
            )

    save_history(history)

    if not results:

        raise Exception(
            "No valid market data"
        )

    # -----------------------------
    # مرتب‌سازی
    # -----------------------------

    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    # -----------------------------
    # پیام تلگرام
    # -----------------------------

    message = (
        "🚨 PUMP SCANNER 5M\n"
        "━━━━━━━━━━━━━━\n"
    )

    # -----------------------------
    # سیگنال‌های قوی
    # -----------------------------

    strong = [
        x for x in results
        if (
            x["score"] >= 70
            and x["change5"] is not None
            and x["change5"] > 0
        )
    ]

    if strong:

        message += "\n🔥 STRONG PUMP SIGNAL\n\n"

        for x in strong[:5]:

            rsi_text = (
                f"{x['rsi']:.0f}"
                if x["rsi"] is not None
                else "N/A"
            )

            message += (
                f"🚨 {x['symbol']}\n"
                f"⭐ Score: {x['score']}/100\n"
                f"5m: {x['change5']:+.2f}%\n"
                f"10m: "
                f"{x['change10']:+.2f}%\n"
                f"15m: "
                f"{x['change15']:+.2f}%\n"
                f"RSI: {rsi_text}\n"
                f"Breakout: "
                f"{'✅' if x['breakout'] else '❌'}\n\n"
            )

    else:

        message += (
            "\n⚪ فعلاً سیگنال قوی وجود ندارد.\n"
        )

    # -----------------------------
    # TOP 5
    # -----------------------------

    message += "\n🏆 TOP 5\n\n"

    for i, x in enumerate(
        results[:5],
        1
    ):

        c5 = (
            f"{x['change5']:+.2f}%"
            if x["change5"] is not None
            else "N/A"
        )

        c15 = (
            f"{x['change15']:+.2f}%"
            if x["change15"] is not None
            else "N/A"
        )

        rsi = (
            f"{x['rsi']:.0f}"
            if x["rsi"] is not None
            else "N/A"
        )

        message += (
            f"{i}. {x['symbol']} "
            f"⭐ {x['score']}/100\n"
            f"   5m {c5} | "
            f"15m {c15} | "
            f"RSI {rsi}\n"
        )

    # -----------------------------
    # وضعیت سیستم
    # -----------------------------

    message += (
        "\n━━━━━━━━━━━━━━\n"
        f"📊 Scanned: {len(results)}/30\n"
        f"❌ Failed: {len(failed)}\n"
        "⏱ Timeframe: 5m\n"
        "📡 Source: CoinGecko\n"
    )

    send_telegram(message)


if __name__ == "__main__":
    main()
