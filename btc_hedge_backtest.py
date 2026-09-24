# ============================================================
# KRAKEN FUTURES BTC HEDGE BACKTEST
# VERSION 2.0
# ============================================================
#
# DATA:
#   Kraken Futures
#   PF_XBTUSD
#   5-minute candles
#   Last 6 months
#
# CAPITAL:
#   $2.00
#   10x leverage
#   Initial position = $10
#   Initial margin = $1
#
# HEDGE:
#   Trigger = 2%
#   Maximum hedges = 2
#
# MODELS:
#   A = 50%  -> $5 hedge each
#   B = 75%  -> $7.50 hedge each
#   C = 100% -> $10 hedge each
#
# TARGET:
#   +$0.20 net per cycle
#
# COSTS:
#   Kraken Futures Tier-1 taker fee = 0.05%
#   Slippage = 0.02% per execution
#   Funding = Kraken historical funding
#
# IMPORTANT:
#   PAPER / BACKTEST ONLY
#   NO API KEY
#   NO ORDERS
#
# CONSERVATIVE INTRABAR RULE:
#   If liquidation and hedge can both occur inside the
#   same 5m candle, liquidation wins.
#
# EXIT:
#   1) Net target +$0.20
#   2) Liquidation
#   3) End of data
#
# NO TIME EXIT.
#
# OUTPUT:
#   btc_hedge_trades.csv
#   btc_hedge_summary.csv
#   btc_hedge_equity.csv
#   btc_hedge_funding.csv
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

VERSION = "2.0"

SYMBOL = "PF_XBTUSD"

CAPITAL_START = 2.00
LEVERAGE = 10.0

INITIAL_NOTIONAL = 10.00
INITIAL_MARGIN = INITIAL_NOTIONAL / LEVERAGE

HEDGE_TRIGGER_PCT = 0.02
MAX_HEDGES = 2

TARGET_NET_PROFIT = 0.20

# Kraken Futures Tier 1 taker fee
TAKER_FEE_RATE = 0.0005

# User requested slippage to be included.
# 0.02% = 2 basis points.
SLIPPAGE_RATE = 0.0002

# Conservative cross-margin liquidation model.
# The actual exchange liquidation engine can include
# additional mechanics, so this is deliberately not presented
# as an exact account liquidation price.
LIQUIDATION_BUFFER_USD = 0.0

# Kraken chart resolution
TIMEFRAME = "5m"
CANDLE_SECONDS = 300

# Download chunk size
CHUNK_CANDLES = 1000

# Six months approximation.
LOOKBACK_DAYS = 182

REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 5
REQUEST_SLEEP = 0.25

KRAKEN_BASE = "https://futures.kraken.com"

CANDLES_URL = (
    KRAKEN_BASE
    + "/api/charts/v1/trade/"
    + SYMBOL
    + "/"
    + TIMEFRAME
)

FUNDING_URL = (
    KRAKEN_BASE
    + "/derivatives/api/v3/historicalfundingrates"
)

INSTRUMENTS_URL = (
    KRAKEN_BASE
    + "/derivatives/api/v3/instruments"
)

OUTPUT_TRADES = "btc_hedge_trades.csv"
OUTPUT_SUMMARY = "btc_hedge_summary.csv"
OUTPUT_EQUITY = "btc_hedge_equity.csv"
OUTPUT_FUNDING = "btc_hedge_funding.csv"


# ============================================================
# HEDGE MODELS
# ============================================================

MODELS = {
    "A": {
        "hedge_ratio": 0.50,
        "hedge_notional": 5.00,
    },
    "B": {
        "hedge_ratio": 0.75,
        "hedge_notional": 7.50,
    },
    "C": {
        "hedge_ratio": 1.00,
        "hedge_notional": 10.00,
    },
}


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "BTC-Hedge-Backtest/2.0"
        ),
        "Accept": "application/json",
    }
)


# ============================================================
# UTILITIES
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def iso_utc(ms):
    return datetime.fromtimestamp(
        ms / 1000.0,
        tz=timezone.utc,
    ).isoformat()


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def request_json(
    url,
    params=None,
    retries=REQUEST_RETRIES,
):
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            data = response.json()

            return data

        except Exception as exc:
            last_error = exc

            print(
                f"API retry "
                f"{attempt}/{retries}: "
                f"{url}"
            )

            print(f"ERROR: {exc}")

            if attempt < retries:
                time.sleep(
                    REQUEST_SLEEP * attempt
                )

    raise RuntimeError(
        f"Kraken request failed after "
        f"{retries} attempts: {url}"
    ) from last_error


# ============================================================
# KRAKEN INSTRUMENT
# ============================================================

def get_instrument():
    print()
    print("=" * 70)
    print("LOADING KRAKEN FUTURES INSTRUMENT")
    print("=" * 70)

    data = request_json(INSTRUMENTS_URL)

    instruments = data.get(
        "instruments",
        [],
    )

    target = None

    for item in instruments:
        symbol = str(
            item.get("symbol", "")
        ).upper()

        if symbol == SYMBOL.upper():
            target = item
            break

    if target is None:
        raise RuntimeError(
            f"Kraken instrument not found: {SYMBOL}"
        )

    print(
        f"Instrument: "
        f"{target.get('symbol')}"
    )

    print(
        f"Type: "
        f"{target.get('type')}"
    )

    print(
        f"Tradeable: "
        f"{target.get('tradeable')}"
    )

    # Prefer retail margin levels when available.
    levels = target.get(
        "retailMarginLevels"
    )

    if not levels:
        levels = target.get(
            "marginLevels"
        )

    if not levels:
        raise RuntimeError(
            "No margin levels found for "
            + SYMBOL
        )

    first_level = None

    for level in levels:
        contracts = safe_float(
            level.get("contracts"),
            0.0,
        )

        if contracts <= 0:
            first_level = level
            break

    if first_level is None:
        first_level = levels[0]

    maintenance_margin = safe_float(
        first_level.get(
            "maintenanceMargin"
        ),
        0.01,
    )

    exchange_initial_margin = safe_float(
        first_level.get(
            "initialMargin"
        ),
        0.10,
    )

    print(
        f"Exchange maintenance margin: "
        f"{maintenance_margin * 100:.4f}%"
    )

    print(
        f"Exchange reference initial margin: "
        f"{exchange_initial_margin * 100:.4f}%"
    )

    return {
        "maintenance_margin": maintenance_margin,
        "exchange_initial_margin": (
            exchange_initial_margin
        ),
        "raw": target,
    }


