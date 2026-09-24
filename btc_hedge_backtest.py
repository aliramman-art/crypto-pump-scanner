# ============================================================
# BTC FUTURES HEDGE BACKTEST
# VERSION 2.1
# ============================================================
#
# KRAKEN FUTURES
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
#   Trigger: 2% adverse move
#   Maximum: 2 hedges
#
# MODELS:
#   A = 50% hedge  -> $5
#   B = 75% hedge  -> $7.50
#   C = 100% hedge -> $10
#
# TARGET:
#   +$0.20 net
#   = +10% of initial $2
#
# PAPER BACKTEST ONLY
# NO REAL ORDERS
#
# IMPORTANT:
#   - Closed 5m candles
#   - No lookahead
#   - Sequential cycles
#   - No overlapping cycles
#   - Long and Short tested separately
#   - Liquidation wins if liquidation and hedge are
#     both inside the same candle
#   - Fees included
#   - Slippage included
#   - Funding included
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

VERSION = "2.1"

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

# Kraken taker fee assumption used by this backtest.
# Change only if you want to model another fee tier.
TAKER_FEE_RATE = 0.0005

# Slippage per execution side.
SLIPPAGE_RATE = 0.0002

# Additional liquidation buffer.
LIQUIDATION_BUFFER_USD = 0.0

CANDLE_SECONDS = 300
CHUNK_CANDLES = 1000

REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 5

KRAKEN_BASE = "https://futures.kraken.com"

CANDLES_URL = (
    KRAKEN_BASE
    + "/api/charts/v1/trade/"
    + SYMBOL
    + "/"
    + TIMEFRAME
)

# ============================================================
# IMPORTANT FIX
#
# Correct Kraken endpoint:
# /derivatives/api/v3/historical-funding-rates
#
# Previous incorrect endpoint:
# /derivatives/api/v3/historicalfundingrates
# ============================================================

FUNDING_URL = (
    KRAKEN_BASE
    + "/derivatives/api/v3/historical-funding-rates"
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
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "BTC-Hedge-Backtest/2.1"
        )
    }
)


# ============================================================
# HELPERS
# ============================================================

def log(message):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{now}] {message}", flush=True)


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default

        if isinstance(value, bool):
            return float(value)

        return float(value)
    except Exception:
        return default


def iso_utc(ts):
    if ts is None:
        return ""

    try:
        ts = float(ts)

        if ts > 10_000_000_000:
            ts /= 1000.0

        dt = datetime.fromtimestamp(
            ts,
            tz=timezone.utc,
        )

        return dt.isoformat()
    except Exception:
        return ""


def timestamp_seconds(value):
    try:
        value = float(value)

        if value > 10_000_000_000:
            value /= 1000.0

        return int(value)
    except Exception:
        return 0


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

            return response.json()

        except Exception as exc:

            last_error = exc

            log(
                f"HTTP attempt "
                f"{attempt}/{retries} failed: "
                f"{url} | {exc}"
            )

            if attempt < retries:
                time.sleep(2 * attempt)

    raise last_error


# ============================================================
# KRAKEN INSTRUMENT
# ============================================================

def load_instrument():

    log("Loading Kraken instrument information...")

    data = request_json(
        INSTRUMENTS_URL
    )

    instruments = []

    if isinstance(data, dict):

        for key in (
            "instruments",
            "result",
            "data",
        ):

            value = data.get(key)

            if isinstance(value, list):
                instruments = value
                break

            if isinstance(value, dict):
                instruments = list(value.values())
                break

    elif isinstance(data, list):

        instruments = data

    if not instruments:
        raise RuntimeError(
            "Could not parse Kraken instruments response."
        )

    target = None

    for item in instruments:

        if not isinstance(item, dict):
            continue

        names = [
            item.get("symbol"),
            item.get("instrument"),
            item.get("ticker"),
            item.get("name"),
        ]

        if SYMBOL in names:
            target = item
            break

    if target is None:

        # Secondary flexible comparison.
        for item in instruments:

            if not isinstance(item, dict):
                continue

            text = str(item)

            if SYMBOL in text:
                target = item
                break

    if target is None:
        raise RuntimeError(
            f"Kraken instrument {SYMBOL} not found."
        )

    maintenance_margin = None
    initial_margin = None

    for key in (
        "maintenanceMargin",
        "maintenance_margin",
        "maintenanceMarginRate",
    ):

        if key in target:
            maintenance_margin = safe_float(
                target.get(key),
                None,
            )

            if maintenance_margin is not None:
                break

    for key in (
        "initialMargin",
        "initial_margin",
        "initialMarginRate",
    ):

        if key in target:
            initial_margin = safe_float(
                target.get(key),
                None,
            )

            if initial_margin is not None:
                break

    # Some Kraken responses express margins as percentages,
    # others may expose them as decimal rates.
    if maintenance_margin is None:
        maintenance_margin = 0.005

    if initial_margin is None:
        initial_margin = 0.01

    if maintenance_margin > 0.10:
        maintenance_margin /= 100.0

    if initial_margin > 0.10:
        initial_margin /= 100.0

    log(
        f"Instrument: {SYMBOL}"
    )

    log(
        f"Type: "
        f"{target.get('type', target.get('instrumentType', 'unknown'))}"
    )

    log(
        f"Tradeable: "
        f"{target.get('tradeable', target.get('tradable', 'unknown'))}"
    )

    log(
        f"Exchange maintenance margin: "
        f"{maintenance_margin * 100:.4f}%"
    )

    log(
        f"Exchange reference initial margin: "
        f"{initial_margin * 100:.4f}%"
    )

    return {
        "raw": target,
        "maintenance_margin": maintenance_margin,
        "initial_margin": initial_margin,
    }


# ============================================================
# CANDLE PARSER
# ============================================================

def parse_candle(item):

    if isinstance(item, dict):

        ts = (
            item.get("time")
            if item.get("time") is not None
            else item.get("timestamp")
        )

        if ts is None:
            ts = item.get("t")

        o = item.get("open")
        h = item.get("high")
        l = item.get("low")
        c = item.get("close")

        if o is None:
            o = item.get("o")

        if h is None:
            h = item.get("h")

        if l is None:
            l = item.get("l")

        if c is None:
            c = item.get("c")

        if None in (ts, o, h, l, c):
            return None

        return {
            "ts": timestamp_seconds(ts),
            "open": safe_float(o),
            "high": safe_float(h),
            "low": safe_float(l),
            "close": safe_float(c),
        }

    if isinstance(item, (list, tuple)):

        if len(item) < 5:
            return None

        # Kraken chart format:
        # [time, open, high, low, close, ...]
        try:

            return {
                "ts": timestamp_seconds(item[0]),
                "open": safe_float(item[1]),
                "high": safe_float(item[2]),
                "low": safe_float(item[3]),
                "close": safe_float(item[4]),
            }

        except Exception:
            return None

    return None


