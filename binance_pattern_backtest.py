# ============================================================
# BINANCE FUTURES PATTERN BACKTEST - 1 YEAR
# ============================================================
#
# هدف:
#   تشخیص چند پترن تکنیکال روی Binance USDT-M Futures
#   و محاسبه فقط:
#
#   - تعداد معاملات
#   - معاملات موفق
#   - معاملات ناموفق
#   - Success Rate
#   - Failure Rate
#
# موفقیت:
#   TP قبل از SL لمس شود
#
# شکست:
#   SL قبل از TP لمس شود
#
# داده:
#   Binance Futures
#
# تایم‌فریم:
#   1H
#
# ============================================================

import time
import math
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone


# ============================================================
# SETTINGS
# ============================================================

BASE_URL = "https://fapi.binance.com"

DAYS = 365

INTERVAL = "1h"

# تعداد ارزها
TOP_SYMBOLS = 30

# حداقل حجم 24 ساعته برای انتخاب ارزها
MIN_QUOTE_VOLUME = 50_000_000

# تعداد کندل مورد استفاده برای تشخیص پترن
LOOKBACK = 120

# تعداد کندل بعد از سیگنال برای بررسی TP / SL
MAX_HOLD_CANDLES = 48

# فاصله TP و SL
TP_PCT = 0.020       # 2%
SL_PCT = 0.010       # 1%

# حداقل فاصله برای تشخیص کف/سقف مشابه
PATTERN_TOLERANCE = 0.015

# حداقل فاصله بین دو کف/سقف
MIN_PATTERN_DISTANCE = 5

# حداکثر فاصله بین دو کف/سقف
MAX_PATTERN_DISTANCE = 60

# برای جلوگیری از سیگنال‌های پشت سر هم
SIGNAL_COOLDOWN = 10


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Mozilla/5.0"
})


# ============================================================
# BINANCE API
# ============================================================

def binance_get(endpoint, params=None):

    url = BASE_URL + endpoint

    for attempt in range(5):

        try:

            r = session.get(
                url,
                params=params,
                timeout=20
            )

            if r.status_code == 200:
                return r.json()

            if r.status_code == 429:
                time.sleep(2)

            else:
                print(
                    f"HTTP {r.status_code}: {r.text[:200]}"
                )
                time.sleep(1)

        except Exception as e:

            print("Request error:", e)
            time.sleep(2)

    return None


# ============================================================
# GET FUTURES SYMBOLS
# ============================================================

def get_symbols():

    data = binance_get(
        "/fapi/v1/exchangeInfo"
    )

    if not data:
        return []

    symbols = []

    for s in data["symbols"]:

        if s["status"] != "TRADING":
            continue

        if s["quoteAsset"] != "USDT":
            continue

        if s["contractType"] != "PERPETUAL":
            continue

        symbols.append(s["symbol"])

    return symbols


# ============================================================
# GET TOP VOLUME SYMBOLS
# ============================================================

def get_top_symbols():

    data = binance_get(
        "/fapi/v1/ticker/24hr"
    )

    if not data:
        return []

    valid_symbols = set(get_symbols())

    rows = []

    for x in data:

        symbol = x["symbol"]

        if symbol not in valid_symbols:
            continue

        try:
            volume = float(x["quoteVolume"])
        except:
            continue

        if volume < MIN_QUOTE_VOLUME:
            continue

        rows.append(
            (
                symbol,
                volume
            )
        )

    rows.sort(
        key=lambda x: x[1],
        reverse=True
    )

    return [
        x[0]
        for x in rows[:TOP_SYMBOLS]
    ]


# ============================================================
# GET KLINES
# ============================================================

def get_klines(
    symbol,
    start_ms,
    end_ms
):

    all_rows = []

    current_start = start_ms

    while current_start < end_ms:

        params = {
            "symbol": symbol,
            "interval": INTERVAL,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": 1500
        }

        data = binance_get(
            "/fapi/v1/klines",
            params
        )

        if not data:
            break

        all_rows.extend(data)

        if len(data) < 1500:
            break

        last_open_time = data[-1][0]

        current_start = last_open_time + 1

        time.sleep(0.05)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        all_rows,
        columns=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "trades",
            "taker_base",
            "taker_quote",
            "ignore"
        ]
    )

    numeric_cols = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume"
    ]

    for c in numeric_cols:
        df[c] = pd.to_numeric(
            df[c],
            errors="coerce"
        )

    df["open_time"] = pd.to_datetime(
        df["open_time"],
        unit="ms",
        utc=True
    )

    df["close_time"] = pd.to_datetime(
        df["close_time"],
        unit="ms",
        utc=True
    )

    df = df.drop_duplicates(
        subset=["open_time"]
    )

    df = df.sort_values(
        "open_time"
    )

    df = df.reset_index(
        drop=True
    )

    return df