# ============================================================
# KRAKEN CANDLES
# ============================================================

def parse_candle(item):
    if not isinstance(item, dict):
        return None

    timestamp = item.get("time")

    if timestamp is None:
        timestamp = item.get("timestamp")

    if timestamp is None:
        return None

    timestamp = int(timestamp)

    if timestamp < 10_000_000_000:
        timestamp *= 1000

    return {
        "timestamp": timestamp,
        "open": safe_float(item.get("open")),
        "high": safe_float(item.get("high")),
        "low": safe_float(item.get("low")),
        "close": safe_float(item.get("close")),
        "volume": safe_float(item.get("volume")),
    }


def download_candles():
    print()
    print("=" * 70)
    print("DOWNLOADING KRAKEN BTC PERPETUAL 5M DATA")
    print("=" * 70)

    end_dt = utc_now()

    start_dt = (
        end_dt
        - timedelta(days=LOOKBACK_DAYS)
    )

    start_ms = int(
        start_dt.timestamp() * 1000
    )

    # Do not use the currently-forming candle.
    current_bucket_ms = (
        int(end_dt.timestamp())
        // CANDLE_SECONDS
    ) * CANDLE_SECONDS * 1000

    end_ms = current_bucket_ms

    print(
        "From:",
        start_dt.isoformat(),
    )

    print(
        "To:",
        datetime.fromtimestamp(
            end_ms / 1000,
            timezone.utc,
        ).isoformat(),
    )

    candles = []

    cursor_ms = start_ms

    while cursor_ms < end_ms:
        chunk_end_ms = min(
            end_ms,
            cursor_ms
            + (
                CHUNK_CANDLES
                * CANDLE_SECONDS
                * 1000
            ),
        )

        params = {
            "from": int(
                cursor_ms / 1000
            ),
            "to": int(
                chunk_end_ms / 1000
            ),
            "count": CHUNK_CANDLES,
        }

        data = request_json(
            CANDLES_URL,
            params=params,
        )

        raw_candles = data.get(
            "candles",
            [],
        )

        parsed = []

        for item in raw_candles:
            candle = parse_candle(item)

            if candle is None:
                continue

            if candle["timestamp"] < start_ms:
                continue

            if candle["timestamp"] >= end_ms:
                continue

            parsed.append(candle)

        if not parsed:
            print(
                "No candles returned. "
                f"cursor={iso_utc(cursor_ms)}"
            )

            break

        candles.extend(parsed)

        last_ts = parsed[-1]["timestamp"]

        next_cursor = (
            last_ts
            + CANDLE_SECONDS * 1000
        )

        if next_cursor <= cursor_ms:
            raise RuntimeError(
                "Kraken candle pagination "
                "did not advance."
            )

        cursor_ms = next_cursor

        print(
            f"Downloaded: "
            f"{len(candles):,} candles | "
            f"{iso_utc(last_ts)}"
        )

        time.sleep(REQUEST_SLEEP)

    # Deduplicate.
    unique = {}

    for candle in candles:
        unique[
            candle["timestamp"]
        ] = candle

    candles = list(
        unique.values()
    )

    candles.sort(
        key=lambda x: x["timestamp"]
    )

    if len(candles) < 1000:
        raise RuntimeError(
            "Too few candles downloaded."
        )

    # Validate gaps.
    gaps = []

    for i in range(1, len(candles)):
        delta = (
            candles[i]["timestamp"]
            - candles[i - 1]["timestamp"]
        )

        if delta != CANDLE_SECONDS * 1000:
            gaps.append(
                (
                    candles[i - 1]["timestamp"],
                    candles[i]["timestamp"],
                    delta,
                )
            )

    print()
    print(
        f"TOTAL CANDLES: {len(candles):,}"
    )

    print(
        f"DATA START: "
        f"{iso_utc(candles[0]['timestamp'])}"
    )

    print(
        f"DATA END:   "
        f"{iso_utc(candles[-1]['timestamp'])}"
    )

    print(
        f"GAPS: {len(gaps)}"
    )

    if gaps:
        print(
            "WARNING: candle gaps detected."
        )

        for gap in gaps[:10]:
            print(
                " GAP:",
                iso_utc(gap[0]),
                "->",
                iso_utc(gap[1]),
            )

    return candles


# ============================================================
# KRAKEN FUNDING
# ============================================================

def download_funding(
    start_ms,
    end_ms,
):
    print()
    print("=" * 70)
    print("DOWNLOADING KRAKEN HISTORICAL FUNDING")
    print("=" * 70)

    params = {
        "symbol": SYMBOL,
    }

    data = request_json(
        FUNDING_URL,
        params=params,
    )

    rates = data.get(
        "rates",
        [],
    )

    result = []

    for item in rates:
        timestamp_raw = item.get(
            "timestamp"
        )

        if timestamp_raw is None:
            continue

        try:
            dt = datetime.fromisoformat(
                str(timestamp_raw).replace(
                    "Z",
                    "+00:00",
                )
            )

            timestamp_ms = int(
                dt.timestamp() * 1000
            )

        except Exception:
            continue

        if timestamp_ms < start_ms:
            continue

        if timestamp_ms > end_ms:
            continue

        # Kraken publishes both:
        # fundingRate
        # relativeFundingRate
        #
        # For the funding payment calculation,
        # relativeFundingRate is the percentage rate.
        relative_rate = safe_float(
            item.get(
                "relativeFundingRate"
            ),
            0.0,
        )

        funding_rate = safe_float(
            item.get(
                "fundingRate"
            ),
            0.0,
        )

        result.append(
            {
                "timestamp": timestamp_ms,
                "funding_rate": funding_rate,
                "relative_funding_rate": (
                    relative_rate
                ),
            }
        )

    result.sort(
        key=lambda x: x["timestamp"]
    )

    # Deduplicate.
    unique = {}

    for item in result:
        unique[
            item["timestamp"]
        ] = item

    result = list(
        unique.values()
    )

    result.sort(
        key=lambda x: x["timestamp"]
    )

    print(
        f"FUNDING EVENTS: {len(result):,}"
    )

    if result:
        print(
            "FUNDING START:",
            iso_utc(
                result[0]["timestamp"]
            ),
        )

        print(
            "FUNDING END:",
            iso_utc(
                result[-1]["timestamp"]
            ),
        )

    return result


