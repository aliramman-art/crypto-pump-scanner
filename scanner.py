# ============================================================
# ICHIMOKU TENKAN / KIJUN OVERLAP SCANNER
# ============================================================
# Kraken Futures
# 30 Coins
# Timeframes: 1m / 5m / 15m / 30m / 1h / 4h
#
# هدف:
# پیدا کردن نزدیک‌ترین همپوشانی Tenkan و Kijun
#
# خروجی:
# فقط TOP 5
# ============================================================

import os
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone


# ============================================================
# SETTINGS
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

KRAKEN_URL = "https://futures.kraken.com/api/charts/v1/trade"

TOP_N = 5

# حداکثر فاصله برای در نظر گرفتن Overlap
MAX_OVERLAP_DISTANCE = 0.10

TIMEFRAMES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
}


# ============================================================
# COINS
# ============================================================

SYMBOLS = {
    "BTC": "pf_xbtusd",
    "ETH": "pf_ethusd",
    "BNB": "pf_bnbusd",
    "SOL": "pf_solusd",
    "XRP": "pf_xrpusd",
    "DOGE": "pf_dogeusd",
    "ADA": "pf_adausd",
    "AVAX": "pf_avaxusd",
    "LINK": "pf_linkusd",
    "DOT": "pf_dotusd",
    "TRX": "pf_trxusd",
    "LTC": "pf_ltcusd",
    "BCH": "pf_bchusd",
    "ATOM": "pf_atomusd",
    "UNI": "pf_uniusd",
    "ETC": "pf_etcusd",
    "XLM": "pf_xlmusd",
    "NEAR": "pf_nearusd",
    "APT": "pf_aptusd",
    "FIL": "pf_filusd",
    "ARB": "pf_arbusd",
    "OP": "pf_opusd",
    "SUI": "pf_suiusd",
    "INJ": "pf_injusd",
    "AAVE": "pf_aaveusd",
    "MKR": "pf_mkrusd",
    "ALGO": "pf_algousd",
    "VET": "pf_vetusd",
    "SEI": "pf_seiusd",
    "TIA": "pf_tiausd",
}


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secrets are missing.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=20
        )

        print("Telegram HTTP:", response.status_code)

        if response.status_code == 200:
            print("Telegram message sent.")
            return True

        print("Telegram error:", response.text)
        return False

    except Exception as e:

        print("Telegram exception:", e)
        return False


# ============================================================
# KRAKEN DATA
# ============================================================

def get_ohlcv(symbol, interval, limit=200):

    params = {
        "symbol": symbol,
        "interval": interval,
    }

    try:

        response = requests.get(
            KRAKEN_URL,
            params=params,
            timeout=20
        )

        print(
            f"KRAKEN {symbol} {interval} "
            f"HTTP={response.status_code}"
        )

        if response.status_code != 200:
            return None

        data = response.json()

        candles = data.get("candles")

        if not candles:
            return None

        rows = []

        for candle in candles[-limit:]:

            try:

                rows.append({
                    "time": pd.to_datetime(
                        candle["time"],
                        unit="ms",
                        utc=True
                    ),
                    "open": float(candle["open"]),
                    "high": float(candle["high"]),
                    "low": float(candle["low"]),
                    "close": float(candle["close"]),
                    "volume": float(candle.get("volume", 0)),
                })

            except Exception:
                continue

        if len(rows) < 60:
            return None

        df = pd.DataFrame(rows)

        df = df.drop_duplicates(
            subset=["time"]
        )

        df = df.sort_values("time")

        df = df.reset_index(drop=True)

        return df

    except Exception as e:

        print(
            f"KRAKEN ERROR {symbol} "
            f"{interval}: {e}"
        )

        return None


# ============================================================
# ICHIMOKU
# ============================================================