# ============================================================
# CANDLE EXTRACTION
# ============================================================

def extract_candles(data):

    candidates = []

    if isinstance(data, list):
        candidates = data

    elif isinstance(data, dict):

        for key in (
            "candles",
            "data",
            "result",
            "ohlc",
        ):

            value = data.get(key)

            if isinstance(value, list):
                candidates = value
                break

            if isinstance(value, dict):

                for nested_key in (
                    "candles",
                    "data",
                    "result",
                    "ohlc",
                ):

                    nested = value.get(nested_key)

                    if isinstance(nested, list):
                        candidates = nested
                        break

                if candidates:
                    break

    parsed = []

    for item in candidates:

        candle = parse_candle(item)

        if candle is None:
            continue

        if candle["ts"] <= 0:
            continue

        if candle["open"] <= 0:
            continue

        if candle["high"] <= 0:
            continue

        if candle["low"] <= 0:
            continue

        if candle["close"] <= 0:
            continue

        parsed.append(candle)

    parsed.sort(
        key=lambda x: x["ts"]
    )

    return parsed


# ============================================================
# DOWNLOAD CANDLES
# ============================================================

def download_candles():

    end_ts = int(
        datetime.now(timezone.utc).timestamp()
    )

    start_ts = int(
        (
            datetime.now(timezone.utc)
            - timedelta(days=LOOKBACK_DAYS)
        ).timestamp()
    )

    log(
        f"Downloading {LOOKBACK_DAYS} days "
        f"of {TIMEFRAME} candles..."
    )

    all_candles = []

    current = start_ts

    while current < end_ts:

        params = {
            "from": current,
            "to": min(
                end_ts,
                current
                + CHUNK_CANDLES * CANDLE_SECONDS,
            ),
        }

        data = request_json(
            CANDLES_URL,
            params=params,
        )

        batch = extract_candles(data)

        if not batch:
            break

        all_candles.extend(batch)

        newest = max(
            x["ts"]
            for x in batch
        )

        if newest <= current:
            break

        current = newest + CANDLE_SECONDS

        log(
            f"Candles downloaded: "
            f"{len(all_candles):,} "
            f"| latest={iso_utc(newest)}"
        )

        if len(batch) < 10:
            break

        time.sleep(0.15)

    unique = {}

    for candle in all_candles:
        unique[candle["ts"]] = candle

    candles = list(unique.values())

    candles.sort(
        key=lambda x: x["ts"]
    )

    if len(candles) < 100:
        raise RuntimeError(
            "Not enough candles downloaded."
        )

    # Remove currently open candle.
    now_ts = int(
        datetime.now(timezone.utc).timestamp()
    )

    closed = [
        c
        for c in candles
        if c["ts"] + CANDLE_SECONDS <= now_ts
    ]

    log(
        f"Final closed candles: "
        f"{len(closed):,}"
    )

    if closed:

        log(
            f"Range: "
            f"{iso_utc(closed[0]['ts'])} "
            f"-> "
            f"{iso_utc(closed[-1]['ts'])}"
        )

    return closed


# ============================================================
# FUNDING PARSER
# ============================================================

def extract_funding(data):

    raw = []

    if isinstance(data, list):
        raw = data

    elif isinstance(data, dict):

        # Kraken commonly returns result in "rates".
        for key in (
            "rates",
            "fundingRates",
            "funding_rates",
            "data",
            "result",
        ):

            value = data.get(key)

            if isinstance(value, list):
                raw = value
                break

            if isinstance(value, dict):

                for nested_key in (
                    "rates",
                    "fundingRates",
                    "funding_rates",
                    "data",
                    "result",
                ):

                    nested = value.get(nested_key)

                    if isinstance(nested, list):
                        raw = nested
                        break

                if raw:
                    break

    parsed = []

    for item in raw:

        if not isinstance(item, dict):
            continue

        ts = None
        rate = None

        for key in (
            "timestamp",
            "time",
            "fundingTime",
            "fundingTimestamp",
        ):

            if item.get(key) is not None:
                ts = item.get(key)
                break

        for key in (
            "relativeFundingRate",
            "fundingRate",
            "rate",
            "relative_rate",
        ):

            if item.get(key) is not None:
                rate = item.get(key)
                break

        if ts is None or rate is None:
            continue

        ts = timestamp_seconds(ts)
        rate = safe_float(rate, None)

        if ts <= 0 or rate is None:
            continue

        parsed.append(
            {
                "ts": ts,
                "rate": rate,
            }
        )

    parsed.sort(
        key=lambda x: x["ts"]
    )

    unique = {}

    for item in parsed:
        unique[item["ts"]] = item

    return list(
        sorted(
            unique.values(),
            key=lambda x: x["ts"],
        )
    )


# ============================================================
# DOWNLOAD FUNDING
# ============================================================

def download_funding():

    log(
        "Downloading Kraken historical funding rates..."
    )

    # Correct endpoint:
    #
    # /derivatives/api/v3/historical-funding-rates
    #
    # Required parameter:
    # symbol=PF_XBTUSD

    data = request_json(
        FUNDING_URL,
        params={
            "symbol": SYMBOL,
        },
    )

    funding = extract_funding(data)

    if not funding:

        log(
            "WARNING: Kraken returned no funding rows."
        )

        return []

    start_ts = int(
        (
            datetime.now(timezone.utc)
            - timedelta(days=LOOKBACK_DAYS)
        ).timestamp()
    )

    end_ts = int(
        datetime.now(timezone.utc).timestamp()
    )

    funding = [
        x
        for x in funding
        if start_ts <= x["ts"] <= end_ts
    ]

    log(
        f"Funding records: "
        f"{len(funding):,}"
    )

    if funding:

        log(
            f"Funding range: "
            f"{iso_utc(funding[0]['ts'])} "
            f"-> "
            f"{iso_utc(funding[-1]['ts'])}"
        )

        rates = [
            x["rate"]
            for x in funding
        ]

        avg_rate = (
            sum(rates) / len(rates)
            if rates
            else 0
        )

        log(
            f"Average funding rate: "
            f"{avg_rate:.8f}"
        )

    return funding


