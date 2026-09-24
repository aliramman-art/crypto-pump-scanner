# ============================================================
# KRAKEN FUTURES BTC HEDGE BACKTEST
# VERSION 2.2
# ============================================================
#
# PAPER / BACKTEST ONLY
#
# CAPITAL:
#   $2.00
#
# LEVERAGE:
#   10x
#
# INITIAL POSITION:
#   $10 notional
#
# HEDGE:
#   Trigger after 2% adverse movement
#   Maximum 2 hedges
#
# MODELS:
#   A_50  = 50% of original position
#   B_75  = 75% of original position
#   C_100 = 100% of original position
#
# COSTS:
#   Taker fee
#   Slippage
#   Funding
#
# IMPORTANT:
#   This is a mechanism backtest.
#   LONG and SHORT are tested independently.
#   No overlapping cycles.
#
# CONSERVATIVE INTRABAR RULE:
#   If liquidation and hedge trigger are both inside
#   the same 5m candle, liquidation wins.
#
# TARGET:
#   +$0.20 net equity per cycle
#   = +10% of starting $2
#
# ============================================================

import csv
import math
import os
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta

import requests


# ============================================================
# CONFIG
# ============================================================

VERSION = "2.2"

REAL_TRADING = False
PAPER_TRADING = True

SYMBOL = "PF_XBTUSD"
TIMEFRAME = "5m"

LOOKBACK_DAYS = 182

CAPITAL_START = 2.00

LEVERAGE = 10.0

INITIAL_NOTIONAL = 10.00
INITIAL_MARGIN = INITIAL_NOTIONAL / LEVERAGE

HEDGE_TRIGGER_PCT = 0.02

MAX_HEDGES = 2

HEDGE_MODELS = {
    "A_50": 0.50,
    "B_75": 0.75,
    "C_100": 1.00,
}

TARGET_NET_PROFIT = 0.20

# Model assumptions.
# These are NOT claimed to be the user's actual account fee tier.
TAKER_FEE_RATE = 0.0005
SLIPPAGE_RATE = 0.0002

# Conservative liquidation buffer.
LIQUIDATION_BUFFER_USD = 0.0

# Kraken maintenance margin used if instrument endpoint
# does not provide a usable value.
DEFAULT_MAINTENANCE_MARGIN_RATE = 0.005

# Candle duration.
CANDLE_SECONDS = 300

# Kraken candle endpoint supports a finite number of rows.
CHUNK_CANDLES = 1000

# HTTP settings.
REQUEST_TIMEOUT = 30
MAX_RETRIES = 5
RETRY_SLEEP = 2.0

# Funding analytics interval.
# Kraken supports 60,300,900,1800,3600,14400,43200,86400,604800.
FUNDING_INTERVAL_SECONDS = 14400

# Outputs.
TRADES_CSV = "btc_hedge_trades.csv"
SUMMARY_CSV = "btc_hedge_summary.csv"
EQUITY_CSV = "btc_hedge_equity.csv"
FUNDING_CSV = "btc_hedge_funding.csv"


# ============================================================
# URLS
# ============================================================

KRAKEN_BASE = "https://futures.kraken.com"

CANDLES_URL = (
    KRAKEN_BASE
    + "/api/charts/v1/trade/"
    + SYMBOL
    + "/"
    + TIMEFRAME
)

HISTORICAL_FUNDING_URL = (
    KRAKEN_BASE
    + "/derivatives/api/v3/historical-funding-rates"
)

ANALYTICS_FUNDING_URL = (
    KRAKEN_BASE
    + "/api/charts/v1/analytics/"
    + SYMBOL
    + "/funding"
)

INSTRUMENTS_URL = (
    KRAKEN_BASE
    + "/derivatives/api/v3/instruments"
)


# ============================================================
# GLOBAL SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(compatible; BTC-Hedge-Backtest/2.2)"
        ),
        "Accept": "application/json",
    }
)


# ============================================================
# LOGGING
# ============================================================

def log(message=""):
    now = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )
    print(f"[{now}] {message}", flush=True)


# ============================================================
# HTTP
# ============================================================

def request_json(
    url,
    params=None,
    allow_empty=False,
):
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code == 200:

                data = response.json()

                if data is None:
                    if allow_empty:
                        return {}
                    raise RuntimeError(
                        f"Empty JSON response from {url}"
                    )

                return data

            body = response.text[:500]

            last_error = RuntimeError(
                f"HTTP {response.status_code}: {body}"
            )

            log(
                f"HTTP error {response.status_code} "
                f"(attempt {attempt}/{MAX_RETRIES})"
            )

        except Exception as exc:

            last_error = exc

            log(
                f"Request error "
                f"(attempt {attempt}/{MAX_RETRIES}): "
                f"{exc}"
            )

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_SLEEP * attempt)

    raise last_error


# ============================================================
# TIME HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def dt_to_ts(dt):
    return int(dt.timestamp())


def ts_to_dt(ts):
    return datetime.fromtimestamp(
        float(ts),
        tz=timezone.utc,
    )