# ============================================================
# SWING HIGH / LOW
# ============================================================

def find_swing_highs(df, left=2, right=2):

    highs = df["high"].values

    result = []

    for i in range(
        left,
        len(df) - right
    ):

        left_side = highs[
            i-left:i
        ]

        right_side = highs[
            i+1:i+right+1
        ]

        if (
            highs[i] > left_side.max()
            and
            highs[i] > right_side.max()
        ):
            result.append(i)

    return result


def find_swing_lows(df, left=2, right=2):

    lows = df["low"].values

    result = []

    for i in range(
        left,
        len(df) - right
    ):

        left_side = lows[
            i-left:i
        ]

        right_side = lows[
            i+1:i+right+1
        ]

        if (
            lows[i] < left_side.min()
            and
            lows[i] < right_side.min()
        ):
            result.append(i)

    return result


# ============================================================
# DOUBLE BOTTOM
# ============================================================

def detect_double_bottom(df, index):

    if index < LOOKBACK:
        return False

    start = max(
        0,
        index - LOOKBACK
    )

    window = df.iloc[start:index]

    lows = find_swing_lows(
        window
    )

    if len(lows) < 2:
        return False

    l2 = lows[-1]
    l1 = lows[-2]

    distance = l2 - l1

    if distance < MIN_PATTERN_DISTANCE:
        return False

    if distance > MAX_PATTERN_DISTANCE:
        return False

    p1 = float(
        window.iloc[l1]["low"]
    )

    p2 = float(
        window.iloc[l2]["low"]
    )

    avg = (p1 + p2) / 2

    if avg == 0:
        return False

    difference = abs(p1 - p2) / avg

    if difference > PATTERN_TOLERANCE:
        return False

    neckline = window.iloc[
        l1:l2+1
    ]["high"].max()

    current_close = float(
        df.iloc[index]["close"]
    )

    # شکست neckline
    if current_close <= neckline:
        return False

    return True


# ============================================================
# DOUBLE TOP
# ============================================================

def detect_double_top(df, index):

    if index < LOOKBACK:
        return False

    start = max(
        0,
        index - LOOKBACK
    )

    window = df.iloc[start:index]

    highs = find_swing_highs(
        window
    )

    if len(highs) < 2:
        return False

    h2 = highs[-1]
    h1 = highs[-2]

    distance = h2 - h1

    if distance < MIN_PATTERN_DISTANCE:
        return False

    if distance > MAX_PATTERN_DISTANCE:
        return False

    p1 = float(
        window.iloc[h1]["high"]
    )

    p2 = float(
        window.iloc[h2]["high"]
    )

    avg = (p1 + p2) / 2

    if avg == 0:
        return False

    difference = abs(p1 - p2) / avg

    if difference > PATTERN_TOLERANCE:
        return False

    neckline = window.iloc[
        h1:h2+1
    ]["low"].min()

    current_close = float(
        df.iloc[index]["close"]
    )

    # شکست neckline
    if current_close >= neckline:
        return False

    return True


# ============================================================
# HEAD & SHOULDERS
# ============================================================

def detect_head_shoulders(df, index):

    if index < LOOKBACK:
        return False

    start = max(
        0,
        index - LOOKBACK
    )

    window = df.iloc[start:index]

    highs = find_swing_highs(
        window
    )

    if len(highs) < 3:
        return False

    h1, h2, h3 = highs[-3:]

    p1 = float(
        window.iloc[h1]["high"]
    )

    p2 = float(
        window.iloc[h2]["high"]
    )

    p3 = float(
        window.iloc[h3]["high"]
    )

    # Head باید بالاتر باشد
    if not (
        p2 > p1
        and
        p2 > p3
    ):
        return False

    # دو شانه نسبتاً نزدیک
    shoulder_diff = (
        abs(p1 - p3)
        /
        ((p1 + p3) / 2)
    )

    if shoulder_diff > 0.03:
        return False

    neckline = min(
        window.iloc[h1:h2]["low"].min(),
        window.iloc[h2:h3]["low"].min()
    )

    current_close = float(
        df.iloc[index]["close"]
    )

    if current_close >= neckline:
        return False

    return True