# ============================================================
# PRICE AT TIMESTAMP
# ============================================================

def price_before_timestamp(
    candles,
    ts,
):

    # Last closed candle at or before timestamp.
    #
    # This avoids using a future candle close
    # to price a funding event.

    lo = 0
    hi = len(candles) - 1
    answer = None

    while lo <= hi:

        mid = (lo + hi) // 2

        if candles[mid]["ts"] <= ts:

            answer = candles[mid]
            lo = mid + 1

        else:

            hi = mid - 1

    if answer is None:
        return None

    return answer["close"]


# ============================================================
# POSITION
# ============================================================

def position_qty(
    notional,
    price,
    direction,
):

    if price <= 0:
        return 0.0

    qty = notional / price

    if direction == "LONG":
        return qty

    return -qty


# ============================================================
# EXECUTION PRICE
# ============================================================

def execution_price(
    market_price,
    direction,
    action,
):

    if market_price <= 0:
        return market_price

    # BUY gets worse price upward.
    # SELL gets worse price downward.

    if action == "BUY":

        return market_price * (
            1.0 + SLIPPAGE_RATE
        )

    return market_price * (
        1.0 - SLIPPAGE_RATE
    )


def action_for_direction(
    direction,
):

    if direction == "LONG":
        return "BUY"

    return "SELL"


# ============================================================
# FEE
# ============================================================

def fee_for_notional(
    notional,
):

    return abs(notional) * TAKER_FEE_RATE


# ============================================================
# UNREALIZED PNL
# ============================================================

def position_unrealized_pnl(
    position,
    mark_price,
):

    return (
        position["qty"]
        * (
            mark_price
            - position["entry_price"]
        )
    )


def total_unrealized_pnl(
    positions,
    mark_price,
):

    total = 0.0

    for p in positions:
        total += position_unrealized_pnl(
            p,
            mark_price,
        )

    return total


# ============================================================
# POSITION NOTIONAL
# ============================================================

def total_notional(
    positions,
    mark_price,
):

    total = 0.0

    for p in positions:

        total += abs(
            p["qty"] * mark_price
        )

    return total


# ============================================================
# EQUITY
# ============================================================

def account_equity(
    cash,
    positions,
    mark_price,
):

    return (
        cash
        + total_unrealized_pnl(
            positions,
            mark_price,
        )
    )


# ============================================================
# MAINTENANCE MARGIN
# ============================================================

def maintenance_margin_required(
    positions,
    mark_price,
    maintenance_margin_rate,
):

    notional = total_notional(
        positions,
        mark_price,
    )

    return (
        notional
        * maintenance_margin_rate
    )


# ============================================================
# LIQUIDATION TEST
# ============================================================

def is_liquidated(
    cash,
    positions,
    mark_price,
    maintenance_margin_rate,
):

    if not positions:
        return False

    equity = account_equity(
        cash,
        positions,
        mark_price,
    )

    maintenance = (
        maintenance_margin_required(
            positions,
            mark_price,
            maintenance_margin_rate,
        )
        + LIQUIDATION_BUFFER_USD
    )

    return equity <= maintenance


# ============================================================
# APPROXIMATE LIQUIDATION PRICE
# ============================================================

def approximate_liquidation_price(
    cash,
    positions,
    maintenance_margin_rate,
):

    if not positions:
        return None

    net_qty = sum(
        p["qty"]
        for p in positions
    )

    if abs(net_qty) < 1e-12:
        return None

    # Equity(P):
    #
    # cash + sum(qty * (P - entry))
    #
    # Maintenance(P):
    #
    # abs(net exposure) * P * mm
    #
    # Solve:
    #
    # cash
    # + P*net_qty
    # - sum(qty*entry)
    # =
    # abs(net_qty)*P*mm

    constant = (
        cash
        - sum(
            p["qty"] * p["entry_price"]
            for p in positions
        )
    )

    abs_qty = abs(net_qty)

    denominator = (
        net_qty
        - abs_qty * maintenance_margin_rate
    )

    if abs(denominator) < 1e-12:
        return None

    price = (
        -constant
        / denominator
    )

    if price <= 0:
        return None

    return price


# ============================================================
# HEDGE DIRECTION
# ============================================================

def hedge_direction(
    original_direction,
):

    if original_direction == "LONG":
        return "SHORT"

    return "LONG"


# ============================================================
# HEDGE TRIGGER PRICE
# ============================================================

def hedge_trigger_price(
    original_entry_price,
    original_direction,
    hedge_number,
):

    adverse_pct = (
        HEDGE_TRIGGER_PCT
        * hedge_number
    )

    if original_direction == "LONG":

        return (
            original_entry_price
            * (1.0 - adverse_pct)
        )

    return (
        original_entry_price
        * (1.0 + adverse_pct)
    )


# ============================================================
# TARGET CHECK
# ============================================================

def projected_exit_equity(
    cash,
    positions,
    exit_market_price,
):

    if not positions:
        return cash

    gross = total_unrealized_pnl(
        positions,
        exit_market_price,
    )

    closing_notional = sum(
        abs(
            p["qty"]
            * exit_market_price
        )
        for p in positions
    )

    closing_fee = fee_for_notional(
        closing_notional
    )

    closing_slippage = (
        closing_notional
        * SLIPPAGE_RATE
    )

    return (
        cash
        + gross
        - closing_fee
        - closing_slippage
    )


def target_reached(
    cash,
    positions,
    market_price,
):

    target_equity = (
        CAPITAL_START
        + TARGET_NET_PROFIT
    )

    projected = projected_exit_equity(
        cash,
        positions,
        market_price,
    )

    return projected >= target_equity


# ============================================================
# TARGET PRICE SOLVER
# ============================================================