def parse_timestamp(value):

    if value is None:
        return None

    if isinstance(value, (int, float)):

        x = float(value)

        if x > 1_000_000_000_000:
            x /= 1000.0

        return int(x)

    text_value = str(value).strip()

    if not text_value:
        return None

    try:
        numeric = float(text_value)

        if numeric > 1_000_000_000_000:
            numeric /= 1000.0

        return int(numeric)

    except Exception:
        pass

    text_value = text_value.replace(
        "Z",
        "+00:00",
    )

    try:

        dt = datetime.fromisoformat(
            text_value
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return int(dt.timestamp())

    except Exception:
        return None


# ============================================================
# CANDLE PARSING
# ============================================================

def parse_candle(row):

    if isinstance(row, dict):

        timestamp = (
            row.get("time")
            or row.get("timestamp")
            or row.get("ts")
        )

        open_price = (
            row.get("open")
            or row.get("o")
        )

        high_price = (
            row.get("high")
            or row.get("h")
        )

        low_price = (
            row.get("low")
            or row.get("l")
        )

        close_price = (
            row.get("close")
            or row.get("c")
        )

        volume = (
            row.get("volume")
            or row.get("v")
            or 0
        )

    elif isinstance(row, (list, tuple)):

        if len(row) < 5:
            return None

        timestamp = row[0]
        open_price = row[1]
        high_price = row[2]
        low_price = row[3]
        close_price = row[4]

        volume = (
            row[5]
            if len(row) > 5
            else 0
        )

    else:
        return None

    ts = parse_timestamp(timestamp)

    if ts is None:
        return None

    try:

        o = float(open_price)
        h = float(high_price)
        l = float(low_price)
        c = float(close_price)
        v = float(volume or 0)

    except Exception:
        return None

    if not all(
        math.isfinite(x)
        for x in (o, h, l, c, v)
    ):
        return None

    if h < l:
        return None

    return {
        "timestamp": ts,
        "datetime": ts_to_dt(ts),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
    }


# ============================================================
# CANDLE RESPONSE EXTRACTION
# ============================================================

def extract_candle_rows(data):

    if not isinstance(data, dict):
        return []

    candidates = [
        data.get("candles"),
        data.get("data"),
        data.get("results"),
    ]

    result = data.get("result")

    if isinstance(result, dict):

        candidates.extend(
            [
                result.get("candles"),
                result.get("data"),
                result.get("results"),
            ]
        )

    for candidate in candidates:

        if isinstance(candidate, list):
            return candidate

    return []


# ============================================================
# DOWNLOAD CANDLES
# ============================================================

def download_candles():

    end_dt = utc_now()

    start_dt = (
        end_dt
        - timedelta(days=LOOKBACK_DAYS)
    )

    start_ts = dt_to_ts(start_dt)
    end_ts = dt_to_ts(end_dt)

    log(
        f"Downloading Kraken {TIMEFRAME} candles..."
    )

    candles_by_ts = {}

    cursor = start_ts

    chunk_seconds = (
        CHUNK_CANDLES
        * CANDLE_SECONDS
    )

    while cursor < end_ts:

        chunk_end = min(
            cursor + chunk_seconds,
            end_ts,
        )

        params = {
            "from": cursor,
            "to": chunk_end,
        }

        data = request_json(
            CANDLES_URL,
            params=params,
        )

        rows = extract_candle_rows(data)

        parsed_count = 0

        for row in rows:

            candle = parse_candle(row)

            if candle is None:
                continue

            ts = candle["timestamp"]

            if start_ts <= ts < end_ts:
                candles_by_ts[ts] = candle
                parsed_count += 1

        if candles_by_ts:

            latest_ts = max(
                candles_by_ts.keys()
            )

            log(
                "Candles downloaded: "
                f"{len(candles_by_ts):,} | "
                f"latest={ts_to_dt(latest_ts).isoformat()}"
            )

        else:

            log(
                "Candles downloaded: 0"
            )

        if parsed_count == 0:

            # Move forward even if the endpoint
            # unexpectedly returns no rows.
            cursor = chunk_end

        else:

            cursor = chunk_end

        time.sleep(0.05)

    candles = [
        candles_by_ts[k]
        for k in sorted(candles_by_ts)
    ]

    # Only closed candles.
    now_ts = dt_to_ts(utc_now())

    closed_cutoff = (
        now_ts
        - (
            now_ts
            % CANDLE_SECONDS
        )
    )

    candles = [
        c
        for c in candles
        if (
            c["timestamp"]
            + CANDLE_SECONDS
            <= closed_cutoff
        )
    ]

    if not candles:
        raise RuntimeError(
            "No closed candles downloaded."
        )

    log(
        f"Final closed candles: {len(candles):,}"
    )

    log(
        "Range: "
        f"{candles[0]['datetime'].isoformat()}"
        " -> "
        f"{candles[-1]['datetime'].isoformat()}"
    )

    return candles


# ============================================================
# INSTRUMENT INFO
# ============================================================

def load_instrument():

    log("Loading Kraken instrument information...")

    data = request_json(
        INSTRUMENTS_URL
    )

    instruments = []

    if isinstance(data, dict):

        instruments = (
            data.get("instruments")
            or data.get("result", {}).get(
                "instruments",
                []
            )
        )

    if not isinstance(instruments, list):
        instruments = []

    target = None

    for item in instruments:

        if not isinstance(item, dict):
            continue

        symbol = str(
            item.get("symbol", "")
        ).upper()

        if symbol == SYMBOL.upper():
            target = item
            break

    maintenance_rate = (
        DEFAULT_MAINTENANCE_MARGIN_RATE
    )

    instrument_type = "unknown"
    tradeable = None
    reference_initial_margin = None

    if target:

        instrument_type = (
            target.get("type")
            or target.get("contractType")
            or "unknown"
        )

        tradeable = target.get(
            "tradeable"
        )

        possible_maintenance = [
            target.get("maintenanceMarginRate"),
            target.get("maintenanceMargin"),
            target.get("mm"),
        ]

        for value in possible_maintenance:

            try:

                x = float(value)

                if 0 < x < 1:
                    maintenance_rate = x
                    break

            except Exception:
                pass

        possible_initial = [
            target.get("initialMarginRate"),
            target.get("initialMargin"),
        ]

        for value in possible_initial:

            try:

                x = float(value)

                if 0 < x < 1:
                    reference_initial_margin = x
                    break

            except Exception:
                pass

    log(
        f"Instrument: {SYMBOL}"
    )

    log(
        f"Type: {instrument_type}"
    )

    log(
        f"Tradeable: {tradeable}"
    )

    log(
        "Maintenance margin: "
        f"{maintenance_rate:.4%}"
    )

    if reference_initial_margin is not None:

        log(
            "Exchange reference initial margin: "
            f"{reference_initial_margin:.4%}"
        )

    return {
        "symbol": SYMBOL,
        "type": instrument_type,
        "tradeable": tradeable,
        "maintenance_margin_rate": maintenance_rate,
        "reference_initial_margin": (
            reference_initial_margin
        ),
    }


# ============================================================
# FUNDING HELPERS
# ============================================================

def normalize_funding_rate(value):

    try:

        x = float(value)

    except Exception:
        return None

    if not math.isfinite(x):
        return None

    return x


def extract_funding_records_from_standard(data):

    if not isinstance(data, dict):
        return []

    candidates = []

    candidates.append(
        data.get("rates")
    )

    result = data.get("result")

    if isinstance(result, dict):
        candidates.append(
            result.get("rates")
        )

    for candidate in candidates:

        if isinstance(candidate, list):

            records = []

            for item in candidate:

                if not isinstance(item, dict):
                    continue

                ts = parse_timestamp(
                    item.get("timestamp")
                    or item.get("time")
                )

                rate = normalize_funding_rate(
                    item.get("fundingRate")
                    if item.get("fundingRate")
                    is not None
                    else item.get("relativeFundingRate")
                )

                # Prefer fundingRate when available.
                if (
                    item.get("fundingRate")
                    is not None
                ):
                    rate = normalize_funding_rate(
                        item.get("fundingRate")
                    )

                if ts is None or rate is None:
                    continue

                records.append(
                    {
                        "timestamp": ts,
                        "rate": rate,
                        "source": "historical",
                    }
                )

            return records

    return []


def extract_funding_records_from_analytics(data):

    if not isinstance(data, dict):
        return []

    root = data

    if isinstance(
        data.get("result"),
        dict,
    ):
        root = data["result"]

    timestamps = (
        root.get("timestamp")
        or root.get("timestamps")
        or []
    )

    payload = root.get("data")

    if isinstance(payload, dict):
        rate_values = (
            payload.get("rate")
            or payload.get("fundingRate")
            or payload.get("funding_rate")
            or []
        )

        if not timestamps:
            timestamps = (
                payload.get("timestamp")
                or payload.get("timestamps")
                or []
            )

    else:
        rate_values = (
            root.get("rate")
            or root.get("fundingRate")
            or []
        )

    if not isinstance(timestamps, list):
        return []

    if not isinstance(rate_values, list):
        return []

    records = []

    for ts_value, rate_value in zip(
        timestamps,
        rate_values,
    ):

        ts = parse_timestamp(
            ts_value
        )

        rate = normalize_funding_rate(
            rate_value
        )

        if ts is None or rate is None:
            continue

        records.append(
            {
                "timestamp": ts,
                "rate": rate,
                "source": "analytics",
            }
        )

    return records


def download_funding():

    log(
        "Downloading Kraken historical funding rates..."
    )

    end_ts = dt_to_ts(
        utc_now()
    )

    start_ts = dt_to_ts(
        utc_now()
        - timedelta(days=LOOKBACK_DAYS)
    )

    records = []

    # --------------------------------------------------------
    # Method 1:
    # Official historical funding endpoint.
    # --------------------------------------------------------

    try:

        data = request_json(
            HISTORICAL_FUNDING_URL,
            params={
                "symbol": SYMBOL,
            },
            allow_empty=True,
        )

        records = (
            extract_funding_records_from_standard(
                data
            )
        )

        if records:

            log(
                "Historical funding endpoint returned "
                f"{len(records):,} rows."
            )

    except Exception as exc:

        log(
            "Historical funding endpoint failed: "
            f"{exc}"
        )

    # --------------------------------------------------------
    # Method 2:
    # Official Kraken Market Analytics funding endpoint.
    #
    # This is used if the historical endpoint returns no rows.
    # --------------------------------------------------------

    if not records:

        log(
            "Historical funding returned no usable rows."
        )

        log(
            "Trying Kraken Market Analytics funding..."
        )

        try:

            data = request_json(
                ANALYTICS_FUNDING_URL,
                params={
                    "since": start_ts,
                    "to": end_ts,
                    "interval": FUNDING_INTERVAL_SECONDS,
                },
                allow_empty=True,
            )

            records = (
                extract_funding_records_from_analytics(
                    data
                )
            )

            if records:

                log(
                    "Analytics funding returned "
                    f"{len(records):,} rows."
                )

        except Exception as exc:

            log(
                "Analytics funding failed: "
                f"{exc}"
            )

    # --------------------------------------------------------
    # Filter to backtest range.
    # --------------------------------------------------------

    filtered = []

    for row in records:

        ts = row["timestamp"]

        if start_ts <= ts <= end_ts:

            filtered.append(
                row
            )

    # Deduplicate.
    dedup = {}

    for row in filtered:

        dedup[
            row["timestamp"]
        ] = row

    funding = [
        dedup[k]
        for k in sorted(dedup)
    ]

    if not funding:

        log(
            "WARNING: Kraken returned no usable "
            "funding rows for the requested period."
        )

        log(
            "The backtest will continue with "
            "funding_cost = 0 only because no "
            "Kraken funding data was available."
        )

    else:

        log(
            f"Final funding rows: {len(funding):,}"
        )

        log(
            "Funding range: "
            f"{ts_to_dt(funding[0]['timestamp']).isoformat()}"
            " -> "
            f"{ts_to_dt(funding[-1]['timestamp']).isoformat()}"
        )

    return funding


# ============================================================
# FUNDING LOOKUP
# ============================================================

def build_funding_lookup(funding):

    return {
        int(row["timestamp"]): row
        for row in funding
    }


def get_funding_events_between(
    funding,
    start_ts,
    end_ts,
):

    return [
        row
        for row in funding
        if (
            start_ts
            < row["timestamp"]
            <= end_ts
        )
    ]


# ============================================================
# PRICE LOOKUP FOR FUNDING
# ============================================================

def price_before_timestamp(
    candles,
    timestamp,
):

    # Binary search would be faster, but the funding list
    # is tiny compared with the candle list. This helper is
    # intentionally simple and deterministic.

    lo = 0
    hi = len(candles) - 1

    answer = None

    while lo <= hi:

        mid = (
            lo + hi
        ) // 2

        ts = candles[mid]["timestamp"]

        if ts <= timestamp:

            answer = candles[mid]["close"]
            lo = mid + 1

        else:

            hi = mid - 1

    return answer


# ============================================================
# POSITION HELPERS
# ============================================================

def position_notional(qty, price):
    return abs(qty) * price


def position_direction(qty):

    if qty > 0:
        return "LONG"

    if qty < 0:
        return "SHORT"

    return "FLAT"


def apply_slippage(
    price,
    side,
):

    if side == "BUY":
        return price * (
            1.0 + SLIPPAGE_RATE
        )

    return price * (
        1.0 - SLIPPAGE_RATE
    )


def execution_fee(
    notional,
):

    return abs(notional) * TAKER_FEE_RATE


def signed_pnl(
    qty,
    entry_price,
    exit_price,
):

    return qty * (
        exit_price - entry_price
    )


# ============================================================
# EQUITY
# ============================================================

def unrealized_pnl(
    positions,
    price,
):

    total = 0.0

    for pos in positions:

        total += signed_pnl(
            pos["qty"],
            pos["entry_price"],
            price,
        )

    return total


def total_open_notional(
    positions,
    price,
):

    return sum(
        abs(pos["qty"]) * price
        for pos in positions
    )


def current_margin_requirement(
    positions,
):

    total_notional = sum(
        abs(pos["notional"])
        for pos in positions
    )

    return (
        total_notional
        / LEVERAGE
    )


def current_equity(
    cash,
    positions,
    price,
):

    return (
        cash
        + unrealized_pnl(
            positions,
            price,
        )
    )


# ============================================================
# LIQUIDATION
# ============================================================

def approximate_liquidation_price(
    cash,
    positions,
    maintenance_rate,
):

    if not positions:
        return None

    total_qty = sum(
        pos["qty"]
        for pos in positions
    )

    total_abs_qty = sum(
        abs(pos["qty"])
        for pos in positions
    )

    if total_abs_qty <= 0:
        return None

    # Weighted average entry based on absolute quantity.
    weighted_entry = (
        sum(
            abs(pos["qty"])
            * pos["entry_price"]
            for pos in positions
        )
        / total_abs_qty
    )

    net_qty = total_qty

    if abs(net_qty) < 1e-12:
        return None

    # Cross-margin approximation:
    #
    # equity(price)
    # =
    # cash + net_qty * (price - weighted_entry)
    #
    # liquidation when:
    #
    # equity <= maintenance_rate *
    #           abs(net_qty) * price
    #
    # Solve approximately for price.
    #
    # This is deliberately conservative and is NOT
    # Kraken's exact liquidation engine.

    a = (
        net_qty
        - (
            maintenance_rate
            * abs(net_qty)
        )
    )

    b = (
        cash
        - (
            net_qty
            * weighted_entry
        )
    )

    if abs(a) < 1e-12:
        return None

    liq = -b / a

    if liq <= 0:
        return None

    if net_qty > 0:
        # Long liquidation below current area.
        return liq

    # Short liquidation above current area.
    return liq


def is_liquidation_hit(
    candle,
    liq_price,
    net_qty,
):

    if liq_price is None:
        return False

    if net_qty > 0:

        return (
            candle["low"]
            <= liq_price
        )

    if net_qty < 0:

        return (
            candle["high"]
            >= liq_price
        )

    return False


# ============================================================
# DRAW DOWN
# ============================================================

def update_drawdown(
    cycle,
    equity,
):

    peak = cycle.get(
        "peak_equity"
    )

    if peak is None:
        peak = equity

    if equity > peak:
        peak = equity

    cycle["peak_equity"] = peak

    dd = equity - peak

    if dd < cycle["max_drawdown"]:
        cycle["max_drawdown"] = dd


# ============================================================
# TARGET CHECK
# ============================================================

def target_reached(
    cash,
    positions,
    price,
):

    equity = current_equity(
        cash,
        positions,
        price,
    )

    return (
        equity
        >= CAPITAL_START
        + TARGET_NET_PROFIT
    )


# ============================================================
# OPEN POSITION
# ============================================================

def create_position(
    qty,
    raw_price,
    timestamp,
    label,
):

    side = (
        "BUY"
        if qty > 0
        else "SELL"
    )

    execution_price = apply_slippage(
        raw_price,
        side,
    )

    notional = (
        abs(qty)
        * execution_price
    )

    fee = execution_fee(
        notional
    )

    return {
        "label": label,
        "qty": qty,
        "entry_price": execution_price,
        "raw_price": raw_price,
        "entry_timestamp": timestamp,
        "notional": notional,
        "entry_fee": fee,
    }


# ============================================================
# CLOSE ALL POSITIONS
# ============================================================

def close_all_positions(
    positions,
    raw_price,
    timestamp,
):

    total_pnl = 0.0
    total_fee = 0.0
    total_slippage = 0.0

    fills = []

    for pos in positions:

        qty = pos["qty"]

        side = (
            "SELL"
            if qty > 0
            else "BUY"
        )

        exit_price = apply_slippage(
            raw_price,
            side,
        )

        pnl = signed_pnl(
            qty,
            pos["entry_price"],
            exit_price,
        )

        fee = execution_fee(
            abs(qty)
            * exit_price
        )

        # Slippage cost relative to raw market price.
        slippage_cost = (
            abs(qty)
            * abs(
                exit_price
                - raw_price
            )
        )

        total_pnl += pnl
        total_fee += fee
        total_slippage += slippage_cost

        fills.append(
            {
                "label": pos["label"],
                "qty": qty,
                "entry_price": pos["entry_price"],
                "exit_price": exit_price,
                "pnl": pnl,
                "fee": fee,
                "slippage": slippage_cost,
            }
        )

    return (
        total_pnl,
        total_fee,
        total_slippage,
        fills,
    )


# ============================================================
# FUNDING APPLICATION
# ============================================================

def apply_funding_events(
    cash,
    positions,
    funding,
    candles,
    last_funding_ts,
    current_ts,
    cycle,
):

    if not positions:
        return (
            cash,
            last_funding_ts,
        )

    events = get_funding_events_between(
        funding,
        last_funding_ts,
        current_ts,
    )

    for event in events:

        event_ts = event["timestamp"]

        # Do not apply the same funding event twice.
        if event_ts <= last_funding_ts:
            continue

        funding_price = price_before_timestamp(
            candles,
            event_ts,
        )

        if funding_price is None:
            funding_price = positions[0][
                "entry_price"
            ]

        rate = event["rate"]

        for pos in positions:

            notional = (
                abs(pos["qty"])
                * funding_price
            )

            # Positive funding:
            # long pays, short receives.
            #
            # Negative funding:
            # short pays, long receives.
            payment = (
                pos["qty"]
                * funding_price
                * rate
            )

            cash -= payment

            cycle["funding_total"] += (
                -payment
            )

            cycle["funding_events"] += 1

            cycle["funding_records"].append(
                {
                    "timestamp": event_ts,
                    "datetime": ts_to_dt(
                        event_ts
                    ).isoformat(),
                    "rate": rate,
                    "position_label": pos["label"],
                    "qty": pos["qty"],
                    "price": funding_price,
                    "notional": notional,
                    "payment": payment,
                    "cash_effect": -payment,
                    "source": event.get(
                        "source",
                        "unknown",
                    ),
                }
            )

        last_funding_ts = event_ts

    return (
        cash,
        last_funding_ts,
    )


# ============================================================
# HEDGE SIZE
# ============================================================

def hedge_qty_for_model(
    initial_qty,
    hedge_ratio,
):

    return (
        -initial_qty
        * hedge_ratio
    )


# ============================================================
# CAN HEDGE?
# ============================================================

def can_add_position(
    cash,
    positions,
    new_qty,
    price,
):

    new_notional = (
        abs(new_qty)
        * price
    )

    required_margin = (
        new_notional
        / LEVERAGE
    )

    new_fee = execution_fee(
        new_notional
    )

    existing_margin = (
        current_margin_requirement(
            positions
        )
    )

    total_required = (
        existing_margin
        + required_margin
        + new_fee
    )

    # This is a conservative isolated-style
    # available-cash check.
    return (
        total_required
        <= cash
        + 1e-12
    )


# ============================================================
# CYCLE CREATION
# ============================================================

def create_cycle(
    model_name,
    direction,
    first_candle,
):

    raw_entry = first_candle["close"]

    initial_qty = (
        INITIAL_NOTIONAL
        / raw_entry
    )

    if direction == "LONG":
        initial_qty = abs(initial_qty)
    else:
        initial_qty = -abs(initial_qty)

    position = create_position(
        initial_qty,
        raw_entry,
        first_candle["timestamp"],
        "INITIAL",
    )

    cash = (
        CAPITAL_START
        - position["entry_fee"]
    )

    initial_equity = (
        cash
        + unrealized_pnl(
            [position],
            raw_entry,
        )
    )

    cycle = {
        "model": model_name,
        "direction": direction,

        "start_timestamp": (
            first_candle["timestamp"]
        ),

        "entry_price": (
            position["entry_price"]
        ),

        "entry_raw_price": raw_entry,

        "initial_qty": initial_qty,

        "positions": [position],

        "cash": cash,

        "hedge_count": 0,

        "hedge_prices": [],

        "hedge_sizes": [],

        "gross_pnl": 0.0,

        "fees": position["entry_fee"],

        "slippage": 0.0,

        "funding_total": 0.0,

        "funding_events": 0,

        "funding_records": [],

        "max_drawdown": 0.0,

        "peak_equity": initial_equity,

        "liquidation_count": 0,

        "exit_price": None,

        "exit_raw_price": None,

        "exit_timestamp": None,

        "exit_reason": None,

        "exit_fees": 0.0,

        "exit_slippage": 0.0,

        "duration_seconds": 0,

        "blocked_hedges": 0,

        "target_hit": False,

        "liquidation_price_at_entry": None,
    }

    return cycle


# ============================================================
# CYCLE EXECUTION
# ============================================================

def run_cycle(
    candles,
    start_index,
    model_name,
    hedge_ratio,
    direction,
    funding,
    maintenance_rate,
):

    if start_index >= len(candles):
        return None, len(candles)

    first_candle = candles[
        start_index
    ]

    cycle = create_cycle(
        model_name,
        direction,
        first_candle,
    )

    initial_qty = cycle[
        "initial_qty"
    ]

    first_entry_price = cycle[
        "entry_price"
    ]

    hedge_trigger_price = (
        first_entry_price
        * (
            1.0
            - HEDGE_TRIGGER_PCT
        )
        if direction == "LONG"
        else
        first_entry_price
        * (
            1.0
            + HEDGE_TRIGGER_PCT
        )
    )

    last_funding_ts = (
        first_candle["timestamp"]
    )

    # Start scanning AFTER the entry candle.
    i = start_index + 1

    while i < len(candles):

        candle = candles[i]

        # ----------------------------------------------------
        # Funding
        # ----------------------------------------------------

        (
            cycle["cash"],
            last_funding_ts,
        ) = apply_funding_events(
            cycle["cash"],
            cycle["positions"],
            funding,
            candles,
            last_funding_ts,
            candle["timestamp"],
            cycle,
        )

        # ----------------------------------------------------
        # Mark-to-market at candle open.
        # ----------------------------------------------------

        mark_equity = current_equity(
            cycle["cash"],
            cycle["positions"],
            candle["open"],
        )

        update_drawdown(
            cycle,
            mark_equity,
        )

        # ----------------------------------------------------
        # Current net quantity.
        # ----------------------------------------------------

        net_qty = sum(
            pos["qty"]
            for pos in cycle["positions"]
        )

        # ----------------------------------------------------
        # Approximate liquidation.
        # ----------------------------------------------------

        liq_price = (
            approximate_liquidation_price(
                cycle["cash"],
                cycle["positions"],
                maintenance_rate,
            )
        )

        cycle[
            "liquidation_price_at_entry"
        ] = liq_price

        liquidation_hit = (
            is_liquidation_hit(
                candle,
                liq_price,
                net_qty,
            )
        )

        # ----------------------------------------------------
        # Hedge trigger.
        # ----------------------------------------------------

        hedge_trigger_hit = False

        if cycle["hedge_count"] < MAX_HEDGES:

            if direction == "LONG":

                hedge_trigger_hit = (
                    candle["low"]
                    <= hedge_trigger_price
                )

            else:

                hedge_trigger_hit = (
                    candle["high"]
                    >= hedge_trigger_price
                )

        # ----------------------------------------------------
        # CRITICAL CONSERVATIVE RULE:
        #
        # If liquidation and hedge happen in the
        # same candle, liquidation wins.
        # ----------------------------------------------------

        if liquidation_hit:

            raw_exit = (
                liq_price
                if liq_price is not None
                else candle["close"]
            )

            (
                pnl,
                fee,
                slippage,
                fills,
            ) = close_all_positions(
                cycle["positions"],
                raw_exit,
                candle["timestamp"],
            )

            cycle["gross_pnl"] += pnl
            cycle["fees"] += fee
            cycle["exit_fees"] += fee
            cycle["slippage"] += slippage
            cycle["exit_slippage"] += slippage

            cycle["cash"] += pnl
            cycle["cash"] -= fee

            cycle["liquidation_count"] = 1

            cycle["exit_raw_price"] = raw_exit

            cycle["exit_price"] = (
                fills[-1]["exit_price"]
                if fills
                else raw_exit
            )

            cycle["exit_timestamp"] = (
                candle["timestamp"]
            )

            cycle["exit_reason"] = (
                "LIQUIDATION"
            )

            cycle["duration_seconds"] = (
                candle["timestamp"]
                - cycle["start_timestamp"]
            )

            final_equity = cycle["cash"]

            update_drawdown(
                cycle,
                final_equity,
            )

            return (
                finalize_cycle(
                    cycle
                ),
                i + 1,
            )

        # ----------------------------------------------------
        # HEDGE
        # ----------------------------------------------------

        if hedge_trigger_hit:

            raw_hedge_price = (
                hedge_trigger_price
            )

            hedge_qty = hedge_qty_for_model(
                initial_qty,
                hedge_ratio,
            )

            # For second hedge the same model size is used.
            # This preserves the requested model definition.
            if can_add_position(
                cycle["cash"],
                cycle["positions"],
                hedge_qty,
                raw_hedge_price,
            ):

                hedge_label = (
                    f"HEDGE_{cycle['hedge_count'] + 1}"
                )

                hedge_position = create_position(
                    hedge_qty,
                    raw_hedge_price,
                    candle["timestamp"],
                    hedge_label,
                )

                cycle["cash"] -= (
                    hedge_position["entry_fee"]
                )

                cycle["fees"] += (
                    hedge_position["entry_fee"]
                )

                cycle["hedge_count"] += 1

                cycle["hedge_prices"].append(
                    hedge_position["entry_price"]
                )

                cycle["hedge_sizes"].append(
                    abs(hedge_qty)
                )

                cycle["positions"].append(
                    hedge_position
                )

                # After each hedge, move the next hedge
                # trigger another 2% against the ORIGINAL
                # direction.
                if cycle["hedge_count"] < MAX_HEDGES:

                    if direction == "LONG":

                        hedge_trigger_price = (
                            hedge_trigger_price
                            * (
                                1.0
                                - HEDGE_TRIGGER_PCT
                            )
                        )

                    else:

                        hedge_trigger_price = (
                            hedge_trigger_price
                            * (
                                1.0
                                + HEDGE_TRIGGER_PCT
                            )
                        )

            else:

                cycle["blocked_hedges"] += 1

                # Do not repeatedly attempt the same blocked
                # hedge on every candle.
                cycle["hedge_count"] += 1

        # ----------------------------------------------------
        # Target check.
        #
        # We calculate the hypothetical closing cost at the
        # current candle close. This prevents claiming +10%
        # before closing costs.
        # ----------------------------------------------------

        raw_target_price = candle["close"]

        projected_pnl = unrealized_pnl(
            cycle["positions"],
            raw_target_price,
        )

        projected_exit_fee = execution_fee(
            total_open_notional(
                cycle["positions"],
                raw_target_price,
            )
        )

        projected_exit_slippage = (
            sum(
                abs(pos["qty"])
                * (
                    SLIPPAGE_RATE
                    * raw_target_price
                )
                for pos in cycle["positions"]
            )
        )

        projected_equity = (
            cycle["cash"]
            + projected_pnl
            - projected_exit_fee
            - projected_exit_slippage
        )

        update_drawdown(
            cycle,
            projected_equity,
        )

        if (
            projected_equity
            >= (
                CAPITAL_START
                + TARGET_NET_PROFIT
            )
        ):

            (
                pnl,
                fee,
                slippage,
                fills,
            ) = close_all_positions(
                cycle["positions"],
                raw_target_price,
                candle["timestamp"],
            )

            cycle["gross_pnl"] += pnl
            cycle["fees"] += fee
            cycle["exit_fees"] += fee
            cycle["slippage"] += slippage
            cycle["exit_slippage"] += slippage

            cycle["cash"] += pnl
            cycle["cash"] -= fee

            cycle["target_hit"] = True

            cycle["exit_raw_price"] = (
                raw_target_price
            )

            cycle["exit_price"] = (
                fills[-1]["exit_price"]
                if fills
                else raw_target_price
            )

            cycle["exit_timestamp"] = (
                candle["timestamp"]
            )

            cycle["exit_reason"] = (
                "TARGET"
            )

            cycle["duration_seconds"] = (
                candle["timestamp"]
                - cycle["start_timestamp"]
            )

            final_equity = cycle["cash"]

            update_drawdown(
                cycle,
                final_equity,
            )

            return (
                finalize_cycle(
                    cycle
                ),
                i + 1,
            )

        # ----------------------------------------------------
        # Update drawdown at candle close.
        # ----------------------------------------------------

        close_equity = current_equity(
            cycle["cash"],
            cycle["positions"],
            candle["close"],
        )

        update_drawdown(
            cycle,
            close_equity,
        )

        i += 1

    # ========================================================
    # END OF DATA
    # ========================================================

    final_candle = candles[-1]

    raw_exit = final_candle["close"]

    (
        pnl,
        fee,
        slippage,
        fills,
    ) = close_all_positions(
        cycle["positions"],
        raw_exit,
        final_candle["timestamp"],
    )

    cycle["gross_pnl"] += pnl
    cycle["fees"] += fee
    cycle["exit_fees"] += fee
    cycle["slippage"] += slippage
    cycle["exit_slippage"] += slippage

    cycle["cash"] += pnl
    cycle["cash"] -= fee

    cycle["exit_raw_price"] = raw_exit

    cycle["exit_price"] = (
        fills[-1]["exit_price"]
        if fills
        else raw_exit
    )

    cycle["exit_timestamp"] = (
        final_candle["timestamp"]
    )

    cycle["exit_reason"] = (
        "END_OF_DATA"
    )

    cycle["duration_seconds"] = (
        final_candle["timestamp"]
        - cycle["start_timestamp"]
    )

    update_drawdown(
        cycle,
        cycle["cash"],
    )

    return (
        finalize_cycle(
            cycle
        ),
        len(candles),
    )


# ============================================================
# FINALIZE CYCLE
# ============================================================

def finalize_cycle(
    cycle,
):

    final_equity = cycle["cash"]

    net_pnl = (
        final_equity
        - CAPITAL_START
    )

    net_return_pct = (
        net_pnl
        / CAPITAL_START
        * 100.0
    )

    duration_hours = (
        cycle["duration_seconds"]
        / 3600.0
    )

    cycle["final_equity"] = final_equity
    cycle["net_pnl"] = net_pnl
    cycle["net_return_pct"] = net_return_pct
    cycle["duration_hours"] = duration_hours

    cycle["hedge1_price"] = (
        cycle["hedge_prices"][0]
        if len(cycle["hedge_prices"]) >= 1
        else None
    )

    cycle["hedge2_price"] = (
        cycle["hedge_prices"][1]
        if len(cycle["hedge_prices"]) >= 2
        else None
    )

    cycle["hedge1_size"] = (
        cycle["hedge_sizes"][0]
        if len(cycle["hedge_sizes"]) >= 1
        else None
    )

    cycle["hedge2_size"] = (
        cycle["hedge_sizes"][1]
        if len(cycle["hedge_sizes"]) >= 2
        else None
    )

    # Liquidation distance at entry.
    liq_price = cycle.get(
        "liquidation_price_at_entry"
    )

    entry_price = cycle[
        "entry_price"
    ]

    if (
        liq_price is not None
        and entry_price > 0
    ):

        cycle["liquidation_distance_pct"] = (
            abs(
                liq_price
                - entry_price
            )
            / entry_price
            * 100.0
        )

    else:

        cycle["liquidation_distance_pct"] = None

    return cycle


# ============================================================
# SCENARIO RUNNER
# ============================================================

def run_scenario(
    candles,
    model_name,
    hedge_ratio,
    direction,
    funding,
    maintenance_rate,
):

    log("")
    log("=" * 70)
    log(
        f"SCENARIO: {model_name} | {direction}"
    )
    log("=" * 70)

    cycles = []

    index = 0

    while index < len(candles) - 1:

        result, next_index = run_cycle(
            candles=candles,
            start_index=index,
            model_name=model_name,
            hedge_ratio=hedge_ratio,
            direction=direction,
            funding=funding,
            maintenance_rate=maintenance_rate,
        )

        if result is None:
            break

        cycles.append(result)

        if next_index <= index:
            next_index = index + 1

        index = next_index

    return cycles


# ============================================================
# SUMMARY
# ============================================================

def summarize_scenario(
    cycles,
    model_name,
    direction,
):

    if not cycles:

        return {
            "model": model_name,
            "direction": direction,
            "cycles": 0,
            "successful_cycles": 0,
            "success_pct": 0.0,
            "average_profit": 0.0,
            "average_return_pct": 0.0,
            "max_loss": 0.0,
            "max_drawdown": 0.0,
            "liquidations": 0,
            "target_hits": 0,
            "end_of_data": 0,
            "blocked_hedges": 0,
            "final_equity": CAPITAL_START,
            "final_return_pct": 0.0,
            "total_net_pnl": 0.0,
            "total_fees": 0.0,
            "total_slippage": 0.0,
            "total_funding": 0.0,
            "funding_events": 0,
        }

    profits = [
        c["net_pnl"]
        for c in cycles
    ]

    returns = [
        c["net_return_pct"]
        for c in cycles
    ]

    successful = [
        c
        for c in cycles
        if c["net_pnl"] > 0
    ]

    liquidations = sum(
        1
        for c in cycles
        if c["exit_reason"]
        == "LIQUIDATION"
    )

    targets = sum(
        1
        for c in cycles
        if c["exit_reason"]
        == "TARGET"
    )

    end_data = sum(
        1
        for c in cycles
        if c["exit_reason"]
        == "END_OF_DATA"
    )

    total_net_pnl = sum(
        profits
    )

    final_equity = (
        CAPITAL_START
        + total_net_pnl
    )

    return {
        "model": model_name,
        "direction": direction,

        "cycles": len(cycles),

        "successful_cycles": len(
            successful
        ),

        "success_pct": (
            len(successful)
            / len(cycles)
            * 100.0
        ),

        "average_profit": (
            sum(profits)
            / len(profits)
        ),

        "average_return_pct": (
            sum(returns)
            / len(returns)
        ),

        "max_loss": min(
            profits
        ),

        "max_drawdown": min(
            c["max_drawdown"]
            for c in cycles
        ),

        "liquidations": liquidations,

        "target_hits": targets,

        "end_of_data": end_data,

        "blocked_hedges": sum(
            c["blocked_hedges"]
            for c in cycles
        ),

        "final_equity": final_equity,

        "final_return_pct": (
            (
                final_equity
                - CAPITAL_START
            )
            / CAPITAL_START
            * 100.0
        ),

        "total_net_pnl": total_net_pnl,

        "total_fees": sum(
            c["fees"]
            for c in cycles
        ),

        "total_slippage": sum(
            c["slippage"]
            for c in cycles
        ),

        "total_funding": sum(
            c["funding_total"]
            for c in cycles
        ),

        "funding_events": sum(
            c["funding_events"]
            for c in cycles
        ),
    }


# ============================================================
# CSV: TRADES
# ============================================================

def write_trades_csv(
    all_cycles
):

    fields = [
        "model",
        "direction",
        "start_datetime",
        "entry_price",
        "entry_raw_price",
        "hedge1_price",
        "hedge1_size",
        "hedge2_price",
        "hedge2_size",
        "hedge_count",
        "blocked_hedges",
        "exit_datetime",
        "exit_price",
        "exit_raw_price",
        "exit_reason",
        "duration_hours",
        "gross_pnl",
        "fees",
        "slippage",
        "funding_total",
        "net_pnl",
        "net_return_pct",
        "max_drawdown",
        "liquidation_price_at_entry",
        "liquidation_distance_pct",
        "target_hit",
    ]

    with open(
        TRADES_CSV,
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )

        writer.writeheader()

        for c in all_cycles:

            writer.writerow(
                {
                    "model": c["model"],
                    "direction": c["direction"],

                    "start_datetime": (
                        ts_to_dt(
                            c["start_timestamp"]
                        ).isoformat()
                    ),

                    "entry_price": c[
                        "entry_price"
                    ],

                    "entry_raw_price": c[
                        "entry_raw_price"
                    ],

                    "hedge1_price": c[
                        "hedge1_price"
                    ],

                    "hedge1_size": c[
                        "hedge1_size"
                    ],

                    "hedge2_price": c[
                        "hedge2_price"
                    ],

                    "hedge2_size": c[
                        "hedge2_size"
                    ],

                    "hedge_count": c[
                        "hedge_count"
                    ],

                    "blocked_hedges": c[
                        "blocked_hedges"
                    ],

                    "exit_datetime": (
                        ts_to_dt(
                            c["exit_timestamp"]
                        ).isoformat()
                        if c["exit_timestamp"]
                        is not None
                        else ""
                    ),

                    "exit_price": c[
                        "exit_price"
                    ],

                    "exit_raw_price": c[
                        "exit_raw_price"
                    ],

                    "exit_reason": c[
                        "exit_reason"
                    ],

                    "duration_hours": c[
                        "duration_hours"
                    ],

                    "gross_pnl": c[
                        "gross_pnl"
                    ],

                    "fees": c[
                        "fees"
                    ],

                    "slippage": c[
                        "slippage"
                    ],

                    "funding_total": c[
                        "funding_total"
                    ],

                    "net_pnl": c[
                        "net_pnl"
                    ],

                    "net_return_pct": c[
                        "net_return_pct"
                    ],

                    "max_drawdown": c[
                        "max_drawdown"
                    ],

                    "liquidation_price_at_entry": c[
                        "liquidation_price_at_entry"
                    ],

                    "liquidation_distance_pct": c[
                        "liquidation_distance_pct"
                    ],

                    "target_hit": c[
                        "target_hit"
                    ],
                }
            )


# ============================================================
# CSV: SUMMARY
# ============================================================

def write_summary_csv(
    summaries
):

    if not summaries:
        return

    fields = list(
        summaries[0].keys()
    )

    with open(
        SUMMARY_CSV,
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )

        writer.writeheader()

        for row in summaries:
            writer.writerow(row)


# ============================================================
# CSV: FUNDING
# ============================================================

def write_funding_csv(
    funding
):

    fields = [
        "datetime",
        "timestamp",
        "rate",
        "source",
    ]

    with open(
        FUNDING_CSV,
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )

        writer.writeheader()

        for row in funding:

            writer.writerow(
                {
                    "datetime": (
                        ts_to_dt(
                            row["timestamp"]
                        ).isoformat()
                    ),
                    "timestamp": row[
                        "timestamp"
                    ],
                    "rate": row[
                        "rate"
                    ],
                    "source": row.get(
                        "source",
                        "",
                    ),
                }
            )


# ============================================================
# CSV: EQUITY
# ============================================================

def write_equity_csv(
    all_cycles
):

    fields = [
        "model",
        "direction",
        "cycle_number",
        "start_datetime",
        "exit_datetime",
        "exit_reason",
        "net_pnl",
        "net_return_pct",
        "cumulative_pnl",
        "equity",
    ]

    cumulative_by_scenario = {}

    with open(
        EQUITY_CSV,
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )

        writer.writeheader()

        counters = {}

        for c in all_cycles:

            key = (
                c["model"],
                c["direction"],
            )

            counters[key] = (
                counters.get(key, 0)
                + 1
            )

            cumulative_by_scenario[key] = (
                cumulative_by_scenario.get(
                    key,
                    0.0,
                )
                + c["net_pnl"]
            )

            equity = (
                CAPITAL_START
                + cumulative_by_scenario[key]
            )

            writer.writerow(
                {
                    "model": c["model"],
                    "direction": c["direction"],
                    "cycle_number": counters[key],

                    "start_datetime": (
                        ts_to_dt(
                            c["start_timestamp"]
                        ).isoformat()
                    ),

                    "exit_datetime": (
                        ts_to_dt(
                            c["exit_timestamp"]
                        ).isoformat()
                        if c["exit_timestamp"]
                        is not None
                        else ""
                    ),

                    "exit_reason": c[
                        "exit_reason"
                    ],

                    "net_pnl": c[
                        "net_pnl"
                    ],

                    "net_return_pct": c[
                        "net_return_pct"
                    ],

                    "cumulative_pnl": (
                        cumulative_by_scenario[key]
                    ),

                    "equity": equity,
                }
            )


# ============================================================
# CONSOLE REPORT
# ============================================================

def print_summary(
    summaries
):

    log("")
    log("=" * 100)
    log("FINAL BTC HEDGE BACKTEST REPORT")
    log("=" * 100)

    log(
        f"Capital start: ${CAPITAL_START:.2f}"
    )

    log(
        f"Leverage: {LEVERAGE:.1f}x"
    )

    log(
        f"Initial notional: ${INITIAL_NOTIONAL:.2f}"
    )

    log(
        f"Hedge trigger: "
        f"{HEDGE_TRIGGER_PCT:.2%}"
    )

    log(
        f"Target: "
        f"+${TARGET_NET_PROFIT:.2f} "
        f"(+{TARGET_NET_PROFIT / CAPITAL_START * 100:.1f}%)"
    )

    log(
        f"Fee model: "
        f"{TAKER_FEE_RATE:.4%}"
    )

    log(
        f"Slippage model: "
        f"{SLIPPAGE_RATE:.4%}"
    )

    log("")

    header = (
        "MODEL      DIR    CYCLES   "
        "SUCCESS%   AVG PNL   MAX LOSS   "
        "DD        LIQ   TARGET   "
        "FINAL $   RETURN%"
    )

    print(header)

    print("-" * len(header))

    for s in summaries:

        print(
            f"{s['model']:<10} "
            f"{s['direction']:<6} "
            f"{s['cycles']:>7} "
            f"{s['success_pct']:>10.2f} "
            f"{s['average_profit']:>10.4f} "
            f"{s['max_loss']:>10.4f} "
            f"{s['max_drawdown']:>9.4f} "
            f"{s['liquidations']:>5} "
            f"{s['target_hits']:>8} "
            f"{s['final_equity']:>9.4f} "
            f"{s['final_return_pct']:>8.2f}"
        )

    log("")
    log("=" * 100)

    log(
        "Important: FINAL RETURN is the result of "
        "sequential independent cycles for each scenario."
    )

    log(
        "It is NOT a claim that all six scenarios "
        "were traded simultaneously."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("")
    print("=" * 70)
    print("KRAKEN FUTURES BTC HEDGE BACKTEST")
    print(f"VERSION {VERSION}")
    print("=" * 70)
    print("")

    # --------------------------------------------------------
    # Safety
    # --------------------------------------------------------

    if REAL_TRADING:
        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    if not PAPER_TRADING:
        raise RuntimeError(
            "PAPER_TRADING must remain True."
        )

    # --------------------------------------------------------
    # Basic validation
    # --------------------------------------------------------

    if CAPITAL_START <= 0:
        raise RuntimeError(
            "CAPITAL_START must be positive."
        )

    if LEVERAGE <= 0:
        raise RuntimeError(
            "LEVERAGE must be positive."
        )

    if INITIAL_NOTIONAL <= 0:
        raise RuntimeError(
            "INITIAL_NOTIONAL must be positive."
        )

    if MAX_HEDGES < 0:
        raise RuntimeError(
            "MAX_HEDGES cannot be negative."
        )

    if not HEDGE_MODELS:
        raise RuntimeError(
            "No hedge models configured."
        )

    # --------------------------------------------------------
    # Instrument
    # --------------------------------------------------------

    instrument = load_instrument()

    maintenance_rate = instrument[
        "maintenance_margin_rate"
    ]

    # --------------------------------------------------------
    # Candles
    # --------------------------------------------------------

    candles = download_candles()

    # --------------------------------------------------------
    # Funding
    # --------------------------------------------------------

    funding = download_funding()

    # Save raw funding immediately so even a later failure
    # does not destroy the useful diagnostic output.
    write_funding_csv(
        funding
    )

    # --------------------------------------------------------
    # Run all scenarios.
    # --------------------------------------------------------

    all_cycles = []
    summaries = []

    for model_name, hedge_ratio in (
        HEDGE_MODELS.items()
    ):

        for direction in (
            "LONG",
            "SHORT",
        ):

            cycles = run_scenario(
                candles=candles,
                model_name=model_name,
                hedge_ratio=hedge_ratio,
                direction=direction,
                funding=funding,
                maintenance_rate=maintenance_rate,
            )

            all_cycles.extend(
                cycles
            )

            summary = summarize_scenario(
                cycles,
                model_name,
                direction,
            )

            summaries.append(
                summary
            )

    # --------------------------------------------------------
    # Outputs
    # --------------------------------------------------------

    write_trades_csv(
        all_cycles
    )

    write_summary_csv(
        summaries
    )

    write_equity_csv(
        all_cycles
    )

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    print_summary(
        summaries
    )

    log("")
    log(
        "Output files:"
    )

    log(
        f"  {TRADES_CSV}"
    )

    log(
        f"  {SUMMARY_CSV}"
    )

    log(
        f"  {EQUITY_CSV}"
    )

    log(
        f"  {FUNDING_CSV}"
    )

    log("")
    log(
        "BACKTEST COMPLETED SUCCESSFULLY."
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        log(
            "BACKTEST INTERRUPTED."
        )

        sys.exit(130)

    except Exception as exc:

        print("")
        print("=" * 70)
        print("BACKTEST FAILED")
        print("=" * 70)

        print(
            f"{type(exc).__name__}: {exc}"
        )

        traceback.print_exc()

        print("=" * 70)

        sys.exit(1)