# ============================================================
# POSITION HELPERS
# ============================================================

def opposite_direction(direction):
    if direction == "LONG":
        return "SHORT"

    return "LONG"


def signed_qty(
    direction,
    notional,
    execution_price,
):
    qty = (
        notional
        / execution_price
    )

    if direction == "LONG":
        return qty

    return -qty


def execution_price(
    raw_price,
    direction,
):
    if direction == "LONG":
        return (
            raw_price
            * (1.0 + SLIPPAGE_RATE)
        )

    return (
        raw_price
        * (1.0 - SLIPPAGE_RATE)
    )


def order_fee(
    notional,
):
    return (
        abs(notional)
        * TAKER_FEE_RATE
    )


def position_pnl_at_price(
    position,
    price,
):
    return (
        position["qty"]
        * (
            price
            - position["entry_exec"]
        )
    )


def portfolio_unrealized_pnl(
    positions,
    price,
):
    total = 0.0

    for position in positions:
        total += position_pnl_at_price(
            position,
            price,
        )

    return total


def portfolio_equity(
    cash,
    positions,
    price,
):
    return (
        cash
        + portfolio_unrealized_pnl(
            positions,
            price,
        )
    )


def required_initial_margin(
    positions,
):
    total = 0.0

    for position in positions:
        total += (
            position["notional"]
            / LEVERAGE
        )

    return total


def maintenance_margin(
    positions,
    price,
    maintenance_rate,
):
    total_notional = 0.0

    for position in positions:
        total_notional += (
            abs(position["qty"])
            * price
        )

    return (
        total_notional
        * maintenance_rate
    )


def liquidation_function(
    cash,
    positions,
    price,
    maintenance_rate,
):
    equity = portfolio_equity(
        cash,
        positions,
        price,
    )

    maintenance = maintenance_margin(
        positions,
        price,
        maintenance_rate,
    )

    return (
        equity
        - maintenance
        - LIQUIDATION_BUFFER_USD
    )


# ============================================================
# LIQUIDATION PRICE
# ============================================================

def find_liquidation_price(
    cash,
    positions,
    current_price,
    maintenance_rate,
):
    if not positions:
        return None

    current_value = liquidation_function(
        cash,
        positions,
        current_price,
        maintenance_rate,
    )

    if current_value <= 0:
        return current_price

    net_qty = sum(
        position["qty"]
        for position in positions
    )

    # If net long:
    # lower price is adverse.
    #
    # If net short:
    # higher price is adverse.
    #
    # If net neutral:
    # maintenance grows with price,
    # therefore the high side is adverse.
    if net_qty > 1e-18:
        direction = "DOWN"
    else:
        direction = "UP"

    if direction == "DOWN":
        high = current_price
        low = current_price * 0.50

        low_value = liquidation_function(
            cash,
            positions,
            low,
            maintenance_rate,
        )

        for _ in range(40):
            if low_value <= 0:
                break

            low *= 0.75

            if low <= 1e-8:
                break

            low_value = liquidation_function(
                cash,
                positions,
                low,
                maintenance_rate,
            )

        if low_value > 0:
            return None

        for _ in range(80):
            mid = (
                low
                + high
            ) / 2.0

            value = liquidation_function(
                cash,
                positions,
                mid,
                maintenance_rate,
            )

            if value <= 0:
                low = mid
            else:
                high = mid

        return high

    else:
        low = current_price
        high = current_price * 1.50

        high_value = liquidation_function(
            cash,
            positions,
            high,
            maintenance_rate,
        )

        for _ in range(40):
            if high_value <= 0:
                break

            high *= 1.25

            high_value = liquidation_function(
                cash,
                positions,
                high,
                maintenance_rate,
            )

        if high_value > 0:
            return None

        for _ in range(80):
            mid = (
                low
                + high
            ) / 2.0

            value = liquidation_function(
                cash,
                positions,
                mid,
                maintenance_rate,
            )

            if value <= 0:
                high = mid
            else:
                low = mid

        return low


def liquidation_distance_pct(
    cash,
    positions,
    current_price,
    maintenance_rate,
):
    liq_price = find_liquidation_price(
        cash,
        positions,
        current_price,
        maintenance_rate,
    )

    if liq_price is None:
        return None

    if current_price <= 0:
        return None

    return (
        abs(
            liq_price
            / current_price
            - 1.0
        )
        * 100.0
    )


# ============================================================
# FUNDING
# ============================================================

def apply_funding_events(
    cash,
    positions,
    funding_events,
    funding_index,
    until_timestamp,
):
    total_funding = 0.0
    events_used = []

    while (
        funding_index
        < len(funding_events)
    ):
        event = funding_events[
            funding_index
        ]

        if event["timestamp"] > until_timestamp:
            break

        rate = event[
            "relative_funding_rate"
        ]

        # Use the most recent candle close
        # available at the funding timestamp.
        #
        # The caller supplies the price separately
        # by updating position notional using the
        # current mark proxy.
        #
        # Here we use event's rate and current
        # position entry framework; caller will
        # overwrite price in the event object.
        funding_price = event.get(
            "price",
            None,
        )

        if funding_price is None:
            funding_index += 1
            continue

        event_payment = 0.0

        for position in positions:
            notional = (
                abs(position["qty"])
                * funding_price
            )

            # Positive rate:
            # Long pays
            # Short receives
            #
            # Negative rate:
            # Long receives
            # Short pays
            payment = (
                math.copysign(
                    notional * rate,
                    position["qty"],
                )
            )

            event_payment += payment

        cash -= event_payment

        total_funding += event_payment

        events_used.append(
            {
                "timestamp": event["timestamp"],
                "rate": rate,
                "payment": event_payment,
            }
        )

        funding_index += 1

    return (
        cash,
        funding_index,
        total_funding,
        events_used,
    )