def find_target_price(
    cash,
    positions,
    current_price,
):

    if not positions:
        return None

    target_equity = (
        CAPITAL_START
        + TARGET_NET_PROFIT
    )

    net_qty = sum(
        p["qty"]
        for p in positions
    )

    if abs(net_qty) < 1e-12:
        return None

    constant = (
        cash
        - sum(
            p["qty"]
            * p["entry_price"]
            for p in positions
        )
    )

    closing_fee_rate = (
        TAKER_FEE_RATE
        + SLIPPAGE_RATE
    )

    denominator = (
        net_qty
        - abs(net_qty)
        * closing_fee_rate
    )

    if abs(denominator) < 1e-12:
        return None

    target_price = (
        target_equity
        - constant
    ) / denominator

    if target_price <= 0:
        return None

    return target_price


# ============================================================
# MAX ADVERSE PRICE
# ============================================================

def update_max_adverse(
    cycle,
    candle,
    original_direction,
):

    if original_direction == "LONG":

        adverse_price = candle["low"]

        if adverse_price < cycle["min_price"]:
            cycle["min_price"] = adverse_price

    else:

        adverse_price = candle["high"]

        if adverse_price > cycle["max_price"]:
            cycle["max_price"] = adverse_price


# ============================================================
# MDD
# ============================================================

def update_drawdown(
    cycle,
    equity,
):

    if equity > cycle["equity_peak"]:
        cycle["equity_peak"] = equity

    if cycle["equity_peak"] <= 0:
        return

    dd = (
        equity
        - cycle["equity_peak"]
    ) / cycle["equity_peak"]

    if dd < cycle["max_drawdown"]:
        cycle["max_drawdown"] = dd


# ============================================================
# OPEN POSITION
# ============================================================

def open_position(
    positions,
    cash,
    market_price,
    direction,
    notional,
    label,
    event_ts,
):

    action = action_for_direction(
        direction
    )

    exec_price = execution_price(
        market_price,
        direction,
        action,
    )

    qty = position_qty(
        notional,
        exec_price,
        direction,
    )

    fee = fee_for_notional(
        notional
    )

    cash -= fee

    slippage_usd = (
        abs(qty)
        * abs(exec_price - market_price)
    )

    position = {
        "label": label,
        "direction": direction,
        "qty": qty,
        "notional": notional,
        "entry_price": exec_price,
        "market_entry_price": market_price,
        "fee": fee,
        "slippage": slippage_usd,
        "opened_ts": event_ts,
    }

    positions.append(
        position
    )

    return cash, position


# ============================================================
# CLOSE ALL
# ============================================================

def close_all_positions(
    positions,
    cash,
    market_price,
    event_ts,
):

    gross_raw = 0.0
    closing_fees = 0.0
    closing_slippage = 0.0

    exit_price_for_report = market_price

    for p in positions:

        if p["qty"] > 0:

            action = "SELL"

        else:

            action = "BUY"

        exec_price = execution_price(
            market_price,
            p["direction"],
            action,
        )

        pnl = (
            p["qty"]
            * (
                exec_price
                - p["entry_price"]
            )
        )

        notional = abs(
            p["qty"]
            * exec_price
        )

        fee = fee_for_notional(
            notional
        )

        slippage = (
            abs(p["qty"])
            * abs(
                exec_price
                - market_price
            )
        )

        gross_raw += pnl
        closing_fees += fee
        closing_slippage += slippage

        exit_price_for_report = exec_price

    cash_after = (
        cash
        + gross_raw
        - closing_fees
    )

    return {
        "cash_after": cash_after,
        "gross_raw": gross_raw,
        "closing_fees": closing_fees,
        "closing_slippage": closing_slippage,
        "exit_price": exit_price_for_report,
        "exit_ts": event_ts,
    }


# ============================================================
# FUNDING APPLICATION
# ============================================================

def apply_funding_events(
    cycle,
    cash,
    positions,
    funding,
    funding_index,
    current_ts,
    candles,
):

    funding_total = 0.0
    events_applied = []

    while (
        funding_index < len(funding)
        and funding[funding_index]["ts"] <= current_ts
    ):

        event = funding[funding_index]

        event_ts = event["ts"]

        # Only funding after cycle entry.
        if event_ts < cycle["entry_ts"]:
            funding_index += 1
            continue

        price = price_before_timestamp(
            candles,
            event_ts,
        )

        if price is None:

            # Do not apply if there is no safe
            # historical price available.
            funding_index += 1
            continue

        rate = event["rate"]

        event_total = 0.0

        for p in positions:

            notional = abs(
                p["qty"]
                * price
            )

            # Positive funding:
            # Long pays / Short receives.
            #
            # Negative funding:
            # Long receives / Short pays.

            payment = (
                math.copysign(
                    notional * rate,
                    p["qty"],
                )
            )

            cash -= payment

            event_total += payment

        funding_total += event_total

        events_applied.append(
            {
                "cycle_id": cycle["cycle_id"],
                "model": cycle["model"],
                "direction": cycle["direction"],
                "funding_ts": event_ts,
                "funding_time": iso_utc(event_ts),
                "rate": rate,
                "price": price,
                "payment": event_total,
            }
        )

        funding_index += 1

    return (
        cash,
        funding_index,
        funding_total,
        events_applied,
    )


# ============================================================
# CYCLE RESULT
# ============================================================

def empty_cycle(
    cycle_id,
    model,
    direction,
    start_equity,
    entry_ts,
):

    return {
        "cycle_id": cycle_id,
        "model": model,
        "direction": direction,

        "start_equity": start_equity,

        "entry_ts": entry_ts,
        "entry_time": iso_utc(entry_ts),
        "entry_price": 0.0,

        "hedge1_ts": "",
        "hedge1_time": "",
        "hedge1_price": 0.0,
        "hedge1_size": 0.0,

        "hedge2_ts": "",
        "hedge2_time": "",
        "hedge2_price": 0.0,
        "hedge2_size": 0.0,

        "exit_ts": "",
        "exit_time": "",
        "exit_price": 0.0,

        "exit_reason": "",

        "gross_pnl": 0.0,
        "fees": 0.0,
        "slippage": 0.0,
        "funding": 0.0,
        "net_pnl": 0.0,

        "final_equity": start_equity,

        "max_drawdown_pct": 0.0,
        "max_adverse_pct": 0.0,

        "liquidation_price": 0.0,
        "liquidation_distance_pct": 0.0,

        "hedges_used": 0,
        "hedges_blocked": 0,

        "duration_hours": 0.0,

        "target_reached": False,
    }


# ============================================================
# RUN ONE CYCLE
# ============================================================