def calculate_ichimoku(df):

    high = df["high"]
    low = df["low"]

    # Tenkan-sen
    tenkan = (
        high.rolling(9).max()
        + low.rolling(9).min()
    ) / 2

    # Kijun-sen
    kijun = (
        high.rolling(26).max()
        + low.rolling(26).min()
    ) / 2

    # Senkou A
    span_a = (tenkan + kijun) / 2

    # Senkou B
    span_b = (
        high.rolling(52).max()
        + low.rolling(52).min()
    ) / 2

    df["tenkan"] = tenkan
    df["kijun"] = kijun
    df["span_a"] = span_a
    df["span_b"] = span_b

    return df


# ============================================================
# ANALYZE TIMEFRAME
# ============================================================

def analyze_timeframe(df):

    if df is None or len(df) < 60:
        return None

    df = calculate_ichimoku(df)

    row = df.iloc[-1]

    if (
        pd.isna(row["tenkan"])
        or pd.isna(row["kijun"])
    ):
        return None

    price = float(row["close"])
    tenkan = float(row["tenkan"])
    kijun = float(row["kijun"])

    if price <= 0:
        return None

    # فاصله Tenkan/Kijun نسبت به قیمت
    distance = (
        abs(tenkan - kijun)
        / price
        * 100
    )

    # جهت
    if tenkan > kijun:
        direction = "BULLISH"
    elif tenkan < kijun:
        direction = "BEARISH"
    else:
        direction = "EQUAL"

    # وضعیت Cloud
    span_a = row["span_a"]
    span_b = row["span_b"]

    cloud = "UNKNOWN"

    if not pd.isna(span_a) and not pd.isna(span_b):

        cloud_top = max(
            float(span_a),
            float(span_b)
        )

        cloud_bottom = min(
            float(span_a),
            float(span_b)
        )

        if price > cloud_top:
            cloud = "ABOVE"

        elif price < cloud_bottom:
            cloud = "BELOW"

        else:
            cloud = "INSIDE"

    # شیب Tenkan
    tenkan_slope = 0

    if len(df) >= 3:

        previous_tenkan = df["tenkan"].iloc[-3]

        if not pd.isna(previous_tenkan):

            if tenkan > previous_tenkan:
                tenkan_slope = 1

            elif tenkan < previous_tenkan:
                tenkan_slope = -1

    # شیب Kijun
    kijun_slope = 0

    if len(df) >= 3:

        previous_kijun = df["kijun"].iloc[-3]

        if not pd.isna(previous_kijun):

            if kijun > previous_kijun:
                kijun_slope = 1

            elif kijun < previous_kijun:
                kijun_slope = -1

    return {
        "price": price,
        "tenkan": tenkan,
        "kijun": kijun,
        "distance": distance,
        "direction": direction,
        "cloud": cloud,
        "tenkan_slope": tenkan_slope,
        "kijun_slope": kijun_slope,
    }


# ============================================================
# TIMEFRAME SCORE
# ============================================================

def calculate_tf_score(data):

    if data is None:
        return 0

    distance = data["distance"]

    # هرچه فاصله کمتر، امتیاز بیشتر
    if distance <= 0.01:
        score = 30

    elif distance <= 0.03:
        score = 27

    elif distance <= 0.05:
        score = 24

    elif distance <= 0.07:
        score = 20

    elif distance <= 0.10:
        score = 15

    else:
        score = 0

    # جهت صعودی
    if data["direction"] == "BULLISH":
        score += 5

    # شیب مثبت
    if data["tenkan_slope"] == 1:
        score += 3

    if data["kijun_slope"] == 1:
        score += 2

    # قیمت بالای Cloud
    if data["cloud"] == "ABOVE":
        score += 5

    return score


# ============================================================
# ANALYZE COIN
# ============================================================