# ============================================================
# TARGET EXIT
# ============================================================

def close_execution_price(
    raw_price,
    direction,
):
    return execution_price(
        raw_price,
        direction,
    )


def projected_exit_equity(
    cash,
    positions,
    raw_exit_price,
):
    gross_realized = 0.0
    closing_fees = 0.0

    for position in positions:
        direction = (
            "LONG"
            if position["qty"] > 0
            else "SHORT"
        )

        exec_price = (
            close_execution_price(
                raw_exit_price,
                direction,
            )
        )

        gross_realized += (
            position["qty"]
            * (
                exec_price
                - position["entry_exec"]
            )
        )

        closing_notional = (
            abs(position["qty"])
            * exec_price
        )

        closing_fees += order_fee(
            closing_notional
        )

    return (
        cash
        + gross_realized
        - closing_fees
    )


def find_target_price(
    cash,
    positions,
    current_price,
    low,
    high,
    target_equity,
):
    if not positions:
        return None

    net_qty = sum(
        position["qty"]
        for position in positions
    )

    current_equity = projected_exit_equity(
        cash,
        positions,
        current_price,
    )

    if current_equity >= target_equity:
        return current_price

    if abs(net_qty) < 1e-18:
        return None

    if net_qty > 0:
        # Target requires higher price.
        candidate = high

        if (
            projected_exit_equity(
                cash,
                positions,
                candidate,
            )
            < target_equity
        ):
            return None

        lo = current_price
        hi = candidate

        for _ in range(80):
            mid = (
                lo + hi
            ) / 2.0

            value = projected_exit_equity(
                cash,
                positions,
                mid,
            )

            if value >= target_equity:
                hi = mid
            else:
                lo = mid

        return hi

    else:
        # Target requires lower price.
        candidate = low

        if (
            projected_exit_equity(
                cash,
                positions,
                candidate,
            )
            < target_equity
        ):
            return None

        lo = candidate
        hi = current_price

        for _ in range(80):
            mid = (
                lo + hi
            ) / 2.0

            value = projected_exit_equity(
                cash,
                positions,
                mid,
            )

            if value >= target_equity:
                lo = mid
            else:
                hi = mid

        return lo


# ============================================================
# CYCLE SIMULATION
# ============================================================

