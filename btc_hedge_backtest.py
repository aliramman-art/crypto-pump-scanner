# ============================================================
# BTCUSDT FUTURES HEDGE BACKTEST
# VERSION 1.0
# ============================================================
#
# CAPITAL              = $2.00
# LEVERAGE             = 10x
# INITIAL MARGIN       = $1.00
# INITIAL POSITION     = $10.00
#
# HEDGE TRIGGER        = 2%
# HEDGE #1             = 50% / 75% / 100%
# HEDGE #2             = same hedge size
# MAX HEDGES           = 2
#
# TARGET NET PROFIT    = +$0.20
#
# DATA:
#   Binance USD-M Futures
#   BTCUSDT
#   5-minute candles
#   6 months
#
# IMPORTANT:
#   Conservative intrabar handling.
#
#   If a candle contains both:
#       HEDGE TRIGGER
#   and:
#       LIQUIDATION
#
#   we DO NOT assume hedge happened first.
#
#   Liquidation wins whenever the candle is ambiguous.
#
# PAPER BACKTEST ONLY
# ============================================================

import csv
import io
import math
import os
import time
from datetime import datetime, timezone, timedelta

import requests


# ============================================================
# CONFIG
# ============================================================

SYMBOL = "BTCUSDT"
INTERVAL = "5m"

CAPITAL_START = 2.00
INITIAL_MARGIN = 1.00
INITIAL_POSITION = 10.00
LEVERAGE = 10.0

HEDGE_TRIGGER_PCT = 0.02

HEDGE_MODELS = {
    "A_50": 0.50,
    "B_75": 0.75,
    "C_100": 1.00,
}

MAX_HEDGES = 2

TARGET_NET_PROFIT = 0.20

# Binance Futures regular-user taker fee.
# CHANGE ONLY IF YOUR ACCOUNT HAS A DIFFERENT RATE.
TAKER_FEE_RATE = 0.0005       # 0.05%

# Conservative market-order slippage.
SLIPPAGE_RATE = 0.0002        # 0.02%

# Binance funding is fetched from API.
# If funding data cannot be obtained, the script stops.
USE_REAL_FUNDING = True

# Number of months
MONTHS = 6

# Binance endpoints
KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"

# Output files
TRADES_FILE = "btc_hedge_trades.csv"
SUMMARY_FILE = "btc_hedge_summary.csv"

# Conservative liquidation assumptions.
#
# IMPORTANT:
# Binance liquidation is actually calculated from:
#   wallet balance
#   maintenance margin
#   position size
#   mark price
#   fees
#   other exchange rules
#
# This backtest uses a conservative isolated-margin approximation.
#
# Maintenance margin rate:
MAINTENANCE_MARGIN_RATE = 0.004

# Extra liquidation safety buffer.
# This makes liquidation happen slightly earlier.
LIQUIDATION_BUFFER = 0.0005


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "BTC-Hedge-Backtest/1.0"
})


# ============================================================
# TIME
# ============================================================

def utc_now_ms():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def six_months_ago_ms():
    now = datetime.now(timezone.utc)

    # Approximation intentionally kept deterministic.
    # 6 months ~= 182 days.
    start = now - timedelta(days=182)

    return int(start.timestamp() * 1000)