def analyze_coin(name, symbol):

    result = {
        "coin": name,
        "symbol": symbol,
        "timeframes": {},
        "overlaps": [],
        "score": 0,
    }

    for tf_name, interval in TIMEFRAMES.items():

        df = get_ohlcv(
            symbol,
            interval
        )

        data = analyze_timeframe(df)

        result["timeframes"][tf_name] = data

        if data is not None:

            # فقط همپوشانی صعودی
            if (
                data["direction"] == "BULLISH"
                and
                data["distance"] <= MAX_OVERLAP_DISTANCE
            ):

                result["overlaps"].append({
                    "tf": tf_name,
                    "distance": data["distance"],
                    "data": data,
                })

        # فشار اضافه روی API ندهیم
        time.sleep(0.10)

    if not result["overlaps"]:
        return None

    # مرتب‌سازی بر اساس نزدیک‌ترین همپوشانی
    result["overlaps"].sort(
        key=lambda x: x["distance"]
    )

    # امتیاز پایه
    overlap_count = len(
        result["overlaps"]
    )

    result["score"] = min(
        100,
        overlap_count * 15
    )

    # نزدیک‌ترین فاصله امتیاز اضافه می‌گیرد
    closest = result["overlaps"][0]["distance"]

    if closest <= 0.01:
        result["score"] += 25

    elif closest <= 0.03:
        result["score"] += 20

    elif closest <= 0.05:
        result["score"] += 15

    elif closest <= 0.07:
        result["score"] += 10

    else:
        result["score"] += 5

    # Cloud و شیب را هم در امتیاز لحاظ می‌کنیم
    for item in result["overlaps"]:

        data = item["data"]

        if data["cloud"] == "ABOVE":
            result["score"] += 2

        if data["tenkan_slope"] == 1:
            result["score"] += 1

        if data["kijun_slope"] == 1:
            result["score"] += 1

    result["score"] = min(
        100,
        result["score"]
    )

    return result


# ============================================================
# FORMAT TELEGRAM
# ============================================================

def format_result(results, total):

    now = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    message = (
        "🔎 <b>ICHIMOKU OVERLAP SCANNER</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"UTC: {now}\n"
        f"Coins: {total}/30\n"
        "Strategy: Tenkan/Kijun\n"
        "Bullish overlap only\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
    )

    if not results:

        message += (
            "⚪ <b>NO CLOSE OVERLAP</b>\n\n"
            f"Threshold: ≤ {MAX_OVERLAP_DISTANCE:.2f}%"
        )

        return message

    message += "🔥 <b>TOP 5 NEAREST</b>\n\n"

    for i, result in enumerate(
        results[:TOP_N],
        start=1
    ):

        message += (
            f"<b>{i}. {result['coin']}</b> "
            f"⭐ {result['score']}/100\n"
        )

        for item in result["overlaps"]:

            tf = item["tf"]
            distance = item["distance"]

            data = item["data"]

            if data["direction"] == "BULLISH":
                icon = "🟢"
            else:
                icon = "🔴"

            message += (
                f"   {tf:<3} "
                f"{icon} {distance:.3f}%\n"
            )

        closest = result["overlaps"][0]

        message += (
            f"   🎯 Closest: "
            f"{closest['tf']} "
            f"{closest['distance']:.3f}%\n\n"
        )

    return message


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=========================================="
    )
    print(
        "ICHIMOKU OVERLAP SCANNER"
    )
    print(
        "=========================================="
    )

    results = []

    successful = 0

    for name, symbol in SYMBOLS.items():

        print(
            f"\nScanning {name}..."
        )

        try:

            result = analyze_coin(
                name,
                symbol
            )

            successful += 1

            if result is not None:

                results.append(result)

                print(
                    f"{name}: "
                    f"OVERLAP "
                    f"closest="
                    f"{result['overlaps'][0]['distance']:.4f}% "
                    f"score="
                    f"{result['score']}"
                )

            else:

                print(
                    f"{name}: "
                    f"NO CLOSE OVERLAP"
                )

        except Exception as e:

            print(
                f"{name}: ERROR {e}"
            )

    # ========================================================
    # SORT
    # ========================================================

    results.sort(
        key=lambda x: (
            x["overlaps"][0]["distance"],
            -x["score"]
        )
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    message = format_result(
        results,
        successful
    )

    print("\n")
    print(message)

    send_telegram(message)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
