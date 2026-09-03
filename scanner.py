# ============================================================
# ICHIMOKU SIGNAL SCANNER v2
# ============================================================
# Kraken Futures
# 30 Coins
# Timeframes:
# 1m / 5m / 15m / 30m / 1h / 4h
#
# NO DIVERGENCE
#
# Detects:
# - Tenkan / Kijun proximity
# - Bullish TK Cross
# - Bearish TK Cross
# - Tenkan slope
# - Kijun slope
# - Cloud position
# - Multi-timeframe alignment
#
# Telegram
# ============================================================

import os
import time
import requests
import pandas as pd
from datetime import datetime, timezone


# ============================================================
# SETTINGS
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

KRAKEN_URL = "https://futures.kraken.com/api/charts/v1/trade"

TOP_N = 8

# فاصله حداکثری برای کاندید شدن
MAX_TK_DISTANCE = 0.25

# فاصله‌های مهم
STRONG_DISTANCE = 0.05
GOOD_DISTANCE = 0.10

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
        print("Telegram secrets missing.")
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

        print(
            "Telegram HTTP:",
            response.status_code
        )

        if response.status_code == 200:
            print("Telegram message sent.")
            return True

        print(
            "Telegram error:",
            response.text
        )

    except Exception as e:

        print(
            "Telegram exception:",
            e
        )

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
            f"KRAKEN {symbol} "
            f"{interval} "
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
                    "open": float(
                        candle["open"]
                    ),
                    "high": float(
                        candle["high"]
                    ),
                    "low": float(
                        candle["low"]
                    ),
                    "close": float(
                        candle["close"]
                    ),
                    "volume": float(
                        candle.get(
                            "volume",
                            0
                        )
                    ),
                })

            except Exception:
                continue

        if len(rows) < 60:
            return None

        df = pd.DataFrame(rows)

        df = (
            df
            .drop_duplicates(
                subset=["time"]
            )
            .sort_values("time")
            .reset_index(drop=True)
        )

        return df

    except Exception as e:

        print(
            f"KRAKEN ERROR "
            f"{symbol} {interval}: {e}"
        )

        return None


# ============================================================
# ICHIMOKU
# ============================================================