def run_cycle(
    model_name,
    hedge_fraction,
    direction,
    candles,
    funding,
    maintenance_margin_rate,
    cycle_id,
    start_index,
    starting_equity,
):

    if start_index >= len(candles):
        return None, start_index

    entry_candle = candles[start_index]

    entry_market = entry_candle["open"]

    result = empty_cycle(
        cycle_id,
        model_name,
        direction,
        starting_equity,
        entry_candle["ts"],
    )

    positions = []

    cash = starting_equity

    total_fees = 0.0
    total_slippage = 0.0
    total_funding = 0.0

    # --------------------------------------------------------
    # Initial margin check
    # --------------------------------------------------------

    initial_fee = fee_for_notional(
        INITIAL_NOTIONAL
    )

    if cash < (
        INITIAL_MARGIN
        + initial_fee
    ):

        result["exit_reason"] = (
            "INSUFFICIENT_MARGIN"
        )

        result["exit_ts"] = entry_candle["ts"]
        result["exit_time"] = iso_utc(
            entry_candle["ts"]
        )

        result["final_equity"] = cash

        return result, start_index + 1

    # --------------------------------------------------------
    # Initial position
    # --------------------------------------------------------

    cash, initial_position = open_position(
        positions=positions,
        cash=cash,
        market_price=entry_market,
        direction=direction,
        notional=INITIAL_NOTIONAL,
        label="INITIAL",
        event_ts=entry_candle["ts"],
    )

    result["entry_price"] = (
        initial_position["entry_price"]
    )

    total_fees += (
        initial_position["fee"]
    )

    total_slippage += (
        initial_position["slippage"]
    )

    result["equity_peak"] = (
        cash
    )

    result["min_price"] = (
        entry_market
    )

    result["max_price"] = (
        entry_market
    )

    hedge_count = 0

    funding_index = 0

    while (
        funding_index < len(funding)
        and funding[funding_index]["ts"]
        < entry_candle["ts"]
    ):
        funding_index += 1

    # --------------------------------------------------------
    # Scan forward
    # --------------------------------------------------------

    exit_index = start_index

    for i in range(
        start_index,
        len(candles),
    ):

        candle = candles[i]

        exit_index = i

        # ----------------------------------------------------
        # Funding
        # ----------------------------------------------------

        (
            cash,
            funding_index,
            funding_delta,
            funding_events,
        ) = apply_funding_events(
            cycle=result,
            cash=cash,
            positions=positions,
            funding=funding,
            funding_index=funding_index,
            current_ts=(
                candle["ts"]
                + CANDLE_SECONDS
                - 1
            ),
            candles=candles,
        )

        total_funding += funding_delta

        # ----------------------------------------------------
        # Update adverse movement
        # ----------------------------------------------------

        update_max_adverse(
            result,
            candle,
            direction,
        )

        # ----------------------------------------------------
        # Conservative liquidation check
        #
        # If liquidation and hedge trigger are both
        # inside the same candle, liquidation wins.
        # ----------------------------------------------------

        if is_liquidated(
            cash,
            positions,
            candle["low"],
            maintenance_margin_rate,
        ) or is_liquidated(
            cash,
            positions,
            candle["high"],
            maintenance_margin_rate,
        ):

            # We need to identify which side can liquidate.
            #
            # Long exposure -> low is dangerous.
            # Short exposure -> high is dangerous.
            #
            # If net exposure is flat, liquidation is unlikely
            # through directional price movement, but fees/funding
            # can still reduce equity.

            net_qty = sum(
                p["qty"]
                for p in positions
            )

            if net_qty > 0:

                liq_price = candle["low"]

            elif net_qty < 0:

                liq_price = candle["high"]

            else:

                liq_price = candle["close"]

            result["exit_ts"] = candle["ts"]
            result["exit_time"] = iso_utc(
                candle["ts"]
            )

            result["exit_price"] = liq_price

            result["exit_reason"] = (
                "LIQUIDATION"
            )

            gross = total_unrealized_pnl(
                positions,
                liq_price,
            )

            notional = total_notional(
                positions,
                liq_price,
            )

            liquidation_fee = fee_for_notional(
                notional
            )

            final_equity = (
                cash
                + gross
                - liquidation_fee
            )

            result["gross_pnl"] = gross

            result["fees"] = (
                total_fees
                + liquidation_fee
            )

            result["slippage"] = (
                total_slippage
            )

            result["funding"] = (
                total_funding
            )

            result["net_pnl"] = (
                final_equity
                - starting_equity
            )

            result["final_equity"] = (
                final_equity
            )

            result["hedges_used"] = hedge_count

            result["liquidation_price"] = (
                approximate_liquidation_price(
                    cash,
                    positions,
                    maintenance_margin_rate,
                )
                or liq_price
            )

            if direction == "LONG":

                result["max_adverse_pct"] = (
                    max(
                        0.0,
                        (
                            result["entry_price"]
                            - result["min_price"]
                        )
                        / result["entry_price"]
                    )
                    * 100.0
                )

            else:

                result["max_adverse_pct"] = (
                    max(
                        0.0,
                        (
                            result["max_price"]
                            - result["entry_price"]
                        )
                        / result["entry_price"]
                    )
                    * 100.0
                )

            result["duration_hours"] = (
                result["exit_ts"]
                - result["entry_ts"]
            ) / 3600.0

            return result, exit_index + 1

        # ----------------------------------------------------
        # HEDGE LOGIC
        # ----------------------------------------------------

        while hedge_count < MAX_HEDGES:

            hedge_number = hedge_count + 1

            trigger = hedge_trigger_price(
                result["entry_price"],
                direction,
                hedge_number,
            )

            if direction == "LONG":

                trigger_hit = (
                    candle["low"]
                    <= trigger
                )

            else:

                trigger_hit = (
                    candle["high"]
                    >= trigger
                )

            if not trigger_hit:
                break

            hedge_notional = (
                INITIAL_NOTIONAL
                * hedge_fraction
            )

            required_margin = (
                hedge_notional
                / LEVERAGE
            )

            hedge_fee = fee_for_notional(
                hedge_notional
            )

            # Conservative available margin:
            # initial + all existing hedge positions
            # + this new hedge.
            current_margin = sum(
                abs(
                    p["notional"]
                ) / LEVERAGE
                for p in positions
            )

            if cash < (
                current_margin
                + required_margin
                + hedge_fee
            ):

                result["hedges_blocked"] += 1

                break

            hedge_direction_value = hedge_direction(
                direction
            )

            cash, hedge_position = open_position(
                positions=positions,
                cash=cash,
                market_price=trigger,
                direction=hedge_direction_value,
                notional=hedge_notional,
                label=f"HEDGE_{hedge_number}",
                event_ts=candle["ts"],
            )

            hedge_count += 1

            total_fees += (
                hedge_position["fee"]
            )

            total_slippage += (
                hedge_position["slippage"]
            )

            if hedge_number == 1:

                result["hedge1_ts"] = (
                    candle["ts"]
                )

                result["hedge1_time"] = (
                    iso_utc(candle["ts"])
                )

                result["hedge1_price"] = (
                    hedge_position["entry_price"]
                )

                result["hedge1_size"] = (
                    hedge_notional
                )

            elif hedge_number == 2:

                result["hedge2_ts"] = (
                    candle["ts"]
                )

                result["hedge2_time"] = (
                    iso_utc(candle["ts"])
                )

                result["hedge2_price"] = (
                    hedge_position["entry_price"]
                )

                result["hedge2_size"] = (
                    hedge_notional
                )

            # Recheck liquidation immediately after hedge.
            if is_liquidated(
                cash,
                positions,
                trigger,
                maintenance_margin_rate,
            ):

                result["exit_ts"] = candle["ts"]
                result["exit_time"] = iso_utc(
                    candle["ts"]
                )

                result["exit_price"] = trigger

                result["exit_reason"] = (
                    "LIQUIDATION"
                )

                gross = total_unrealized_pnl(
                    positions,
                    trigger,
                )

                notional = total_notional(
                    positions,
                    trigger,
                )

                liq_fee = fee_for_notional(
                    notional
                )

                final_equity = (
                    cash
                    + gross
                    - liq_fee
                )

                result["gross_pnl"] = gross

                result["fees"] = (
                    total_fees
                    + liq_fee
                )

                result["slippage"] = (
                    total_slippage
                )

                result["funding"] = (
                    total_funding
                )

                result["net_pnl"] = (
                    final_equity
                    - starting_equity
                )

                result["final_equity"] = (
                    final_equity
                )

                result["hedges_used"] = (
                    hedge_count
                )

                result["liquidation_price"] = (
                    approximate_liquidation_price(
                        cash,
                        positions,
                        maintenance_margin_rate,
                    )
                    or trigger
                )

                result["duration_hours"] = (
                    result["exit_ts"]
                    - result["entry_ts"]
                ) / 3600.0

                if direction == "LONG":

                    result["max_adverse_pct"] = (
                        max(
                            0.0,
                            (
                                result["entry_price"]
                                - result["min_price"]
                            )
                            / result["entry_price"]
                        )
                        * 100.0
                    )

                else:

                    result["max_adverse_pct"] = (
                        max(
                            0.0,
                            (
                                result["max_price"]
                                - result["entry_price"]
                            )
                            / result["entry_price"]
                        )
                        * 100.0
                    )

                return result, exit_index + 1

            # Continue while-loop in case second hedge
            # is also reached in the same candle.

        # ----------------------------------------------------
        # Current equity
        # ----------------------------------------------------

        mark_price = candle["close"]

        equity = account_equity(
            cash,
            positions,
            mark_price,
        )

        update_drawdown(
            result,
            equity,
        )

        # ----------------------------------------------------
        # TARGET
        # ----------------------------------------------------

        if target_reached(
            cash,
            positions,
            mark_price,
        ):

            close_data = close_all_positions(
                positions,
                cash,
                mark_price,
                candle["ts"],
            )

            final_equity = (
                close_data["cash_after"]
            )

            result["exit_ts"] = (
                close_data["exit_ts"]
            )

            result["exit_time"] = (
                iso_utc(
                    close_data["exit_ts"]
                )
            )

            result["exit_price"] = (
                close_data["exit_price"]
            )

            result["exit_reason"] = (
                "TARGET"
            )

            result["gross_pnl"] = (
                close_data["gross_raw"]
            )

            result["fees"] = (
                total_fees
                + close_data["closing_fees"]
            )

            result["slippage"] = (
                total_slippage
                + close_data["closing_slippage"]
            )

            result["funding"] = (
                total_funding
            )

            result["final_equity"] = (
                final_equity
            )

            result["net_pnl"] = (
                final_equity
                - starting_equity
            )

            result["hedges_used"] = (
                hedge_count
            )

            result["target_reached"] = True

            result["duration_hours"] = (
                result["exit_ts"]
                - result["entry_ts"]
            ) / 3600.0

            if direction == "LONG":

                result["max_adverse_pct"] = (
                    max(
                        0.0,
                        (
                            result["entry_price"]
                            - result["min_price"]
                        )
                        / result["entry_price"]
                    )
                    * 100.0
                )

            else:

                result["max_adverse_pct"] = (
                    max(
                        0.0,
                        (
                            result["max_price"]
                            - result["entry_price"]
                        )
                        / result["entry_price"]
                    )
                    * 100.0
                )

            liq = approximate_liquidation_price(
                cash,
                positions,
                maintenance_margin_rate,
            )

            result["liquidation_price"] = (
                liq or 0.0
            )

            if liq:

                result["liquidation_distance_pct"] = (
                    abs(
                        mark_price - liq
                    )
                    / mark_price
                    * 100.0
                )

            return result, exit_index + 1

    # --------------------------------------------------------
    # Data ended while position open
    # --------------------------------------------------------

    last_candle = candles[-1]

    close_data = close_all_positions(
        positions,
        cash,
        last_candle["close"],
        last_candle["ts"],
    )

    final_equity = (
        close_data["cash_after"]
    )

    result["exit_ts"] = (
        close_data["exit_ts"]
    )

    result["exit_time"] = iso_utc(
        close_data["exit_ts"]
    )

    result["exit_price"] = (
        close_data["exit_price"]
    )

    result["exit_reason"] = (
        "DATA_END"
    )

    result["gross_pnl"] = (
        close_data["gross_raw"]
    )

    result["fees"] = (
        total_fees
        + close_data["closing_fees"]
    )

    result["slippage"] = (
        total_slippage
        + close_data["closing_slippage"]
    )

    result["funding"] = (
        total_funding
    )

    result["final_equity"] = (
        final_equity
    )

    result["net_pnl"] = (
        final_equity
        - starting_equity
    )

    result["hedges_used"] = (
        hedge_count
    )

    result["duration_hours"] = (
        result["exit_ts"]
        - result["entry_ts"]
    ) / 3600.0

    if direction == "LONG":

        result["max_adverse_pct"] = (
            max(
                0.0,
                (
                    result["entry_price"]
                    - result["min_price"]
                )
                / result["entry_price"]
            )
            * 100.0
        )

    else:

        result["max_adverse_pct"] = (
            max(
                0.0,
                (
                    result["max_price"]
                    - result["entry_price"]
                )
                / result["entry_price"]
            )
            * 100.0
        )

    liq = approximate_liquidation_price(
        cash,
        positions,
        maintenance_margin_rate,
    )

    result["liquidation_price"] = (
        liq or 0.0
    )

    return result, len(candles)


