# ============================================================
# KRAKEN FUTURES HISTORICAL DATA DOWNLOADER
# ============================================================
# STAGE 1
# One-year 5m historical data test
# ============================================================

import os
import sys
import time
import json
import requests
from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com/api/charts/v1/trade"

TIMEFRAME = "5m"

START_DATE = "2025-09-07"
END_DATE = "2026-09-07"

# Stage 1 = ONE symbol only
TEST_SYMBOL = "PI_XBTUSD"

CHUNK_SIZE = 1000

REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 3
REQUEST_SLEEP = 0.3

DATA_DIR = "backtest_data"


# ============================================================
# LOG
# ============================================================

def log(message=""):
    print(message, flush=True)


# ============================================================
# TIME
# ============================================================

def utc_timestamp(date_string):

    dt = datetime.strptime(
        date_string,
        "%Y-%m-%d"
    ).replace(tzinfo=timezone.utc)

    return int(dt.timestamp())


def timestamp_to_iso(ts):

    return datetime.fromtimestamp(
        ts,
        tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")


# ============================================================
# CANDLE NORMALIZER
# ============================================================

def normalize_candle(candle):

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

        # milliseconds -> seconds
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
# KRAKEN REQUEST
# ============================================================

def request_chunk(symbol, start_ts, end_ts):

    url = f"{BASE_URL}/{symbol}/{TIMEFRAME}"

    params = {
        "from": start_ts,
        "to": end_ts,
        "count": CHUNK_SIZE,
    }

    log(
        f"[HTTP] {symbol} "
        f"{timestamp_to_iso(start_ts)} -> "
        f"{timestamp_to_iso(end_ts)}"
    )

    last_error = None

    for attempt in range(1, REQUEST_RETRIES + 1):

        try:

            response = requests.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            log(
                f"[HTTP] status={response.status_code}"
            )

            response.raise_for_status()

            data = response.json()

            if not isinstance(data, dict):

                raise RuntimeError(
                    "Unexpected API response type"
                )

            candles = data.get("candles", [])

            if not isinstance(candles, list):

                raise RuntimeError(
                    "Invalid candles field"
                )

            log(
                f"[HTTP] candles={len(candles)}"
            )

            return candles

        except Exception as exc:

            last_error = exc

            log(
                f"[WARN] Attempt "
                f"{attempt}/{REQUEST_RETRIES}: "
                f"{exc}"
            )

            if attempt < REQUEST_RETRIES:

                time.sleep(2 * attempt)

    raise RuntimeError(
        f"Kraken request failed: {last_error}"
    )


# ============================================================
# DOWNLOAD SYMBOL
# ============================================================

def download_symbol(symbol):

    log()
    log("=" * 70)
    log("STARTING HISTORICAL DOWNLOAD")
    log("=" * 70)

    start_ts = utc_timestamp(START_DATE)
    end_ts = utc_timestamp(END_DATE)

    candle_seconds = 300

    current_ts = start_ts

    all_candles = {}

    request_number = 0

    log(f"Symbol       : {symbol}")
    log(f"Timeframe    : {TIMEFRAME}")
    log(f"Start        : {timestamp_to_iso(start_ts)}")
    log(f"End          : {timestamp_to_iso(end_ts)}")
    log(f"Chunk        : {CHUNK_SIZE}")
    log()

    while current_ts < end_ts:

        request_number += 1

        request_end = min(
            current_ts +
            CHUNK_SIZE * candle_seconds,
            end_ts
        )

        log(
            f"[CHUNK {request_number}] "
            f"{timestamp_to_iso(current_ts)} -> "
            f"{timestamp_to_iso(request_end)}"
        )

        raw_candles = request_chunk(
            symbol,
            current_ts,
            request_end
        )

        valid = 0

        for candle in raw_candles:

            item = normalize_candle(candle)

            if item is None:
                continue

            ts = item["time"]

            if ts < start_ts:
                continue

            if ts >= end_ts:
                continue

            all_candles[ts] = item

            valid += 1

        log(
            f"[CHUNK {request_number}] "
            f"valid={valid} "
            f"total={len(all_candles)}"
        )

        # ----------------------------------------------------
        # Move forward
        # ----------------------------------------------------

        if raw_candles:

            normalized_times = []

            for candle in raw_candles:

                item = normalize_candle(candle)

                if item is not None:
                    normalized_times.append(
                        item["time"]
                    )

            if normalized_times:

                newest_ts = max(
                    normalized_times
                )

                next_ts = (
                    newest_ts +
                    candle_seconds
                )

                if next_ts <= current_ts:

                    next_ts = (
                        current_ts +
                        CHUNK_SIZE *
                        candle_seconds
                    )

                current_ts = next_ts

            else:

                current_ts = request_end

        else:

            log(
                "[WARN] Kraken returned ZERO candles"
            )

            current_ts = request_end

        # Progress
        elapsed = end_ts - start_ts
        done = current_ts - start_ts

        progress = min(
            100.0,
            max(
                0.0,
                done / elapsed * 100
            )
        )

        log(
            f"[PROGRESS] "
            f"{progress:.2f}% | "
            f"candles={len(all_candles)}"
        )

        time.sleep(REQUEST_SLEEP)

    # ========================================================
    # SORT
    # ========================================================

    candles = sorted(
        all_candles.values(),
        key=lambda x: x["time"]
    )

    # ========================================================
    # REMOVE FUTURE / INCOMPLETE
    # ========================================================

    now_ts = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    candles = [
        c for c in candles
        if c["time"] + candle_seconds <= now_ts
    ]

    # ========================================================
    # EXPECTED
    # ========================================================

    expected = int(
        (end_ts - start_ts)
        / candle_seconds
    )

    actual = len(candles)

    coverage = (
        actual / expected * 100
        if expected
        else 0
    )

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
                "missing_seconds":
                    diff - candle_seconds
            })

    # ========================================================
    # RESULT
    # ========================================================

    log()
    log("=" * 70)
    log("DOWNLOAD COMPLETE")
    log("=" * 70)

    log(f"Symbol           : {symbol}")
    log(f"Expected candles : {expected:,}")
    log(f"Downloaded       : {actual:,}")
    log(f"Coverage         : {coverage:.2f}%")
    log(f"Gaps             : {len(gaps):,}")

    if candles:

        log(
            "First candle     : "
            f"{timestamp_to_iso(candles[0]['time'])}"
        )

        log(
            "Last candle      : "
            f"{timestamp_to_iso(candles[-1]['time'])}"
        )

    else:

        log("First candle     : NONE")
        log("Last candle      : NONE")

    # ========================================================
    # SAVE
    # ========================================================

    os.makedirs(
        DATA_DIR,
        exist_ok=True
    )

    output_file = os.path.join(
        DATA_DIR,
        f"{symbol}_{TIMEFRAME}.json"
    )

    result = {
        "symbol": symbol,
        "timeframe": TIMEFRAME,
        "start": START_DATE,
        "end": END_DATE,
        "downloaded_at":
            datetime.now(
                timezone.utc
            ).isoformat(),
        "expected_candles": expected,
        "actual_candles": actual,
        "coverage_percent": coverage,
        "gap_count": len(gaps),
        "gaps": gaps[:100],
        "candles": candles,
    }

    log()
    log("[SAVE] Writing JSON...")

    with open(
        output_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            result,
            f,
            ensure_ascii=False
        )

    log(
        f"[SAVE] {output_file}"
    )

    return result