def fmt_time(ms):
    return datetime.fromtimestamp(
        ms / 1000,
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# API
# ============================================================

def api_get(url, params):
    for attempt in range(5):

        try:
            r = SESSION.get(
                url,
                params=params,
                timeout=30
            )

            r.raise_for_status()

            return r.json()

        except Exception as e:

            if attempt == 4:
                raise

            print(
                f"API retry {attempt + 1}/5: {e}"
            )

            time.sleep(2)


# ============================================================
# DOWNLOAD 5M KLINES
# ============================================================

def download_klines():

    print("=" * 70)
    print("DOWNLOADING BTCUSDT 5M DATA")
    print("=" * 70)

    start_ms = six_months_ago_ms()
    end_ms = utc_now_ms()

    rows = []

    cursor = start_ms

    while cursor < end_ms:

        params = {
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1500,
        }

        data = api_get(
            KLINES_URL,
            params
        )

        if not data:
            break

        for k in data:

            rows.append({
                "open_time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
            })

        last_time = int(data[-1][0])

        next_cursor = last_time + 5 * 60 * 1000

        if next_cursor <= cursor:
            break

        cursor = next_cursor

        print(
            f"Downloaded candles: {len(rows):,}"
        )

        time.sleep(0.15)

    # Remove duplicates
    unique = {}

    for r in rows:
        unique[r["open_time"]] = r

    rows = list(unique.values())

    rows.sort(
        key=lambda x: x["open_time"]
    )

    print(
        f"TOTAL CANDLES: {len(rows):,}"
    )

    return rows


# ============================================================
# FUNDING
# ============================================================

def download_funding():

    print("=" * 70)
    print("DOWNLOADING FUNDING HISTORY")
    print("=" * 70)

    start_ms = six_months_ago_ms()
    end_ms = utc_now_ms()

    rows = []

    cursor = start_ms

    while cursor < end_ms:

        params = {
            "symbol": SYMBOL,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1000,
        }

        data = api_get(
            FUNDING_URL,
            params
        )

        if not data:
            break

        for x in data:

            rows.append({
                "time": int(x["fundingTime"]),
                "rate": float(x["fundingRate"]),
            })

        last_time = int(data[-1]["fundingTime"])

        next_cursor = last_time + 1

        if next_cursor <= cursor:
            break

        cursor = next_cursor

        time.sleep(0.15)

    unique = {}

    for r in rows:
        unique[r["time"]] = r

    rows = list(unique.values())

    rows.sort(
        key=lambda x: x["time"]
    )

    print(
        f"TOTAL FUNDING EVENTS: {len(rows):,}"
    )

    return rows


# ============================================================
# FUNDING INDEX
# ============================================================

def build_funding_index(funding_rows):

    return [
        (x["time"], x["rate"])
        for x in funding_rows
    ]


def funding_between(
    funding_index,
    start_ms,
    end_ms,
    positions
):

    total = 0.0

    for funding_time, rate in funding_index:

        if funding_time <= start_ms:
            continue

        if funding_time > end_ms:
            break

        # Funding is calculated on nominal position value.
        #
        # Positive funding:
        #   LONG pays
        #   SHORT receives
        #
        # Negative funding:
        #   LONG receives
        #   SHORT pays

        for p in positions:

            if p["closed"]:
                continue

            notional = p["size"]

            amount = notional * rate

            if p["direction"] == "LONG":

                # positive rate = long pays
                total -= amount

            else:

                # positive rate = short receives
                total += amount

    return total


# ============================================================
# PRICE EXECUTION WITH SLIPPAGE
# ============================================================

def execution_price(raw_price, direction, action):

    # action:
    #   OPEN
    #   CLOSE
    #
    # For a BUY:
    #   slippage increases price.
    #
    # For a SELL:
    #   slippage decreases price.

    if direction == "LONG":

        # Long opening = BUY
        # Long closing = SELL

        if action == "OPEN":
            return raw_price * (1 + SLIPPAGE_RATE)

        return raw_price * (1 - SLIPPAGE_RATE)

    else:

        # Short opening = SELL
        # Short closing = BUY

        if action == "OPEN":
            return raw_price * (1 - SLIPPAGE_RATE)

        return raw_price * (1 + SLIPPAGE_RATE)


# ============================================================
# FEE
# ============================================================

def trading_fee(notional):

    return notional * TAKER_FEE_RATE


# ============================================================
# POSITION PNL
# ============================================================

def position_pnl(position, exit_price):

    if position["direction"] == "LONG":

        return (
            exit_price - position["entry_price"]
        ) / position["entry_price"] * position["size"]

    else:

        return (
            position["entry_price"] - exit_price
        ) / position["entry_price"] * position["size"]


# ============================================================
# LIQUIDATION PRICE
# ============================================================

def approximate_liquidation_price(
    entry_price,
    direction,
    position_size,
    margin
):

    # Initial leverage is 10x.
    #
    # Approximation:
    #
    # Long liquidation:
    #   entry * (1 - margin/position - maintenance - buffer)
    #
    # Short liquidation:
    #   entry * (1 + margin/position + maintenance + buffer)
    #
    # This is deliberately conservative.

    margin_ratio = margin / position_size

    distance = (
        margin_ratio
        - MAINTENANCE_MARGIN_RATE
        - LIQUIDATION_BUFFER
    )

    # Prevent impossible negative distance.
    distance = max(
        distance,
        0.001
    )

    if direction == "LONG":

        return entry_price * (
            1 - distance
        )

    return entry_price * (
        1 + distance
    )


# ============================================================
# POSITION STATE
# ============================================================

def create_position(
    direction,
    raw_price,
    size,
    margin,
    candle_time
):

    entry_price = execution_price(
        raw_price,
        direction,
        "OPEN"
    )

    fee = trading_fee(size)

    liq_price = approximate_liquidation_price(
        entry_price,
        direction,
        size,
        margin
    )

    return {
        "direction": direction,
        "entry_price": entry_price,
        "raw_entry_price": raw_price,
        "size": size,
        "margin": margin,
        "entry_time": candle_time,
        "fee_open": fee,
        "liq_price": liq_price,
        "closed": False,
        "exit_price": None,
        "exit_time": None,
        "fee_close": 0.0,
    }


# ============================================================
# LIQUIDATION CHECK
# ============================================================

def liquidation_hit(
    position,
    high,
    low
):

    liq = position["liq_price"]

    if position["direction"] == "LONG":

        return low <= liq

    return high >= liq


# ============================================================
# HEDGE TRIGGER
# ============================================================

def hedge_trigger_price(
    initial_entry,
    direction
):

    if direction == "LONG":

        return initial_entry * (
            1 - HEDGE_TRIGGER_PCT
        )

    return initial_entry * (
        1 + HEDGE_TRIGGER_PCT
    )


# ============================================================
# HEDGE DIRECTION
# ============================================================

def opposite(direction):

    return (
        "SHORT"
        if direction == "LONG"
        else "LONG"
    )


# ============================================================
# INTRABAR CONSERVATIVE LOGIC
# ============================================================

def candle_event_order(
    direction,
    candle,
    trigger_price,
    liquidation_price
):

    high = candle["high"]
    low = candle["low"]

    trigger_hit = (
        low <= trigger_price <= high
    )

    liq_hit = (
        low <= liquidation_price <= high
    )

    if not trigger_hit and not liq_hit:
        return None

    if liq_hit:
        return "LIQUIDATION"

    return "HEDGE"


# ============================================================
# CYCLE SIMULATION
# ============================================================

def simulate_cycle(
    candles,
    start_index,
    model_name,
    hedge_ratio,
    funding_index,
    cycle_id
):

    if start_index >= len(candles):
        return None, start_index

    first = candles[start_index]

    # --------------------------------------------------------
    # INITIAL DIRECTION
    #
    # This backtest intentionally tests BOTH directions.
    #
    # Every valid starting candle creates:
    #   LONG cycle
    #   SHORT cycle
    #
    # This avoids injecting directional bias.
    # --------------------------------------------------------

    results = []

    for initial_direction in ["LONG", "SHORT"]:

        result, end_index = simulate_one_direction(
            candles=candles,
            start_index=start_index,
            initial_direction=initial_direction,
            model_name=model_name,
            hedge_ratio=hedge_ratio,
            funding_index=funding_index,
            cycle_id=cycle_id
        )

        if result is not None:
            results.append(result)

    # We return the first result for compatibility.
    # Main engine handles both directions separately.
    return results, start_index


# ============================================================
# ONE DIRECTION
# ============================================================

def simulate_one_direction(
    candles,
    start_index,
    initial_direction,
    model_name,
    hedge_ratio,
    funding_index,
    cycle_id
):

    entry_candle = candles[start_index]

    raw_entry = entry_candle["close"]

    initial_position = create_position(
        direction=initial_direction,
        raw_price=raw_entry,
        size=INITIAL_POSITION,
        margin=INITIAL_MARGIN,
        candle_time=entry_candle["open_time"]
    )

    positions = [
        initial_position
    ]

    total_fees = initial_position["fee_open"]

    total_slippage = (
        abs(
            initial_position["entry_price"]
            - raw_entry
        )
        / raw_entry
        * INITIAL_POSITION
    )

    hedge_count = 0

    next_hedge_number = 1

    max_equity_drawdown = 0.0

    peak_equity = CAPITAL_START

    exit_reason = None
    exit_price = None
    exit_time = None

    entry_time = entry_candle["open_time"]

    # Trigger is based on ORIGINAL position entry.
    trigger_price = hedge_trigger_price(
        initial_position["entry_price"],
        initial_direction
    )

    # --------------------------------------------------------
    # PROCESS CANDLES
    # --------------------------------------------------------

    for i in range(start_index + 1, len(candles)):

        candle = candles[i]

        high = candle["high"]
        low = candle["low"]
        close = candle["close"]

        # ----------------------------------------------------
        # 1. CHECK LIQUIDATION FIRST
        #
        # This is intentional.
        #
        # If the same candle contains both:
        #   hedge trigger
        #   liquidation
        #
        # liquidation wins.
        # ----------------------------------------------------

        active_liquidation = False

        for p in positions:

            if p["closed"]:
                continue

            if liquidation_hit(
                p,
                high,
                low
            ):

                active_liquidation = True
                liq_price = p["liq_price"]

                exit_price = liq_price
                exit_time = candle["open_time"]
                exit_reason = "LIQUIDATION"

                break

        if active_liquidation:

            # Close every active position at liquidation price.
            #
            # For conservative accounting we use the
            # liquidation price for the affected position
            # and current close for other positions.

            for p in positions:

                if p["closed"]:
                    continue

                if (
                    p["direction"]
                    == initial_direction
                ):

                    px = exit_price

                else:

                    px = close

                actual_exit = execution_price(
                    px,
                    p["direction"],
                    "CLOSE"
                )

                p["exit_price"] = actual_exit
                p["exit_time"] = exit_time
                p["fee_close"] = trading_fee(
                    p["size"]
                )
                p["closed"] = True

                total_fees += p["fee_close"]

                total_slippage += (
                    abs(
                        actual_exit - px
                    )
                    / px
                    * p["size"]
                )

            break

        # ----------------------------------------------------
        # 2. CHECK HEDGE TRIGGER
        # ----------------------------------------------------

        if hedge_count < MAX_HEDGES:

            trigger_hit = False

            if initial_direction == "LONG":

                if low <= trigger_price:
                    trigger_hit = True

            else:

                if high >= trigger_price:
                    trigger_hit = True

            if trigger_hit:

                # Hedge size is percentage of ORIGINAL
                # $10 position.

                hedge_size = (
                    INITIAL_POSITION
                    * hedge_ratio
                )

                hedge_direction = opposite(
                    initial_direction
                )

                # Conservative assumption:
                # hedge executes exactly at trigger,
                # then slippage is applied.

                hedge_position = create_position(
                    direction=hedge_direction,
                    raw_price=trigger_price,
                    size=hedge_size,
                    margin=hedge_size / LEVERAGE,
                    candle_time=candle["open_time"]
                )

                positions.append(
                    hedge_position
                )

                total_fees += (
                    hedge_position["fee_open"]
                )

                total_slippage += (
                    abs(
                        hedge_position["entry_price"]
                        - trigger_price
                    )
                    / trigger_price
                    * hedge_size
                )

                hedge_count += 1

                # Second hedge trigger:
                #
                # After first hedge, if price continues
                # against the ORIGINAL position by another
                # 2%, create hedge #2.
                #
                # This is deliberately simple and transparent.

                if hedge_count == 1:

                    if initial_direction == "LONG":

                        trigger_price = (
                            trigger_price
                            * (1 - HEDGE_TRIGGER_PCT)
                        )

                    else:

                        trigger_price = (
                            trigger_price
                            * (1 + HEDGE_TRIGGER_PCT)
                        )

                elif hedge_count == 2:

                    # No more hedges.
                    trigger_price = None

        # ----------------------------------------------------
        # 3. CALCULATE CURRENT EQUITY
        # ----------------------------------------------------

        floating_pnl = 0.0

        for p in positions:

            if p["closed"]:
                continue

            floating_pnl += position_pnl(
                p,
                close
            )

        current_funding = funding_between(
            funding_index,
            entry_time,
            candle["open_time"],
            positions
        )

        current_equity = (
            CAPITAL_START
            + floating_pnl
            - total_fees
            - total_slippage
            + current_funding
        )

        if current_equity > peak_equity:

            peak_equity = current_equity

        drawdown = (
            peak_equity
            - current_equity
        )

        if drawdown > max_equity_drawdown:

            max_equity_drawdown = drawdown

        # ----------------------------------------------------
        # 4. TARGET NET PROFIT
        # ----------------------------------------------------

        if (
            current_equity
            - CAPITAL_START
            >= TARGET_NET_PROFIT
        ):

            exit_price = close
            exit_time = candle["open_time"]
            exit_reason = "TARGET"

            for p in positions:

                if p["closed"]:
                    continue

                actual_exit = execution_price(
                    close,
                    p["direction"],
                    "CLOSE"
                )

                p["exit_price"] = actual_exit
                p["exit_time"] = exit_time
                p["fee_close"] = trading_fee(
                    p["size"]
                )
                p["closed"] = True

                total_fees += p["fee_close"]

                total_slippage += (
                    abs(
                        actual_exit - close
                    )
                    / close
                    * p["size"]
                )

            break

        # ----------------------------------------------------
        # 5. SAFETY:
        # Stop after 30 days.
        # ----------------------------------------------------

        if (
            candle["open_time"]
            - entry_time
            >= 30 * 24 * 60 * 60 * 1000
        ):

            exit_price = close
            exit_time = candle["open_time"]
            exit_reason = "TIMEOUT"

            for p in positions:

                if p["closed"]:
                    continue

                actual_exit = execution_price(
                    close,
                    p["direction"],
                    "CLOSE"
                )

                p["exit_price"] = actual_exit
                p["exit_time"] = exit_time
                p["fee_close"] = trading_fee(
                    p["size"]
                )
                p["closed"] = True

                total_fees += p["fee_close"]

                total_slippage += (
                    abs(
                        actual_exit - close
                    )
                    / close
                    * p["size"]
                )

            break

    # --------------------------------------------------------
    # END OF DATA
    # --------------------------------------------------------

    if exit_reason is None:

        exit_price = candles[-1]["close"]
        exit_time = candles[-1]["open_time"]
        exit_reason = "END_OF_DATA"

        for p in positions:

            if p["closed"]:
                continue

            actual_exit = execution_price(
                exit_price,
                p["direction"],
                "CLOSE"
            )

            p["exit_price"] = actual_exit
            p["exit_time"] = exit_time
            p["fee_close"] = trading_fee(
                p["size"]
            )
            p["closed"] = True

            total_fees += p["fee_close"]

            total_slippage += (
                abs(
                    actual_exit - exit_price
                )
                / exit_price
                * p["size"]
            )

    # --------------------------------------------------------
    # FINAL PNL
    # --------------------------------------------------------

    gross_pnl = 0.0

    for p in positions:

        gross_pnl += position_pnl(
            p,
            p["exit_price"]
        )

    funding = funding_between(
        funding_index,
        entry_time,
        exit_time,
        positions
    )

    net_pnl = (
        gross_pnl
        - total_fees
        - total_slippage
        + funding
    )

    final_capital = (
        CAPITAL_START
        + net_pnl
    )

    duration_hours = (
        exit_time - entry_time
    ) / 3600000.0

    # --------------------------------------------------------
    # HEDGE INFORMATION
    # --------------------------------------------------------

    hedge_positions = positions[1:]

    hedge1_price = ""
    hedge1_size = ""

    hedge2_price = ""
    hedge2_size = ""

    if len(hedge_positions) >= 1:

        hedge1_price = hedge_positions[0][
            "entry_price"
        ]

        hedge1_size = hedge_positions[0][
            "size"
        ]

    if len(hedge_positions) >= 2:

        hedge2_price = hedge_positions[1][
            "entry_price"
        ]

        hedge2_size = hedge_positions[1][
            "size"
        ]

    # --------------------------------------------------------
    # LIQUIDATION DISTANCE
    # --------------------------------------------------------

    initial_liq = initial_position[
        "liq_price"
    ]

    if initial_direction == "LONG":

        liquidation_distance = (
            initial_position["entry_price"]
            - initial_liq
        ) / initial_position["entry_price"]

    else:

        liquidation_distance = (
            initial_liq
            - initial_position["entry_price"]
        ) / initial_position["entry_price"]

    record = {

        "cycle_id": cycle_id,

        "model": model_name,

        "entry_time_utc": fmt_time(
            entry_time
        ),

        "direction": initial_direction,

        "entry_price": round(
            initial_position["entry_price"],
            8
        ),

        "initial_position": INITIAL_POSITION,

        "hedge_price": (
            round(hedge1_price, 8)
            if hedge1_price != ""
            else ""
        ),

        "hedge_size": (
            round(hedge1_size, 8)
            if hedge1_size != ""
            else ""
        ),

        "hedge_2_price": (
            round(hedge2_price, 8)
            if hedge2_price != ""
            else ""
        ),

        "hedge_2_size": (
            round(hedge2_size, 8)
            if hedge2_size != ""
            else ""
        ),

        "hedge_count": hedge_count,

        "liquidation_price": round(
            initial_liq,
            8
        ),

        "liquidation_distance_pct": round(
            liquidation_distance * 100,
            4
        ),

        "maximum_drawdown": round(
            max_equity_drawdown,
            8
        ),

        "gross_pnl": round(
            gross_pnl,
            8
        ),

        "fees": round(
            total_fees,
            8
        ),

        "slippage": round(
            total_slippage,
            8
        ),

        "funding": round(
            funding,
            8
        ),

        "net_pnl": round(
            net_pnl,
            8
        ),

        "final_capital": round(
            final_capital,
            8
        ),

        "exit_price": round(
            exit_price,
            8
        ),

        "exit_time_utc": fmt_time(
            exit_time
        ),

        "exit_reason": exit_reason,

        "duration_hours": round(
            duration_hours,
            4
        ),

        "success": (
            1
            if net_pnl >= TARGET_NET_PROFIT
            else 0
        ),
    }

    return record, start_index


# ============================================================
# RUN MODEL
# ============================================================

def run_model(
    candles,
    funding_index,
    model_name,
    hedge_ratio
):

    print()
    print("=" * 70)
    print(
        f"MODEL {model_name} "
        f"HEDGE={hedge_ratio * 100:.0f}%"
    )
    print("=" * 70)

    records = []

    cycle_id = 0

    # We create a cycle every 5-minute candle.
    #
    # Both LONG and SHORT are tested.
    #
    # This is NOT a compounding strategy.
    # Every cycle starts from the same $2 capital
    # so we can evaluate the mechanics cleanly.

    for i in range(len(candles) - 1):

        cycle_id += 1

        results, _ = simulate_cycle(
            candles=candles,
            start_index=i,
            model_name=model_name,
            hedge_ratio=hedge_ratio,
            funding_index=funding_index,
            cycle_id=cycle_id
        )

        if results:

            records.extend(results)

        if cycle_id % 10000 == 0:

            print(
                f"Processed {cycle_id:,} "
                f"start candles..."
            )

    return records


# ============================================================
# SUMMARY
# ============================================================

def summarize(records):

    if not records:
        return {}

    cycles = len(records)

    successful = sum(
        r["success"]
        for r in records
    )

    liquidations = sum(
        1
        for r in records
        if r["exit_reason"] == "LIQUIDATION"
    )

    total_net = sum(
        r["net_pnl"]
        for r in records
    )

    avg_net = (
        total_net / cycles
    )

    max_loss = min(
        r["net_pnl"]
        for r in records
    )

    avg_gross = (
        sum(r["gross_pnl"] for r in records)
        / cycles
    )

    total_fees = sum(
        r["fees"]
        for r in records
    )

    total_slippage = sum(
        r["slippage"]
        for r in records
    )

    total_funding = sum(
        r["funding"]
        for r in records
    )

    max_dd = max(
        r["maximum_drawdown"]
        for r in records
    )

    final_capital = (
        CAPITAL_START
        + total_net
    )

    return {

        "cycles": cycles,

        "successful_cycles": successful,

        "success_pct": (
            successful
            / cycles
            * 100
        ),

        "average_net_pnl": avg_net,

        "average_gross_pnl": avg_gross,

        "max_loss": max_loss,

        "liquidations": liquidations,

        "liquidation_pct": (
            liquidations
            / cycles
            * 100
        ),

        "total_fees": total_fees,

        "total_slippage": total_slippage,

        "total_funding": total_funding,

        "maximum_drawdown": max_dd,

        "total_net_pnl": total_net,

        "final_capital": final_capital,

        "return_pct": (
            total_net
            / CAPITAL_START
            * 100
        ),
    }


# ============================================================
# SAVE TRADES
# ============================================================

def save_trades(records):

    if not records:
        return

    fields = list(records[0].keys())

    with open(
        TRADES_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields
        )

        writer.writeheader()

        writer.writerows(records)

    print(
        f"Saved: {TRADES_FILE}"
    )


# ============================================================
# SAVE SUMMARY
# ============================================================

def save_summary(all_summaries):

    if not all_summaries:
        return

    fields = list(
        all_summaries[0].keys()
    )

    with open(
        SUMMARY_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields
        )

        writer.writeheader()

        writer.writerows(
            all_summaries
        )

    print(
        f"Saved: {SUMMARY_FILE}"
    )


# ============================================================
# PRINT SUMMARY
# ============================================================

def print_summary(summary):

    print()
    print("-" * 70)

    print(
        f"MODEL              : {summary['model']}"
    )

    print(
        f"CYCLES             : {summary['cycles']:,}"
    )

    print(
        f"SUCCESSFUL         : "
        f"{summary['successful_cycles']:,}"
    )

    print(
        f"SUCCESS %          : "
        f"{summary['success_pct']:.2f}%"
    )

    print(
        f"AVG NET PNL        : "
        f"${summary['average_net_pnl']:.6f}"
    )

    print(
        f"MAX LOSS           : "
        f"${summary['max_loss']:.6f}"
    )

    print(
        f"LIQUIDATIONS       : "
        f"{summary['liquidations']:,}"
    )

    print(
        f"LIQUIDATION %      : "
        f"{summary['liquidation_pct']:.2f}%"
    )

    print(
        f"FEES               : "
        f"${summary['total_fees']:.6f}"
    )

    print(
        f"SLIPPAGE           : "
        f"${summary['total_slippage']:.6f}"
    )

    print(
        f"FUNDING            : "
        f"${summary['total_funding']:.6f}"
    )

    print(
        f"MAX DRAWdown       : "
        f"${summary['maximum_drawdown']:.6f}"
    )

    print(
        f"TOTAL NET PNL      : "
        f"${summary['total_net_pnl']:.6f}"
    )

    print(
        f"FINAL CAPITAL      : "
        f"${summary['final_capital']:.6f}"
    )

    print(
        f"RETURN             : "
        f"{summary['return_pct']:.2f}%"
    )

    print("-" * 70)


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 70)
    print("BTCUSDT HEDGE BACKTEST")
    print("=" * 70)

    print(
        f"Capital          : ${CAPITAL_START:.2f}"
    )

    print(
        f"Leverage         : {LEVERAGE:.1f}x"
    )

    print(
        f"Initial Position : ${INITIAL_POSITION:.2f}"
    )

    print(
        f"Hedge Trigger    : "
        f"{HEDGE_TRIGGER_PCT * 100:.2f}%"
    )

    print(
        f"Target Net       : "
        f"${TARGET_NET_PROFIT:.2f}"
    )

    print(
        f"Taker Fee        : "
        f"{TAKER_FEE_RATE * 100:.4f}%"
    )

    print(
        f"Slippage         : "
        f"{SLIPPAGE_RATE * 100:.4f}%"
    )

    print()

    candles = download_klines()

    if len(candles) < 100:

        raise RuntimeError(
            "Not enough candle data."
        )

    funding_rows = download_funding()

    if USE_REAL_FUNDING and not funding_rows:

        raise RuntimeError(
            "Funding data unavailable."
        )

    funding_index = build_funding_index(
        funding_rows
    )

    all_records = []
    all_summaries = []

    # --------------------------------------------------------
    # RUN A / B / C
    # --------------------------------------------------------

    for model_name, hedge_ratio in HEDGE_MODELS.items():

        records = run_model(
            candles=candles,
            funding_index=funding_index,
            model_name=model_name,
            hedge_ratio=hedge_ratio
        )

        for r in records:
            r["model"] = model_name

        all_records.extend(records)

        s = summarize(records)

        s["model"] = model_name

        all_summaries.append(s)

        print_summary(s)

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    save_trades(
        all_records
    )

    save_summary(
        all_summaries
    )

    # --------------------------------------------------------
    # FINAL TABLE
    # --------------------------------------------------------

    print()
    print()
    print("=" * 90)
    print("FINAL COMPARISON")
    print("=" * 90)

    print(
        f"{'MODEL':<10}"
        f"{'CYCLES':>10}"
        f"{'SUCCESS%':>12}"
        f"{'AVG NET':>14}"
        f"{'MAX LOSS':>14}"
        f"{'LIQ':>10}"
        f"{'FINAL $':>14}"
        f"{'RETURN%':>12}"
    )

    print("-" * 90)

    for s in all_summaries:

        print(
            f"{s['model']:<10}"
            f"{s['cycles']:>10,}"
            f"{s['success_pct']:>11.2f}%"
            f"{s['average_net_pnl']:>13.6f}"
            f"{s['max_loss']:>14.6f}"
            f"{s['liquidations']:>10,}"
            f"{s['final_capital']:>14.6f}"
            f"{s['return_pct']:>11.2f}%"
        )

    print("=" * 90)

    print()
    print(
        "BACKTEST COMPLETE"
    )

    print(
        f"Trades file : {TRADES_FILE}"
    )

    print(
        f"Summary file: {SUMMARY_FILE}"
    )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    main()