# ============================================================
# RUN SCENARIO
# ============================================================

def run_scenario(
    model_name,
    hedge_fraction,
    direction,
    candles,
    funding,
    maintenance_margin_rate,
):

    log("")
    log("=" * 70)

    log(
        f"SCENARIO: "
        f"{model_name} | "
        f"{direction}"
    )

    log("=" * 70)

    results = []

    equity_rows = []

    starting_equity = CAPITAL_START

    start_index = 0

    cycle_id = 0

    while (
        start_index < len(candles) - 1
    ):

        cycle_id += 1

        result, next_index = run_cycle(
            model_name=model_name,
            hedge_fraction=hedge_fraction,
            direction=direction,
            candles=candles,
            funding=funding,
            maintenance_margin_rate=(
                maintenance_margin_rate
            ),
            cycle_id=cycle_id,
            start_index=start_index,
            starting_equity=starting_equity,
        )

        if result is None:
            break

        results.append(result)

        starting_equity = max(
            0.0,
            result["final_equity"],
        )

        equity_rows.append(
            {
                "model": model_name,
                "direction": direction,
                "cycle_id": cycle_id,
                "entry_time": result["entry_time"],
                "exit_time": result["exit_time"],
                "equity": starting_equity,
                "net_pnl": result["net_pnl"],
                "exit_reason": result["exit_reason"],
            }
        )

        if cycle_id % 25 == 0:

            log(
                f"{model_name} "
                f"{direction} | "
                f"cycles={cycle_id} | "
                f"equity=${starting_equity:.4f}"
            )

        if starting_equity <= 0:

            log(
                f"{model_name} "
                f"{direction} stopped: "
                f"equity depleted."
            )

            break

        # Sequential cycles only.
        start_index = max(
            next_index,
            start_index + 1,
        )

    return results, equity_rows