# ============================================================
# INVERSE HEAD & SHOULDERS
# ============================================================

def detect_inverse_head_shoulders(
    df,
    index
):

    if index < LOOKBACK:
        return False

    start = max(
        0,
        index - LOOKBACK
    )

    window = df.iloc[start:index]

    lows = find_swing_lows(
        window
    )

    if len(lows) < 3:
        return False

    l1, l2, l3 = lows[-3:]

    p1 = float(
        window.iloc[l1]["low"]
    )

    p2 = float(
        window.iloc[l2]["low"]
    )

    p3 = float(
        window.iloc[l3]["low"]
    )

    if not (
        p2 < p1
        and
        p2 < p3
    ):
        return False

    shoulder_diff = (
        abs(p1 - p3)
        /
        ((p1 + p3) / 2)
    )

    if shoulder_diff > 0.03:
        return False

    neckline = max(
        window.iloc[l1:l2]["high"].max(),
        window.iloc[l2:l3]["high"].max()
    )

    current_close = float(
        df.iloc[index]["close"]
    )

    if current_close <= neckline:
        return False

    return True


# ============================================================
# TRIANGLE
# ============================================================

def detect_triangle(df, index):

    if index < LOOKBACK:
        return False

    start = max(
        0,
        index - LOOKBACK
    )

    window = df.iloc[start:index]

    highs = find_swing_highs(
        window
    )

    lows = find_swing_lows(
        window
    )

    if len(highs) < 2 or len(lows) < 2:
        return False

    h1, h2 = highs[-2:]
    l1, l2 = lows[-2:]

    high1 = float(
        window.iloc[h1]["high"]
    )

    high2 = float(
        window.iloc[h2]["high"]
    )

    low1 = float(
        window.iloc[l1]["low"]
    )

    low2 = float(
        window.iloc[l2]["low"]
    )

    # Lower highs
    lower_highs = high2 < high1

    # Higher lows
    higher_lows = low2 > low1

    if not (
        lower_highs
        and
        higher_lows
    ):
        return False

    current_close = float(
        df.iloc[index]["close"]
    )

    upper_line = max(
        high1,
        high2
    )

    lower_line = min(
        low1,
        low2
    )

    if (
        current_close > upper_line
        or
        current_close < lower_line
    ):
        return True

    return False


# ============================================================
# WEDGE
# ============================================================

def detect_wedge(df, index):

    if index < LOOKBACK:
        return False

    start = max(
        0,
        index - LOOKBACK
    )

    window = df.iloc[start:index]

    highs = find_swing_highs(
        window
    )

    lows = find_swing_lows(
        window
    )

    if len(highs) < 3 or len(lows) < 3:
        return False

    h1, h2 = highs[-2:]
    l1, l2 = lows[-2:]

    high1 = float(
        window.iloc[h1]["high"]
    )

    high2 = float(
        window.iloc[h2]["high"]
    )

    low1 = float(
        window.iloc[l1]["low"]
    )

    low2 = float(
        window.iloc[l2]["low"]
    )

    high_slope = high2 - high1
    low_slope = low2 - low1

    # Falling wedge
    if (
        high_slope < 0
        and
        low_slope < 0
        and
        low_slope > high_slope
    ):
        return True

    # Rising wedge
    if (
        high_slope > 0
        and
        low_slope > 0
        and
        low_slope < high_slope
    ):
        return True

    return False


# ============================================================
# FLAG
# ============================================================