def simulate_cycle(
    candles,
    funding_events,
    start_index,
    model_name,
    model,
    initial_direction,
    starting_equity,
    maintenance_rate,
):
    if start_index >= len(candles):
        return None, len(candles)

    entry_candle = candles[
        start_index
    ]

    raw_entry_price = (
        entry_candle["open"]
    )

    entry_direction = initial_direction

    entry_exec = execution_price(
        raw_entry_price,
        entry_direction,
    )

    entry_fee = order_fee(
        INITIAL_NOTIONAL
    )

    # Need enough equity for initial margin + entry fee.
    if (
        starting_equity
        < INITIAL_MARGIN + entry_fee
    ):
        return {
            "status": "INSUFFICIENT_CAPITAL",
            "next_index": len(candles),
        }, len(candles)

    cash = (
        starting_equity
        - entry_fee
    )

    initial_position = {
        "direction": entry_direction,
        "qty": signed_qty(
            entry_direction,
            INITIAL_NOTIONAL,
            entry_exec,
        ),
        "notional": INITIAL_NOTIONAL,
        "entry_raw": raw_entry_price,
        "entry_exec": entry_exec,
        "entry_time": entry_candle[
            "timestamp"
        ],
    }

    positions = [
        initial_position
    ]

    fees_total = entry_fee
    slippage_total = (
        abs(
            initial_position["qty"]
        )
        * abs(
            entry_exec
            - raw_entry_price
        )
    )

    funding_total = 0.0

    hedge_events = []

    blocked_hedges = 0

    next_hedge_number = 1

    # Hedge triggers based on the ORIGINAL entry price.
    if entry_direction == "LONG":
        hedge_trigger_prices = [
            raw_entry_price
            * (
                1.0
                - HEDGE_TRIGGER_PCT
                * i
            )
            for i in range(
                1,
                MAX_HEDGES + 1,
            )
        ]
    else:
        hedge_trigger_prices = [
            raw_entry_price
            * (
                1.0
                + HEDGE_TRIGGER_PCT
                * i
            )
            for i in range(
                1,
                MAX_HEDGES + 1,
            )
        ]

    start_time = (
        entry_candle["timestamp"]
    )

    max_drawdown_pct = 0.0

    min_liquidation_distance = None

    max_adverse_price = raw_entry_price

    funding_index = 0

    # Skip funding before entry.
    while (
        funding_index
        < len(funding_events)
        and funding_events[
            funding_index
        ]["timestamp"]
        <= start_time
    ):
        funding_index += 1

    last_processed_timestamp = (
        start_time
    )

    exit_reason = None
    exit_raw_price = None
    exit_exec_price = None
    exit_timestamp = None

    liquidation_price = None

    last_index = start_index

    for i in range(
        start_index,
        len(candles),
    ):
        candle = candles[i]

        timestamp = candle[
            "timestamp"
        ]

        # ----------------------------------------------------
        # APPLY FUNDING AT / BEFORE CANDLE OPEN
        # ----------------------------------------------------

        # Attach candle close as funding price proxy
        # for any funding event falling before this candle.
        while (
            funding_index
            < len(funding_events)
            and funding_events[
                funding_index
            ]["timestamp"]
            <= timestamp
        ):
            funding_events[
                funding_index
            ]["price"] = candle[
                "close"
            ]

            (
                cash,
                funding_index,
                funding_payment,
                used_events,
            ) = apply_funding_events(
                cash,
                positions,
                funding_events,
                funding_index,
                timestamp,
            )

            funding_total += (
                funding_payment
            )

            # Record separately later through
            # the returned funding event list.
            for used in used_events:
                pass

        # ----------------------------------------------------
        # CURRENT OPEN PRICE EQUITY
        # ----------------------------------------------------

        current_open = candle[
            "open"
        ]

        open_equity = portfolio_equity(
            cash,
            positions,
            current_open,
        )

        cycle_drawdown = (
            (
                open_equity
                - starting_equity
            )
            / starting_equity
            * 100.0
        )

        if cycle_drawdown < max_drawdown_pct:
            max_drawdown_pct = (
                cycle_drawdown
            )

        liq_dist = liquidation_distance_pct(
            cash,
            positions,
            current_open,
            maintenance_rate,
        )

        if liq_dist is not None:
            if (
                min_liquidation_distance
                is None
                or liq_dist
                < min_liquidation_distance
            ):
                min_liquidation_distance = (
                    liq_dist
                )

        # ----------------------------------------------------
        # OPEN-PRICE LIQUIDATION CHECK
        # ----------------------------------------------------

        open_liq_value = liquidation_function(
            cash,
            positions,
            current_open,
            maintenance_rate,
        )

        if open_liq_value <= 0:
            exit_reason = "LIQUIDATION"
            exit_raw_price = current_open
            exit_timestamp = timestamp
            liquidation_price = current_open
            last_index = i
            break

        # ----------------------------------------------------
        # INTRABAR LIQUIDATION CHECK
        #
        # THIS IS DONE BEFORE HEDGES.
        # ----------------------------------------------------

        net_qty = sum(
            position["qty"]
            for position in positions
        )

        if net_qty > 1e-18:
            adverse_price = candle["low"]
        elif net_qty < -1e-18:
            adverse_price = candle["high"]
        else:
            adverse_price = candle["high"]

        adverse_liq_value = liquidation_function(
            cash,
            positions,
            adverse_price,
            maintenance_rate,
        )

        if adverse_liq_value <= 0:
            liq_price = find_liquidation_price(
                cash,
                positions,
                current_open,
                maintenance_rate,
            )

            if liq_price is None:
                liq_price = adverse_price

            exit_reason = "LIQUIDATION"
            exit_raw_price = liq_price
            exit_timestamp = timestamp
            liquidation_price = liq_price
            last_index = i
            break

        # ----------------------------------------------------
        # UPDATE MAX DRAWDOWN USING LOW/HIGH
        # ----------------------------------------------------

        adverse_equity = portfolio_equity(
            cash,
            positions,
            adverse_price,
        )

        adverse_drawdown = (
            (
                adverse_equity
                - starting_equity
            )
            / starting_equity
            * 100.0
        )

        if (
            adverse_drawdown
            < max_drawdown_pct
        ):
            max_drawdown_pct = (
                adverse_drawdown
            )

        # Track adverse excursion.
        if entry_direction == "LONG":
            if candle["low"] < max_adverse_price:
                max_adverse_price = candle["low"]
        else:
            if candle["high"] > max_adverse_price:
                max_adverse_price = candle["high"]

        # ----------------------------------------------------
        # HEDGE TRIGGERS
        #
        # Hedge first, because conservative rule.
        # Liquidation was already checked above.
        # ----------------------------------------------------

        while (
            next_hedge_number
            <= MAX_HEDGES
        ):
            trigger_price = (
                hedge_trigger_prices[
                    next_hedge_number - 1
                ]
            )

            if entry_direction == "LONG":
                trigger_hit = (
                    candle["low"]
                    <= trigger_price
                )
            else:
                trigger_hit = (
                    candle["high"]
                    >= trigger_price
                )

            if not trigger_hit:
                break

            hedge_direction = (
                opposite_direction(
                    entry_direction
                )
            )

            hedge_notional = (
                model["hedge_notional"]
            )

            hedge_exec = execution_price(
                trigger_price,
                hedge_direction,
            )

            hedge_margin = (
                hedge_notional
                / LEVERAGE
            )

            current_required_margin = (
                required_initial_margin(
                    positions
                )
            )

            current_equity = portfolio_equity(
                cash,
                positions,
                trigger_price,
            )

            # Margin must be available.
            if (
                current_required_margin
                + hedge_margin
                > current_equity
            ):
                blocked_hedges += 1

                hedge_events.append(
                    {
                        "number": next_hedge_number,
                        "status": (
                            "BLOCKED_MARGIN"
                        ),
                        "raw_price": (
                            trigger_price
                        ),
                        "notional": (
                            hedge_notional
                        ),
                    }
                )

                next_hedge_number += 1

                continue

            hedge_position = {
                "direction": hedge_direction,
                "qty": signed_qty(
                    hedge_direction,
                    hedge_notional,
                    hedge_exec,
                ),
                "notional": hedge_notional,
                "entry_raw": trigger_price,
                "entry_exec": hedge_exec,
                "entry_time": timestamp,
                "hedge_number": (
                    next_hedge_number
                ),
            }

            positions.append(
                hedge_position
            )

            hedge_fee = order_fee(
                hedge_notional
            )

            cash -= hedge_fee

            fees_total += hedge_fee

            slippage_total += (
                abs(
                    hedge_position["qty"]
                )
                * abs(
                    hedge_exec
                    - trigger_price
                )
            )

            hedge_events.append(
                {
                    "number": next_hedge_number,
                    "status": "OPENED",
                    "raw_price": trigger_price,
                    "exec_price": hedge_exec,
                    "notional": hedge_notional,
                    "direction": hedge_direction,
                    "timestamp": timestamp,
                }
            )

            # Re-check liquidation immediately after hedge.
            post_hedge_equity = (
                portfolio_equity(
                    cash,
                    positions,
                    hedge_exec,
                )
            )

            post_hedge_maintenance = (
                maintenance_margin(
                    positions,
                    hedge_exec,
                    maintenance_rate,
                )
            )

            if (
                post_hedge_equity
                <= post_hedge_maintenance
            ):
                exit_reason = (
                    "LIQUIDATION_AFTER_HEDGE"
                )

                exit_raw_price = (
                    trigger_price
                )

                exit_timestamp = timestamp

                liquidation_price = (
                    trigger_price
                )

                last_index = i

                break

            next_hedge_number += 1

        if exit_reason is not None:
            break

        # ----------------------------------------------------
        # TARGET CHECK
        # ----------------------------------------------------

        target_equity = (
            starting_equity
            + TARGET_NET_PROFIT
        )

        target_price = find_target_price(
            cash,
            positions,
            current_open,
            candle["low"],
            candle["high"],
            target_equity,
        )

        if target_price is not None:
            exit_reason = "TARGET"
            exit_raw_price = target_price
            exit_timestamp = timestamp
            last_index = i
            break

        # ----------------------------------------------------
        # END OF CANDLE
        # ----------------------------------------------------

        close_equity = portfolio_equity(
            cash,
            positions,
            candle["close"],
        )

        close_drawdown = (
            (
                close_equity
                - starting_equity
            )
            / starting_equity
            * 100.0
        )

        if (
            close_drawdown
            < max_drawdown_pct
        ):
            max_drawdown_pct = (
                close_drawdown
            )

        # Recalculate liquidation distance.
        liq_dist = liquidation_distance_pct(
            cash,
            positions,
            candle["close"],
            maintenance_rate,
        )

        if liq_dist is not None:
            if (
                min_liquidation_distance
                is None
                or liq_dist
                < min_liquidation_distance
            ):
                min_liquidation_distance = (
                    liq_dist
                )

        last_processed_timestamp = timestamp

    # --------------------------------------------------------
    # DATA END
    # --------------------------------------------------------

    if exit_reason is None:
        last_candle = candles[-1]

        exit_reason = "DATA_END"

        exit_raw_price = (
            last_candle["close"]
        )

        exit_timestamp = (
            last_candle["timestamp"]
        )

        last_index = (
            len(candles) - 1
        )

    # --------------------------------------------------------
    # FINAL EXIT
    # --------------------------------------------------------

    closing_gross_raw = 0.0
    closing_fees = 0.0
    closing_slippage = 0.0

    for position in positions:
        direction = (
            "LONG"
            if position["qty"] > 0
            else "SHORT"
        )

        close_exec = execution_price(
            exit_raw_price,
            direction,
        )

        # Gross PnL BEFORE fees/slippage,
        # using raw market prices.
        closing_gross_raw += (
            position["qty"]
            * (
                exit_raw_price
                - position["entry_raw"]
            )
        )

        closing_notional = (
            abs(position["qty"])
            * close_exec
        )

        fee = order_fee(
            closing_notional
        )

        closing_fees += fee

        closing_slippage += (
            abs(position["qty"])
            * abs(
                close_exec
                - exit_raw_price
            )
        )

    fees_total += closing_fees
    slippage_total += closing_slippage

    exit_exec_price = (
        exit_raw_price
    )

    final_equity = (
        starting_equity
        + closing_gross_raw
        - fees_total
        - slippage_total
        - funding_total
    )

    net_pnl = (
        final_equity
        - starting_equity
    )

    duration_seconds = max(
        0,
        exit_timestamp
        - start_time,
    )

    duration_hours = (
        duration_seconds
        / 3600.0
    )

    max_drawdown_usd = (
        starting_equity
        * max_drawdown_pct
        / 100.0
    )

    if initial_direction == "LONG":
        max_adverse_move_pct = (
            (
                max_adverse_price
                / raw_entry_price
                - 1.0
            )
            * 100.0
        )
    else:
        max_adverse_move_pct = (
            (
                max_adverse_price
                / raw_entry_price
                - 1.0
            )
            * 100.0
        )

    hedge_1 = None
    hedge_2 = None

    for hedge in hedge_events:
        if hedge["number"] == 1:
            hedge_1 = hedge

        elif hedge["number"] == 2:
            hedge_2 = hedge

    cycle_id = (
        f"{model_name}_"
        f"{initial_direction}_"
        f"{start_time}"
    )

    result = {
        "cycle_id": cycle_id,
        "model": model_name,
        "direction": initial_direction,
        "entry_time_utc": iso_utc(
            start_time
        ),
        "exit_time_utc": iso_utc(
            exit_timestamp
        ),
        "entry_price": raw_entry_price,
        "exit_price": exit_raw_price,
        "exit_reason": exit_reason,
        "initial_notional": INITIAL_NOTIONAL,
        "starting_equity": starting_equity,
        "ending_equity": final_equity,
        "net_pnl": net_pnl,
        "net_return_pct": (
            net_pnl
            / starting_equity
            * 100.0
        ),
        "gross_pnl": closing_gross_raw,
        "fees": fees_total,
        "slippage": slippage_total,
        "funding": funding_total,
        "max_drawdown_usd": max_drawdown_usd,
        "max_drawdown_pct": max_drawdown_pct,
        "min_liquidation_distance_pct": (
            min_liquidation_distance
            if min_liquidation_distance
            is not None
            else "",
        ),
        "liquidation_price": (
            liquidation_price
            if liquidation_price
            is not None
            else "",
        ),
        "duration_hours": duration_hours,
        "hedge_1_status": (
            hedge_1["status"]
            if hedge_1
            else "",
        ),
        "hedge_1_price": (
            hedge_1.get("raw_price", "")
            if hedge_1
            else "",
        ),
        "hedge_1_exec_price": (
            hedge_1.get("exec_price", "")
            if hedge_1
            else "",
        ),
        "hedge_1_size": (
            hedge_1.get("notional", "")
            if hedge_1
            else "",
        ),
        "hedge_2_status": (
            hedge_2["status"]
            if hedge_2
            else "",
        ),
        "hedge_2_price": (
            hedge_2.get("raw_price", "")
            if hedge_2
            else "",
        ),
        "hedge_2_exec_price": (
            hedge_2.get("exec_price", "")
            if hedge_2
            else "",
        ),
        "hedge_2_size": (
            hedge_2.get("notional", "")
            if hedge_2
            else "",
        ),
        "blocked_hedges": blocked_hedges,
        "success": (
            1
            if net_pnl >= TARGET_NET_PROFIT
            else 0
        ),
        "data_end": (
            1
            if exit_reason == "DATA_END"
            else 0
        ),
    }

    return result, last_index + 1