# ============================================================
# SUMMARY
# ============================================================

def summarize(
    results,
    model_name,
    direction,
):

    count = len(results)

    if count == 0:

        return {
            "model": model_name,
            "direction": direction,
            "cycles": 0,
            "success_count": 0,
            "success_pct": 0.0,
            "liquidations": 0,
            "liquidation_pct": 0.0,
            "avg_profit": 0.0,
            "avg_loss": 0.0,
            "max_loss": 0.0,
            "total_net_pnl": 0.0,
            "final_equity": CAPITAL_START,
            "final_return_pct": 0.0,
            "avg_duration_hours": 0.0,
            "avg_max_drawdown_pct": 0.0,
            "worst_max_drawdown_pct": 0.0,
            "avg_funding": 0.0,
            "avg_fees": 0.0,
            "avg_slippage": 0.0,
            "target_profit": TARGET_NET_PROFIT,
            "target_reached_cycles": 0,
        }

    successes = [
        r
        for r in results
        if r["target_reached"]
    ]

    liquidations = [
        r
        for r in results
        if r["exit_reason"]
        == "LIQUIDATION"
    ]

    profits = [
        r["net_pnl"]
        for r in results
        if r["net_pnl"] > 0
    ]

    losses = [
        r["net_pnl"]
        for r in results
        if r["net_pnl"] < 0
    ]

    final_equity = results[-1][
        "final_equity"
    ]

    total_net_pnl = (
        final_equity
        - CAPITAL_START
    )

    return {
        "model": model_name,
        "direction": direction,

        "cycles": count,

        "success_count": len(successes),

        "success_pct": (
            len(successes)
            / count
            * 100.0
        ),

        "liquidations": len(
            liquidations
        ),

        "liquidation_pct": (
            len(liquidations)
            / count
            * 100.0
        ),

        "avg_profit": (
            sum(profits)
            / len(profits)
            if profits
            else 0.0
        ),

        "avg_loss": (
            sum(losses)
            / len(losses)
            if losses
            else 0.0
        ),

        "max_loss": (
            min(
                (
                    r["net_pnl"]
                    for r in results
                ),
                default=0.0,
            )
        ),

        "total_net_pnl": total_net_pnl,

        "final_equity": final_equity,

        "final_return_pct": (
            total_net_pnl
            / CAPITAL_START
            * 100.0
        ),

        "avg_duration_hours": (
            sum(
                r["duration_hours"]
                for r in results
            )
            / count
        ),

        "avg_max_drawdown_pct": (
            sum(
                r["max_drawdown_pct"]
                for r in results
            )
            / count
            * 100.0
        ),

        "worst_max_drawdown_pct": (
            min(
                (
                    r["max_drawdown"]
                    for r in results
                ),
                default=0.0,
            )
            * 100.0
        ),

        "avg_funding": (
            sum(
                r["funding"]
                for r in results
            )
            / count
        ),

        "avg_fees": (
            sum(
                r["fees"]
                for r in results
            )
            / count
        ),

        "avg_slippage": (
            sum(
                r["slippage"]
                for r in results
            )
            / count
        ),

        "target_profit": TARGET_NET_PROFIT,

        "target_reached_cycles": len(
            successes
        ),
    }


# ============================================================
# CSV WRITERS
# ============================================================

def write_csv(
    filename,
    rows,
    fieldnames,
):

    with open(
        filename,
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )

        writer.writeheader()

        for row in rows:

            writer.writerow(row)


# ============================================================
# MAIN
# ============================================================

