# ============================================================
# KRAKEN FUTURES HISTORICAL DATA DOWNLOADER
# ============================================================
# Stage 1 of Ichimoku MTF Backtest
#
# Downloads 5m historical candles from Kraken Futures
# for a full one-year period.
#
# NO TRADING LOGIC HERE.
# ============================================================

import os
import time
import json
import requests
from datetime import datetime, timezone

# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com/api/charts/v1/trade"

TIMEFRAME = "5m"

# Exact backtest period
START_DATE = "2025-09-07"
END_DATE = "2026-09-07"

# Stage 1:
# Test with ONE symbol first.
# After successful test, change this.
TEST_SYMBOL = "PI_XBTUSD"

# Number of candles requested per API call
CHUNK_SIZE = 1000

REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 3
REQUEST_SLEEP = 0.5

DATA_DIR = "backtest_data"

# ============================================================
# HELPERS
# ============================================================


def utc_timestamp(date_string):
    """
    Convert YYYY-MM-DD to Unix timestamp.
    """
    dt = datetime.strptime(
        date_string,
        "%Y-%m-%d"
    ).replace(tzinfo=timezone.utc)

    return int(dt.timestamp())


def timestamp_to_iso(ts):
    """
    Unix timestamp -> UTC ISO string.
    """
    return datetime.fromtimestamp(
        ts,
        tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")


def normalize_candle(candle):
    """
    Kraken candle formats can vary.
    Convert the important fields to a common format.

    Expected:
        time
        open
        high
        low
        close
        volume
    """

    if isinstance(candle, dict):

        ts = (
            candle.get("time")
            or candle.get("timestamp")
            or candle.get("ts")
        )

        o = candle.get("open")
        h = candle.get("high")
        l = candle.get("low")
        c = candle.get("close")

        v = (
            candle.get("volume")
            or candle.get("vol")
            or 0
        )

    elif isinstance(candle, (list, tuple)):

        if len(candle) < 5:
            return None

        ts = candle[0]
        o = candle[1]
        h = candle[2]
        l = candle[3]
        c = candle[4]

        v = candle[5] if len(candle) > 5 else 0

    else:
        return None

    if ts is None:
        return None

    try:
        ts = int(float(ts))

        # Some APIs may return milliseconds
        if ts > 10_000_000_000:
            ts //= 1000

        return {
            "time": ts,
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": float(v),
        }

    except (TypeError, ValueError):
        return None


# ============================================================
# API
# ============================================================


def request_chunk(symbol, start_ts, end_ts):
    """
    Request one historical chunk from Kraken.
    """

    url = f"{BASE_URL}/{symbol}/{TIMEFRAME}"

    params = {
        "from": start_ts,
        "to": end_ts,
        "count": CHUNK_SIZE,
    }

    last_error = None

    for attempt in range(1, REQUEST_RETRIES + 1):

        try:

            response = requests.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            data = response.json()

            if not isinstance(data, dict):
                raise RuntimeError(
                    f"Unexpected API response type: "
                    f"{type(data).__name__}"
                )

            candles = data.get("candles", [])

            if not isinstance(candles, list):
                raise RuntimeError(
                    "API returned invalid candles field"
                )

            return candles

        except Exception as exc:

            last_error = exc

            print(
                f"[WARN] Request failed "
                f"{symbol} "
                f"attempt {attempt}/{REQUEST_RETRIES}: "
                f"{exc}"
            )

            if attempt < REQUEST_RETRIES:
                time.sleep(2 * attempt)

    raise RuntimeError(
        f"Failed to download {symbol}: {last_error}"
    )


# ============================================================
# DOWNLOAD
# ============================================================


def download_symbol(symbol):
    """
    Download the complete requested period.

    We move forward in time chunk-by-chunk.
    """

    start_ts = utc_timestamp(START_DATE)
    end_ts = utc_timestamp(END_DATE)

    # 5-minute candle = 300 seconds
    candle_seconds = 300

    current_ts = start_ts

    all_candles = {}

    total_requests = 0

    print()
    print("=" * 70)
    print(f"DOWNLOADING: {symbol}")
    print(f"TIMEFRAME : {TIMEFRAME}")
    print(
        f"PERIOD    : "
        f"{timestamp_to_iso(start_ts)}"
        f" -> "
        f"{timestamp_to_iso(end_ts)}"
    )
    print("=" * 70)

    while current_ts < end_ts:

        # Request approximately CHUNK_SIZE candles.
        #
        # One extra candle overlap helps prevent gaps
        # caused by API boundary behavior.
        request_end = min(
            current_ts + (CHUNK_SIZE * candle_seconds),
            end_ts,
        )

        total_requests += 1

        print(
            f"[{total_requests}] "
            f"{timestamp_to_iso(current_ts)}"
            f" -> "
            f"{timestamp_to_iso(request_end)}"
        )

        raw_candles = request_chunk(
            symbol,
            current_ts,
            request_end,
        )

        normalized = []

        for candle in raw_candles:

            item = normalize_candle(candle)

            if item is None:
                continue

            ts = item["time"]

            # Keep only requested period
            if ts < start_ts:
                continue

            if ts >= end_ts:
                continue

            normalized.append(item)

            all_candles[ts] = item

        print(
            f"    received={len(raw_candles)} "
            f"valid={len(normalized)} "
            f"total={len(all_candles)}"
        )

        # ----------------------------------------------------
        # IMPORTANT:
        # If API returns no candles, move forward anyway.
        # Otherwise an API anomaly could create an infinite loop.
        # ----------------------------------------------------

        if normalized:

            newest_ts = max(
                x["time"] for x in normalized
            )

            next_ts = newest_ts + candle_seconds

            # Never move backwards
            if next_ts <= current_ts:
                next_ts = current_ts + (
                    CHUNK_SIZE * candle_seconds
                )

            current_ts = next_ts

        else:

            current_ts = request_end

        time.sleep(REQUEST_SLEEP)

    # ========================================================
    # SORT
    # ========================================================

    candles = sorted(
        all_candles.values(),
        key=lambda x: x["time"]
    )

    # ========================================================
    # REMOVE INCOMPLETE FINAL CANDLE
    # ========================================================

    now_ts = int(
        datetime.now(timezone.utc).timestamp()
    )

    candles = [
        c for c in candles
        if c["time"] + candle_seconds <= now_ts
    ]

    # ========================================================
    # GAP CHECK
    # ========================================================

    gaps = []

    for i in range(1, len(candles)):

        previous = candles[i - 1]["time"]
        current = candles[i]["time"]

        diff = current - previous

        if diff != candle_seconds:

            gaps.append({
                "from": previous,
                "to": current,
                "missing_seconds": diff - candle_seconds,
            })

    # ========================================================
    # STATISTICS
    # ========================================================

    expected = int(
        (end_ts - start_ts) / candle_seconds
    )

    actual = len(candles)

    coverage = (
        (actual / expected) * 100
        if expected > 0
        else 0
    )

    print()
    print("=" * 70)
    print("DOWNLOAD COMPLETE")
    print("=" * 70)

    print(f"Symbol           : {symbol}")
    print(f"Expected candles : {expected:,}")
    print(f"Downloaded       : {actual:,}")
    print(f"Coverage         : {coverage:.2f}%")
    print(f"Gaps             : {len(gaps):,}")
    print(
        f"First candle     : "
        f"{timestamp_to_iso(candles[0]['time'])}"
        if candles
        else "First candle     : NONE"
    )
    print(
        f"Last candle      : "
        f"{timestamp_to_iso(candles[-1]['time'])}"
        if candles
        else "Last candle      : NONE"
    )

    # ========================================================
    # SAVE
    # ========================================================

    os.makedirs(DATA_DIR, exist_ok=True)

    output_file = os.path.join(
        DATA_DIR,
        f"{symbol}_{TIMEFRAME}.json"
    )

    result = {
        "symbol": symbol,
        "timeframe": TIMEFRAME,
        "start": START_DATE,
        "end": END_DATE,
        "downloaded_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "expected_candles": expected,
        "actual_candles": actual,
        "coverage_percent": coverage,
        "gap_count": len(gaps),
        "gaps": gaps[:100],
        "candles": candles,
    }

    with open(
        output_file,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            result,
            f,
            ensure_ascii=False,
        )

    print()
    print(f"[SAVED] {output_file}")

    return result


# ============================================================
# MAIN
# ============================================================


def main():

    print("=" * 70)
    print("KRAKEN FUTURES HISTORICAL DATA TEST")
    print("=" * 70)

    print(f"Symbol    : {TEST_SYMBOL}")
    print(f"Timeframe : {TIMEFRAME}")
    print(f"Start     : {START_DATE}")
    print(f"End       : {END_DATE}")

    try:

        result = download_symbol(TEST_SYMBOL)

        if result["actual_candles"] == 0:

            print()
            print("[ERROR] No candles downloaded.")
            return 1

        print()
        print("=" * 70)
        print("STAGE 1 SUCCESS")
        print("=" * 70)

        return 0

    except KeyboardInterrupt:

        print()
        print("[STOPPED] User interrupted.")

        return 1

    except Exception as exc:

        print()
        print("=" * 70)
        print("STAGE 1 FAILED")
        print("=" * 70)

        print(f"ERROR: {exc}")

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