def detect_flag(df, index):

    if index < 30:
        return False

    impulse_start = index - 25
    impulse_end = index - 10

    start_price = float(
        df.iloc[impulse_start]["close"]
    )

    end_price = float(
        df.iloc[impulse_end]["close"]
    )

    impulse_move = (
        end_price - start_price
    ) / start_price

    # Strong impulse
    if abs(impulse_move) < 0.04:
        return False

    flag = df.iloc[
        index-10:index
    ]

    flag_high = flag["high"].max()
    flag_low = flag["low"].min()

    flag_range = (
        flag_high - flag_low
    ) / end_price

    # Consolidation
    if flag_range > 0.035:
        return False

    current_close = float(
        df.iloc[index]["close"]
    )

    if impulse_move > 0:

        if current_close > flag_high:
            return True

    else:

        if current_close < flag_low:
            return True

    return False


# ============================================================
# DETECT ALL PATTERNS
# ============================================================

def detect_patterns(df, index):

    patterns = []

    if detect_double_bottom(
        df,
        index
    ):
        patterns.append(
            ("Double Bottom", "LONG")
        )

    if detect_double_top(
        df,
        index
    ):
        patterns.append(
            ("Double Top", "SHORT")
        )

    if detect_head_shoulders(
        df,
        index
    ):
        patterns.append(
            ("Head & Shoulders", "SHORT")
        )

    if detect_inverse_head_shoulders(
        df,
        index
    ):
        patterns.append(
            ("Inverse H&S", "LONG")
        )

    if detect_triangle(
        df,
        index
    ):
        # جهت بر اساس کندل
        current = float(
            df.iloc[index]["close"]
        )

        previous = float(
            df.iloc[index-1]["close"]
        )

        if current > previous:
            patterns.append(
                ("Triangle", "LONG")
            )

        elif current < previous:
            patterns.append(
                ("Triangle", "SHORT")
            )

    if detect_wedge(
        df,
        index
    ):

        current = float(
            df.iloc[index]["close"]
        )

        previous = float(
            df.iloc[index-1]["close"]
        )

        if current > previous:
            patterns.append(
                ("Wedge", "LONG")
            )

        else:
            patterns.append(
                ("Wedge", "SHORT")
            )

    if detect_flag(
        df,
        index
    ):

        current = float(
            df.iloc[index]["close"]
        )

        previous = float(
            df.iloc[index-1]["close"]
        )

        if current > previous:
            patterns.append(
                ("Flag", "LONG")
            )

        else:
            patterns.append(
                ("Flag", "SHORT")
            )

    return patterns


# ============================================================
# TEST TRADE
# ============================================================

def test_trade(
    df,
    entry_index,
    direction
):

    entry = float(
        df.iloc[entry_index]["close"]
    )

    if direction == "LONG":

        tp = entry * (
            1 + TP_PCT
        )

        sl = entry * (
            1 - SL_PCT
        )

    else:

        tp = entry * (
            1 - TP_PCT
        )

        sl = entry * (
            1 + SL_PCT
        )

    end_index = min(
        len(df),
        entry_index
        + MAX_HOLD_CANDLES
        + 1
    )

    for i in range(
        entry_index + 1,
        end_index
    ):

        high = float(
            df.iloc[i]["high"]
        )

        low = float(
            df.iloc[i]["low"]
        )

        if direction == "LONG":

            # اگر هر دو در یک کندل لمس شوند
            # حالت محافظه‌کارانه:
            # SL را اول فرض می‌کنیم
            if low <= sl:
                return False

            if high >= tp:
                return True

        else:

            if high >= sl:
                return False

            if low <= tp:
                return True

    # اگر هیچکدام نرسید
    return None


# ============================================================
# BACKTEST SYMBOL
# ============================================================

def backtest_symbol(
    symbol,
    df
):

    results = []

    last_signal_index = -999999

    start_index = LOOKBACK + 5

    end_index = (
        len(df)
        - MAX_HOLD_CANDLES
        - 2
    )

    for i in range(
        start_index,
        end_index
    ):

        if (
            i - last_signal_index
            < SIGNAL_COOLDOWN
        ):
            continue

        patterns = detect_patterns(
            df,
            i
        )

        if not patterns:
            continue

        # اگر چند پترن همزمان وجود داشت
        # فقط اولین مورد بررسی شود
        pattern, direction = patterns[0]

        result = test_trade(
            df,
            i,
            direction
        )

        if result is None:
            continue

        results.append({
            "symbol": symbol,
            "pattern": pattern,
            "direction": direction,
            "success": result
        })

        last_signal_index = i

    return results