def main():

    print("")
    print("=" * 70)
    print(
        "BTC FUTURES HEDGE BACKTEST "
        f"VERSION {VERSION}"
    )
    print("=" * 70)

    print("")
    print("PAPER TRADING ONLY")
    print(
        f"Capital: ${CAPITAL_START:.2f}"
    )
    print(
        f"Leverage: {LEVERAGE:.0f}x"
    )
    print(
        f"Initial notional: "
        f"${INITIAL_NOTIONAL:.2f}"
    )
    print(
        f"Hedge trigger: "
        f"{HEDGE_TRIGGER_PCT * 100:.2f}%"
    )
    print(
        f"Max hedges: "
        f"{MAX_HEDGES}"
    )
    print(
        f"Target: "
        f"+${TARGET_NET_PROFIT:.2f}"
    )
    print(
        f"Taker fee model: "
        f"{TAKER_FEE_RATE * 100:.4f}%"
    )
    print(
        f"Slippage model: "
        f"{SLIPPAGE_RATE * 100:.4f}%"
    )

    print("")
    print(
        "REAL_TRADING = "
        f"{REAL_TRADING}"
    )

    if REAL_TRADING:
        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    # --------------------------------------------------------
    # Instrument
    # --------------------------------------------------------

    instrument = load_instrument()

    maintenance_margin_rate = (
        instrument["maintenance_margin"]
    )

    # --------------------------------------------------------
    # Candles
    # --------------------------------------------------------

    candles = download_candles()

    # --------------------------------------------------------
    # Funding
    # --------------------------------------------------------

    funding = download_funding()

    # --------------------------------------------------------
    # Run all scenarios
    # --------------------------------------------------------

    all_trades = []
    all_summaries = []
    all_equity = []

    for model_name, fraction in (
        HEDGE_MODELS.items()
    ):

        for direction in (
            "LONG",
            "SHORT",
        ):

            results, equity_rows = (
                run_scenario(
                    model_name=model_name,
                    hedge_fraction=fraction,
                    direction=direction,
                    candles=candles,
                    funding=funding,
                    maintenance_margin_rate=(
                        maintenance_margin_rate
                    ),
                )
            )

            all_trades.extend(
                results
            )

            all_equity.extend(
                equity_rows
            )

            summary = summarize(
                results,
                model_name,
                direction,
            )

            all_summaries.append(
                summary
            )

            log(
                f"RESULT "
                f"{model_name} "
                f"{direction} | "
                f"cycles={summary['cycles']} | "
                f"success="
                f"{summary['success_pct']:.2f}% | "
                f"liq="
                f"{summary['liquidations']} | "
                f"final="
                f"${summary['final_equity']:.4f} | "
                f"return="
                f"{summary['final_return_pct']:.2f}%"
            )

    # --------------------------------------------------------
    # Trades CSV
    # --------------------------------------------------------

    trade_fields = [
        "cycle_id",
        "model",
        "direction",

        "start_equity",

        "entry_ts",
        "entry_time",
        "entry_price",

        "hedge1_ts",
        "hedge1_time",
        "hedge1_price",
        "hedge1_size",

        "hedge2_ts",
        "hedge2_time",
        "hedge2_price",
        "hedge2_size",

        "exit_ts",
        "exit_time",
        "exit_price",
        "exit_reason",

        "gross_pnl",
        "fees",
        "slippage",
        "funding",
        "net_pnl",

        "final_equity",

        "max_drawdown_pct",
        "max_adverse_pct",

        "liquidation_price",
        "liquidation_distance_pct",

        "hedges_used",
        "hedges_blocked",

        "duration_hours",

        "target_reached",
    ]

    write_csv(
        OUTPUT_TRADES,
        all_trades,
        trade_fields,
    )

    # --------------------------------------------------------
    # Summary CSV
    # --------------------------------------------------------

    summary_fields = [
        "model",
        "direction",
        "cycles",
        "success_count",
        "success_pct",
        "liquidations",
        "liquidation_pct",
        "avg_profit",
        "avg_loss",
        "max_loss",
        "total_net_pnl",
        "final_equity",
        "final_return_pct",
        "avg_duration_hours",
        "avg_max_drawdown_pct",
        "worst_max_drawdown_pct",
        "avg_funding",
        "avg_fees",
        "avg_slippage",
        "target_profit",
        "target_reached_cycles",
    ]

    write_csv(
        OUTPUT_SUMMARY,
        all_summaries,
        summary_fields,
    )

    # --------------------------------------------------------
    # Equity CSV
    # --------------------------------------------------------

    equity_fields = [
        "model",
        "direction",
        "cycle_id",
        "entry_time",
        "exit_time",
        "equity",
        "net_pnl",
        "exit_reason",
    ]

    write_csv(
        OUTPUT_EQUITY,
        all_equity,
        equity_fields,
    )

    # --------------------------------------------------------
    # Funding CSV
    # --------------------------------------------------------

    funding_rows = []

    for item in funding:

        funding_rows.append(
            {
                "funding_ts": item["ts"],
                "funding_time": iso_utc(
                    item["ts"]
                ),
                "rate": item["rate"],
            }
        )

    write_csv(
        OUTPUT_FUNDING,
        funding_rows,
        [
            "funding_ts",
            "funding_time",
            "rate",
        ],
    )

    # --------------------------------------------------------
    # Console summary
    # --------------------------------------------------------

    print("")
    print("=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)

    for s in all_summaries:

        print(
            f"{s['model']:>5} "
            f"{s['direction']:<5} | "
            f"cycles={s['cycles']:>5} | "
            f"success={s['success_pct']:>7.2f}% | "
            f"liq={s['liquidations']:>4} | "
            f"avgPnL="
            f"${s['avg_profit']:>8.4f} | "
            f"maxLoss="
            f"${s['max_loss']:>8.4f} | "
            f"final="
            f"${s['final_equity']:>8.4f} | "
            f"return="
            f"{s['final_return_pct']:>8.2f}%"
        )

    print("")
    print("=" * 70)
    print("FILES")
    print("=" * 70)

    for filename in (
        OUTPUT_TRADES,
        OUTPUT_SUMMARY,
        OUTPUT_EQUITY,
        OUTPUT_FUNDING,
    ):

        print(
            f"Created: {filename}"
        )

    print("")
    print("BACKTEST COMPLETE.")
    print("PAPER ONLY. NO REAL ORDERS.")


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print("")
        print(
            "Interrupted by user."
        )

        sys.exit(130)

    except Exception as exc:

        print("")
        print("=" * 70)
        print("BACKTEST FAILED")
        print("=" * 70)

        print(
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        traceback.print_exc()

        sys.exit(1)