# ============================================================
# RUN ONE MODEL + ONE DIRECTION
# ============================================================

def run_scenario(
    candles,
    funding_events,
    model_name,
    model,
    direction,
    maintenance_rate,
):
    print()
    print("=" * 70)
    print(
        f"RUNNING MODEL {model_name} "
        f"{direction}"
    )
    print("=" * 70)

    equity = CAPITAL_START

    trades = []

    equity_curve = []

    index = 0

    cycle_number = 0

    while index < len(candles) - 1:
        cycle_number += 1

        result, next_index = simulate_cycle(
            candles=candles,
            funding_events=funding_events,
            start_index=index,
            model_name=model_name,
            model=model,
            initial_direction=direction,
            starting_equity=equity,
            maintenance_rate=maintenance_rate,
        )

        if result is None:
            break

        if result.get("status") == (
            "INSUFFICIENT_CAPITAL"
        ):
            print()
            print(
                f"STOPPED: insufficient capital "
                f"for {model_name} {direction}"
            )
            break

        trades.append(result)

        equity = result[
            "ending_equity"
        ]

        equity_curve.append(
            {
                "cycle": cycle_number,
                "model": model_name,
                "direction": direction,
                "exit_time_utc": result[
                    "exit_time_utc"
                ],
                "equity": equity,
                "net_pnl": result[
                    "net_pnl"
                ],
            }
        )

        print(
            f"Cycle {cycle_number:4d} | "
            f"{result['exit_reason']:24s} | "
            f"PnL ${result['net_pnl']:+.4f} | "
            f"Equity ${equity:.4f}"
        )

        if next_index <= index:
            raise RuntimeError(
                "Backtest cycle did not advance."
            )

        index = next_index

        # If data-end was reached, stop.
        if result["data_end"] == 1:
            break

        # If capital is below initial margin,
        # no new cycle can be opened.
        if equity < INITIAL_MARGIN:
            print(
                "Capital dropped below "
                "initial margin."
            )
            break

    return trades, equity_curve