# ============================================================
# MAIN BACKTEST
# ============================================================

def main():

    print()
    print("=" * 70)
    print("BINANCE FUTURES PATTERN BACKTEST - 1 YEAR")
    print("=" * 70)
    print()

    end_dt = datetime.now(
        timezone.utc
    )

    start_dt = (
        end_dt
        - timedelta(days=DAYS)
    )

    start_ms = int(
        start_dt.timestamp() * 1000
    )

    end_ms = int(
        end_dt.timestamp() * 1000
    )

    print(
        "Period:",
        start_dt.strftime(
            "%Y-%m-%d"
        ),
        "→",
        end_dt.strftime(
            "%Y-%m-%d"
        )
    )

    print(
        "Timeframe:",
        INTERVAL
    )

    print()

    print(
        "Selecting Binance Futures symbols..."
    )

    symbols = get_top_symbols()

    if not symbols:

        print(
            "No symbols found."
        )

        return

    print(
        f"Symbols: {len(symbols)}"
    )

    print()

    all_results = []

    for n, symbol in enumerate(
        symbols,
        1
    ):

        print(
            f"[{n}/{len(symbols)}] "
            f"{symbol}"
        )

        df = get_klines(
            symbol,
            start_ms,
            end_ms
        )

        if df.empty:

            print(
                "  No data"
            )

            continue

        print(
            f"  Candles: {len(df)}"
        )

        try:

            results = backtest_symbol(
                symbol,
                df
            )

            all_results.extend(
                results
            )

            print(
                f"  Trades: {len(results)}"
            )

        except Exception as e:

            print(
                "  Backtest error:",
                e
            )

    # ========================================================
    # FINAL STATISTICS
    # ========================================================

    print()
    print("=" * 70)
    print("FINAL RESULTS")
    print("=" * 70)

    total = len(
        all_results
    )

    successful = sum(
        1
        for x in all_results
        if x["success"]
    )

    failed = (
        total
        - successful
    )

    if total > 0:

        success_rate = (
            successful
            / total
            * 100
        )

        failure_rate = (
            failed
            / total
            * 100
        )

    else:

        success_rate = 0
        failure_rate = 0

    print()
    print(
        f"Total Trades:   {total}"
    )

    print(
        f"Successful:     {successful}"
    )

    print(
        f"Failed:         {failed}"
    )

    print(
        f"Success Rate:   {success_rate:.2f}%"
    )

    print(
        f"Failure Rate:   {failure_rate:.2f}%"
    )

    # ========================================================
    # PATTERN RESULTS
    # ========================================================

    print()
    print("=" * 70)
    print("PATTERN RESULTS")
    print("=" * 70)

    if all_results:

        df_results = pd.DataFrame(
            all_results
        )

        pattern_groups = (
            df_results
            .groupby("pattern")
        )

        for pattern, group in pattern_groups:

            trades = len(group)

            wins = int(
                group["success"].sum()
            )

            losses = (
                trades
                - wins
            )

            wr = (
                wins
                / trades
                * 100
            )

            fr = (
                losses
                / trades
                * 100
            )

            print()
            print(pattern)

            print(
                f"Trades:         {trades}"
            )

            print(
                f"Successful:     {wins}"
            )

            print(
                f"Failed:         {losses}"
            )

            print(
                f"Success Rate:   {wr:.2f}%"
            )

            print(
                f"Failure Rate:   {fr:.2f}%"
            )

        # ====================================================
        # LONG / SHORT
        # ====================================================

        print()
        print("=" * 70)
        print("LONG / SHORT")
        print("=" * 70)

        for direction in [
            "LONG",
            "SHORT"
        ]:

            group = df_results[
                df_results["direction"]
                == direction
            ]

            trades = len(group)

            if trades == 0:
                continue

            wins = int(
                group["success"].sum()
            )

            losses = (
                trades
                - wins
            )

            wr = (
                wins
                / trades
                * 100
            )

            print()
            print(direction)

            print(
                f"Trades:         {trades}"
            )

            print(
                f"Successful:     {wins}"
            )

            print(
                f"Failed:         {losses}"
            )

            print(
                f"Success Rate:   {wr:.2f}%"
            )

    print()
    print("=" * 70)
    print("BACKTEST COMPLETE")
    print("=" * 70)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