# ============================================================
# MAIN
# ============================================================

def main():

    log()
    log("=" * 70)
    log("KRAKEN FUTURES ICHIMOKU BACKTEST")
    log("STAGE 1 - HISTORICAL DATA TEST")
    log("=" * 70)

    log(
        f"Python version: "
        f"{sys.version.split()[0]}"
    )

    log(
        f"Started: "
        f"{datetime.now(timezone.utc).isoformat()}"
    )

    log()
    log(f"TEST SYMBOL : {TEST_SYMBOL}")
    log(f"TIMEFRAME   : {TIMEFRAME}")
    log(f"START       : {START_DATE}")
    log(f"END         : {END_DATE}")

    # ========================================================
    # INTERNET / KRAKEN TEST
    # ========================================================

    log()
    log("[TEST] Connecting to Kraken...")

    try:

        response = requests.get(
            "https://futures.kraken.com",
            timeout=REQUEST_TIMEOUT
        )

        log(
            f"[TEST] Kraken HTTP "
            f"status={response.status_code}"
        )

    except Exception as exc:

        log(
            f"[TEST ERROR] "
            f"Cannot connect to Kraken: {exc}"
        )

        return 1

    # ========================================================
    # DOWNLOAD
    # ========================================================

    try:

        result = download_symbol(
            TEST_SYMBOL
        )

        if result["actual_candles"] == 0:

            log()
            log(
                "[ERROR] ZERO CANDLES DOWNLOADED"
            )

            return 1

        log()
        log("=" * 70)
        log("STAGE 1 SUCCESS")
        log("=" * 70)

        return 0

    except KeyboardInterrupt:

        log()
        log("[STOPPED] Interrupted by user.")

        return 1

    except Exception as exc:

        log()
        log("=" * 70)
        log("STAGE 1 FAILED")
        log("=" * 70)

        log(
            f"ERROR TYPE: "
            f"{type(exc).__name__}"
        )

        log(
            f"ERROR: {exc}"
        )

        return 1


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    exit_code = main()

    log()
    log(
        f"PROCESS EXIT CODE: {exit_code}"
    )

    sys.stdout.flush()
    sys.stderr.flush()

    raise SystemExit(exit_code)