# ============================================================
# SUMMARY
# ============================================================

def build_summary(
    trades,
    model_name,
    direction,
    final_equity,
):
    count = len(trades)

    if count == 0:
        return {
            "model": model_name,
            "direction": direction,
            "cycles": 0,
            "success_count": 0,
            "success_pct": 0.0,
            "liquidation_count": 0,
            "liquidation_pct": 0.0,
            "target_count": 0,
            "target_pct": 0.0,
            "data_end_count": 0,
            "margin_blocked_cycles": 0,
            "average_net_pnl": 0.0,
            "total_net_pnl": 0.0,
            "max_loss": 0.0,
            "best_cycle": 0.0,
            "average_duration_hours": 0.0,
            "max_drawdown_pct": 0.0,
            "min_liquidation_distance_pct": "",
            "final_equity": final_equity,
            "final_return_pct": (
                (
                    final_equity
                    / CAPITAL_START
                )
                - 1.0
            )
            * 100.0,
        }

    pnls = [
        float(t["net_pnl"])
        for t in trades
    ]

    durations = [
        float(t["duration_hours"])
        for t in trades
    ]

    dd = [
        float(t["max_drawdown_pct"])
        for t in trades
    ]

    liq_distances = []

    for t in trades:
        value = t[
            "min_liquidation_distance_pct"
        ]

        if value != "":
            liq_distances.append(
                float(value)
            )

    success_count = sum(
        1
        for t in trades
        if t["success"] == 1
    )

    liquidation_count = sum(
        1
        for t in trades
        if "LIQUIDATION"
        in t["exit_reason"]
    )

    target_count = sum(
        1
        for t in trades
        if t["exit_reason"] == "TARGET"
    )

    data_end_count = sum(
        1
        for t in trades
        if t["exit_reason"] == "DATA_END"
    )

    margin_blocked_cycles = sum(
        1
        for t in trades
        if int(
            t["blocked_hedges"]
        ) > 0
    )

    total_net_pnl = sum(pnls)

    return {
        "model": model_name,
        "direction": direction,
        "cycles": count,
        "success_count": success_count,
        "success_pct": (
            success_count
            / count
            * 100.0
        ),
        "liquidation_count": liquidation_count,
        "liquidation_pct": (
            liquidation_count
            / count
            * 100.0
        ),
        "target_count": target_count,
        "target_pct": (
            target_count
            / count
            * 100.0
        ),
        "data_end_count": data_end_count,
        "margin_blocked_cycles": (
            margin_blocked_cycles
        ),
        "average_net_pnl": (
            sum(pnls)
            / count
        ),
        "total_net_pnl": total_net_pnl,
        "max_loss": min(pnls),
        "best_cycle": max(pnls),
        "average_duration_hours": (
            sum(durations)
            / count
        ),
        "max_drawdown_pct": min(dd),
        "min_liquidation_distance_pct": (
            min(liq_distances)
            if liq_distances
            else ""
        ),
        "final_equity": final_equity,
        "final_return_pct": (
            (
                final_equity
                / CAPITAL_START
            )
            - 1.0
        )
        * 100.0,
    }


# ============================================================
# CSV WRITERS
# ============================================================