def calculate_ichimoku(df):

    high = df["high"]
    low = df["low"]

    # Tenkan 9
    tenkan = (
        high.rolling(9).max()
        +
        low.rolling(9).min()
    ) / 2

    # Kijun 26
    kijun = (
        high.rolling(26).max()
        +
        low.rolling(26).min()
    ) / 2

    # Senkou A
    span_a = (
        tenkan + kijun
    ) / 2

    # Senkou B 52
    span_b = (
        high.rolling(52).max()
        +
        low.rolling(52).min()
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

    current = df.iloc[-1]
    previous = df.iloc[-2]

    if (
        pd.isna(current["tenkan"])
        or
        pd.isna(current["kijun"])
        or
        pd.isna(previous["tenkan"])
        or
        pd.isna(previous["kijun"])
    ):
        return None

    price = float(current["close"])

    tenkan = float(
        current["tenkan"]
    )

    kijun = float(
        current["kijun"]
    )

    previous_tenkan = float(
        previous["tenkan"]
    )

    previous_kijun = float(
        previous["kijun"]
    )

    if price <= 0:
        return None

    # --------------------------------------------------------
    # T/K DISTANCE
    # --------------------------------------------------------

    distance = (
        abs(tenkan - kijun)
        / price
        * 100
    )

    # --------------------------------------------------------
    # DIRECTION
    # --------------------------------------------------------

    if tenkan > kijun:
        direction = "BULLISH"

    elif tenkan < kijun:
        direction = "BEARISH"

    else:
        direction = "EQUAL"

    # --------------------------------------------------------
    # TK CROSS
    # --------------------------------------------------------

    bullish_cross = (
        previous_tenkan
        <= previous_kijun
        and
        tenkan
        > kijun
    )

    bearish_cross = (
        previous_tenkan
        >= previous_kijun
        and
        tenkan
        < kijun
    )

    if bullish_cross:
        cross = "BULLISH_CROSS"

    elif bearish_cross:
        cross = "BEARISH_CROSS"

    else:
        cross = "NONE"

    # --------------------------------------------------------
    # SLOPES
    # --------------------------------------------------------

    older_tenkan = df["tenkan"].iloc[-3]
    older_kijun = df["kijun"].iloc[-3]

    tenkan_slope = "FLAT"
    kijun_slope = "FLAT"

    if not pd.isna(older_tenkan):

        if tenkan > older_tenkan:
            tenkan_slope = "UP"

        elif tenkan < older_tenkan:
            tenkan_slope = "DOWN"

    if not pd.isna(older_kijun):

        if kijun > older_kijun:
            kijun_slope = "UP"

        elif kijun < older_kijun:
            kijun_slope = "DOWN"

    # --------------------------------------------------------
    # CLOUD
    # --------------------------------------------------------

    span_a = current["span_a"]
    span_b = current["span_b"]

    cloud = "UNKNOWN"

    if (
        not pd.isna(span_a)
        and
        not pd.isna(span_b)
    ):

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

    return {
        "price": price,
        "tenkan": tenkan,
        "kijun": kijun,
        "distance": distance,
        "direction": direction,
        "cross": cross,
        "tenkan_slope": tenkan_slope,
        "kijun_slope": kijun_slope,
        "cloud": cloud,
    }


# ============================================================
# SCORE ONE TIMEFRAME
# ============================================================

def timeframe_score(data):

    if data is None:
        return 0

    score = 0

    distance = data["distance"]

    # --------------------------------------------------------
    # DISTANCE
    # --------------------------------------------------------

    if distance <= STRONG_DISTANCE:
        score += 30

    elif distance <= GOOD_DISTANCE:
        score += 24

    elif distance <= MAX_TK_DISTANCE:
        score += 15

    # --------------------------------------------------------
    # CROSS
    # --------------------------------------------------------

    if data["cross"] in (
        "BULLISH_CROSS",
        "BEARISH_CROSS"
    ):
        score += 25

    # --------------------------------------------------------
    # SLOPE
    # --------------------------------------------------------

    if data["direction"] == "BULLISH":

        if data["tenkan_slope"] == "UP":
            score += 7

        if data["kijun_slope"] == "UP":
            score += 5

    elif data["direction"] == "BEARISH":

        if data["tenkan_slope"] == "DOWN":
            score += 7

        if data["kijun_slope"] == "DOWN":
            score += 5

    # --------------------------------------------------------
    # CLOUD
    # --------------------------------------------------------

    if data["direction"] == "BULLISH":

        if data["cloud"] == "ABOVE":
            score += 8

        elif data["cloud"] == "INSIDE":
            score += 3

    elif data["direction"] == "BEARISH":

        if data["cloud"] == "BELOW":
            score += 8

        elif data["cloud"] == "INSIDE":
            score += 3

    return min(score, 100)


# ============================================================
# ANALYZE COIN
# ============================================================

def analyze_coin(name, symbol):

    result = {
        "coin": name,
        "symbol": symbol,
        "timeframes": {},
        "signals": [],
        "score": 0,
        "direction": None,
    }

    for tf_name, interval in TIMEFRAMES.items():

        df = get_ohlcv(
            symbol,
            interval
        )

        data = analyze_timeframe(df)

        result["timeframes"][
            tf_name
        ] = data

        if data is not None:

            # ------------------------------------------------
            # Candidate condition
            # ------------------------------------------------

            close_enough = (
                data["distance"]
                <= MAX_TK_DISTANCE
            )

            cross = (
                data["cross"]
                != "NONE"
            )

            if close_enough or cross:

                tf_score = timeframe_score(
                    data
                )

                result["signals"].append({
                    "tf": tf_name,
                    "score": tf_score,
                    "data": data,
                })

        time.sleep(0.10)

    if not result["signals"]:
        return None

    # ========================================================
    # DIRECTION FROM SIGNALS
    # ========================================================

    bullish_points = 0
    bearish_points = 0

    for item in result["signals"]:

        data = item["data"]
        score = item["score"]

        if data["direction"] == "BULLISH":
            bullish_points += score

        elif data["direction"] == "BEARISH":
            bearish_points += score

        if data["cross"] == "BULLISH_CROSS":
            bullish_points += 20

        elif data["cross"] == "BEARISH_CROSS":
            bearish_points += 20

    if bullish_points > bearish_points:

        result["direction"] = "BULLISH"

    elif bearish_points > bullish_points:

        result["direction"] = "BEARISH"

    else:

        result["direction"] = "MIXED"

    # ========================================================
    # MULTI-TIMEFRAME SCORE
    # ========================================================

    tf_count = len(
        result["signals"]
    )

    base_score = sum(
        x["score"]
        for x in result["signals"]
    )

    # چند تایم‌فریم همزمان
    alignment_bonus = min(
        tf_count * 5,
        25
    )

    # --------------------------------------------------------
    # Direction alignment
    # --------------------------------------------------------

    direction_bonus = 0

    if result["direction"] == "BULLISH":

        bullish_tfs = sum(
            1
            for x in result["signals"]
            if x["data"]["direction"]
            == "BULLISH"
        )

        if bullish_tfs >= 2:
            direction_bonus = 10

        if bullish_tfs >= 3:
            direction_bonus = 15

    elif result["direction"] == "BEARISH":

        bearish_tfs = sum(
            1
            for x in result["signals"]
            if x["data"]["direction"]
            == "BEARISH"
        )

        if bearish_tfs >= 2:
            direction_bonus = 10

        if bearish_tfs >= 3:
            direction_bonus = 15

    # --------------------------------------------------------
    # Final score
    # --------------------------------------------------------

    result["score"] = min(
        100,
        int(
            base_score / tf_count
            +
            alignment_bonus
            +
            direction_bonus
        )
    )

    # نزدیک‌ترین T/K
    result["signals"].sort(
        key=lambda x: (
            x["data"]["distance"],
            -x["score"]
        )
    )

    result["closest"] = (
        result["signals"][0]
    )

    return result


# ============================================================
# FORMAT
# ============================================================

def format_message(results):

    now = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    message = (
        "🔎 <b>ICHIMOKU SIGNAL SCANNER v2</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"UTC: {now}\n"
        "TK Overlap + TK Cross\n"
        "Multi-Timeframe\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
    )

    if not results:

        message += (
            "⚪ <b>NO ICHIMOKU SETUP</b>"
        )

        return message

    message += (
        "🔥 <b>TOP SIGNALS</b>\n\n"
    )

    for index, result in enumerate(
        results[:TOP_N],
        start=1
    ):

        if result["direction"] == "BULLISH":
            direction_icon = "🟢"
        elif result["direction"] == "BEARISH":
            direction_icon = "🔴"
        else:
            direction_icon = "🟡"

        message += (
            f"<b>{index}. "
            f"{direction_icon} "
            f"{result['coin']}</b> "
            f"⭐ {result['score']}/100\n"
        )

        for item in result["signals"]:

            tf = item["tf"]
            data = item["data"]

            distance = data["distance"]

            if data["cross"] == "BULLISH_CROSS":

                status = "🚀 CROSS↑"

            elif data["cross"] == "BEARISH_CROSS":

                status = "🔻 CROSS↓"

            elif data["direction"] == "BULLISH":

                status = "🟢 T/K"

            elif data["direction"] == "BEARISH":

                status = "🔴 T/K"

            else:

                status = "⚪ T/K"

            message += (
                f"   {tf:<3} "
                f"{status} "
                f"{distance:.3f}%\n"
            )

        closest = result["closest"]

        message += (
            f"   🎯 Closest: "
            f"{closest['tf']} "
            f"{closest['data']['distance']:.3f}%\n"
        )

        message += "\n"

    return message


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=========================================="
    )

    print(
        "ICHIMOKU SIGNAL SCANNER v2"
    )

    print(
        "=========================================="
    )

    results = []

    success = 0

    for name, symbol in SYMBOLS.items():

        print(
            f"\nScanning {name}..."
        )

        try:

            result = analyze_coin(
                name,
                symbol
            )

            success += 1

            if result:

                results.append(result)

                print(
                    f"{name}: "
                    f"{result['direction']} "
                    f"score={result['score']} "
                    f"closest="
                    f"{result['closest']['data']['distance']:.4f}%"
                )

            else:

                print(
                    f"{name}: "
                    f"NO SETUP"
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
            -x["score"],
            x["closest"]["data"]["distance"]
        )
    )

    # ========================================================
    # MESSAGE
    # ========================================================

    message = format_message(
        results
    )

    print("\n")
    print(message)

    send_telegram(
        message
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