def write_csv(
    filename,
    rows,
):
    if not rows:
        print(
            f"No rows for {filename}"
        )
        return

    fieldnames = list(
        rows[0].keys()
    )

    with open(
        filename,
        "w",
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        writer.writerows(rows)

    print(
        f"WROTE {filename}: "
        f"{len(rows):,} rows"
    )


# ============================================================
# MAIN
# ============================================================

def main():
    print()
    print("=" * 70)
    print(
        "KRAKEN FUTURES BTC HEDGE BACKTEST"
    )
    print(
        f"VERSION {VERSION}"
    )
    print("=" * 70)

    print(
        f"Symbol: {SYMBOL}"
    )

    print(
        f"Capital: ${CAPITAL_START:.2f}"
    )

    print(
        f"Leverage: {LEVERAGE:.1f}x"
    )

    print(
        f"Initial notional: "
        f"${INITIAL_NOTIONAL:.2f}"
    )

    print(
        f"Initial margin: "
        f"${INITIAL_MARGIN:.2f}"
    )

    print(
        f"Hedge trigger: "
        f"{HEDGE_TRIGGER_PCT * 100:.2f}%"
    )

    print(
        f"Target net: "
        f"${TARGET_NET_PROFIT:.2f}"
    )

    print(
        f"Taker fee: "
        f"{TAKER_FEE_RATE * 100:.4f}%"
    )

    print(
        f"Slippage: "
        f"{SLIPPAGE_RATE * 100:.4f}%"
    )

    print(
        f"Lookback: "
        f"{LOOKBACK_DAYS} days"
    )

    instrument = get_instrument()

    maintenance_rate = instrument[
        "maintenance_margin"
    ]

    candles = download_candles()

    start_ms = candles[0][
        "timestamp"
    ]

    end_ms = candles[-1][
        "timestamp"
    ]

    funding_events = download_funding(
        start_ms,
        end_ms,
    )

    # --------------------------------------------------------
    # Save raw funding used by the backtest.
    # --------------------------------------------------------

    funding_rows = []

    for event in funding_events:
        funding_rows.append(
            {
                "timestamp_utc": iso_utc(
                    event["timestamp"]
                ),
                "funding_rate": event[
                    "funding_rate"
                ],
                "relative_funding_rate": event[
                    "relative_funding_rate"
                ],
            }
        )

    write_csv(
        OUTPUT_FUNDING,
        funding_rows,
    )

    all_trades = []
    all_summaries = []
    all_equity = []

    # --------------------------------------------------------
    # Run A/B/C independently.
    #
    # LONG and SHORT are also run independently.
    #
    # This avoids inventing a directional signal.
    # It tests the hedge mechanism itself.
    # --------------------------------------------------------

    for model_name, model in MODELS.items():
        print()
        print(
            "#" * 70
        )

        print(
            f"MODEL {model_name}"
        )

        print(
            f"Hedge size: "
            f"${model['hedge_notional']:.2f}"
        )

        print(
            "#"
            * 70
        )

        for direction in [
            "LONG",
            "SHORT",
        ]:
            # Important:
            # Each scenario starts with a fresh $2.
            #
            # This makes A LONG, A SHORT, B LONG,
            # etc. independent tests.
            #
            # No overlapping cycles.
            #
            # No artificial summing of simultaneous
            # positions.

            trades, equity_curve = (
                run_scenario(
                    candles=candles,
                    funding_events=[
                        dict(x)
                        for x in funding_events
                    ],
                    model_name=model_name,
                    model=model,
                    direction=direction,
                    maintenance_rate=(
                        maintenance_rate
                    ),
                )
            )

            all_trades.extend(
                trades
            )

            all_equity.extend(
                equity_curve
            )

            final_equity = (
                trades[-1][
                    "ending_equity"
                ]
                if trades
                else CAPITAL_START
            )

            summary = build_summary(
                trades,
                model_name,
                direction,
                final_equity,
            )

            all_summaries.append(
                summary
            )

    # --------------------------------------------------------
    # OUTPUT
    # --------------------------------------------------------

    write_csv(
        OUTPUT_TRADES,
        all_trades,
    )

    write_csv(
        OUTPUT_SUMMARY,
        all_summaries,
    )

    write_csv(
        OUTPUT_EQUITY,
        all_equity,
    )

    # --------------------------------------------------------
    # CONSOLE SUMMARY
    # --------------------------------------------------------

    print()
    print("=" * 90)
    print("FINAL SUMMARY")
    print("=" * 90)

    print(
        f"{'MODEL':<8}"
        f"{'DIR':<8}"
        f"{'CYCLES':>8}"
        f"{'SUCCESS':>12}"
        f"{'LIQ%':>10}"
        f"{'AVG PNL':>12}"
        f"{'MAX LOSS':>12}"
        f"{'FINAL $':>12}"
        f"{'RETURN':>12}"
    )

    print("-" * 90)

    for summary in all_summaries:
        print(
            f"{summary['model']:<8}"
            f"{summary['direction']:<8}"
            f"{summary['cycles']:>8}"
            f"{summary['success_pct']:>11.2f}%"
            f"{summary['liquidation_pct']:>9.2f}%"
            f"${summary['average_net_pnl']:>10.4f}"
            f"${summary['max_loss']:>10.4f}"
            f"${summary['final_equity']:>10.4f}"
            f"{summary['final_return_pct']:>10.2f}%"
        )

    print("=" * 90)

    print()
    print(
        "FILES:"
    )

    print(
        f" - {OUTPUT_TRADES}"
    )

    print(
        f" - {OUTPUT_SUMMARY}"
    )

    print(
        f" - {OUTPUT_EQUITY}"
    )

    print(
        f" - {OUTPUT_FUNDING}"
    )

    print()
    print(
        "BACKTEST COMPLETE."
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print(
            "\nBACKTEST INTERRUPTED."
        )

        sys.exit(130)

    except Exception as exc:
        print()
        print("=" * 70)
        print("BACKTEST FAILED")
        print("=" * 70)

        print(
            type(exc).__name__,
            str(exc),
        )

        traceback.print_exc()

        sys.exit(1)
