# ============================================================
# BTC HEDGE BACKTEST - KRAKEN FUTURES
# VERSION 2.3
# ============================================================
#
# PAPER ONLY
#
# GOAL:
#   Start capital = $2
#   Leverage      = 10x
#   Initial       = $10 notional
#
# HEDGE MODELS:
#   A_50  = 50% hedge
#   B_75  = 75% hedge
#   C_100 = 100% hedge
#
# MAX HEDGES:
#   2
#
# HEDGE TRIGGER:
#   2% adverse movement from the latest hedge reference
#
# TARGET:
#   +$0.20 NET
#   = +10% of initial $2
#
# IMPORTANT:
#   - Kraken Futures
#   - PF_XBTUSD
#   - 5-minute candles
#   - 6 months
#   - Sequential cycles
#   - No overlapping cycles
#   - LONG and SHORT tested independently
#   - Real trading disabled
#   - Funding uses relativeFundingRate
#   - Multi-leg liquidation is calculated from exact leg PnL
#   - Kraken minimum lot is enforced
#   - Intrabar liquidation is conservative
#   - Intrabar target is supported
#
# ============================================================

import os
import io
import json
import time
import math
import traceback
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd


# ============================================================
# CONFIG
# ============================================================

VERSION = "2.3"

REAL_TRADING = False
PAPER_TRADING = True

SYMBOL = "PF_XBTUSD"
TIMEFRAME = "5m"

LOOKBACK_DAYS = 182

CAPITAL_START = 2.00

LEVERAGE = 10.0

INITIAL_NOTIONAL = 10.00

HEDGE_TRIGGER_PCT = 0.02

MAX_HEDGES = 2

TARGET_NET_PROFIT = 0.20

# ------------------------------------------------------------
# Hedge ratios
# ------------------------------------------------------------

MODELS = {
    "A_50": 0.50,
    "B_75": 0.75,
    "C_100": 1.00,
}

DIRECTIONS = [
    "LONG",
    "SHORT",
]

# ------------------------------------------------------------
# Costs
#
# These are configurable assumptions.
# They are NOT claimed to be the user's actual fee tier.
# ------------------------------------------------------------

TAKER_FEE_RATE = 0.0005

SLIPPAGE_RATE = 0.0002

# ------------------------------------------------------------
# Kraken PF_XBTUSD contract constraints
# ------------------------------------------------------------

MIN_QTY = 0.0001

TICK_SIZE = 1.0

# Conservative maintenance-margin assumption.
#
# This is intentionally configurable because Kraken margin
# requirements can vary by contract / account / position.
#
# The backtest does NOT pretend this is an exact exchange
# liquidation engine.
# ------------------------------------------------------------

MAINTENANCE_MARGIN_RATE = 0.005

# ------------------------------------------------------------
# API
# ------------------------------------------------------------

KRAKEN_FUTURES_BASE = (
    "https://futures.kraken.com"
)

KRAKEN_DERIVATIVES_BASE = (
    "https://futures.kraken.com/derivatives/api/v3"
)

KRAKEN_CHARTS_BASE = (
    "https://futures.kraken.com/api/charts/v1"
)

OHLC_URL = (
    f"{KRAKEN_CHARTS_BASE}/candles/"
    f"{SYMBOL}/{TIMEFRAME}"
)

FUNDING_URL = (
    f"{KRAKEN_DERIVATIVES_BASE}/"
    "historical-funding-rates"
)

INSTRUMENTS_URL = (
    f"{KRAKEN_DERIVATIVES_BASE}/instruments"
)

# ------------------------------------------------------------
# Files
# ------------------------------------------------------------

TRADES_FILE = "btc_hedge_trades.csv"

SUMMARY_FILE = "btc_hedge_summary.csv"

EQUITY_FILE = "btc_hedge_equity.csv"

FUNDING_FILE = "btc_hedge_funding.csv"

BLOCKED_FILE = "btc_hedge_blocked.csv"

LOG_FILE = "btc_hedge_log.txt"


# ============================================================
# GLOBAL HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "BTC-Hedge-Backtest/2.3"
        ),
        "Accept": "application/json",
    }
)


# ============================================================
# LOGGING
# ============================================================

def log(message):
    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")

    line = f"[{timestamp}] {message}"

    print(line)

    try:
        with open(
            LOG_FILE,
            "a",
            encoding="utf-8",
        ) as f:
            f.write(line + "\n")
    except Exception:
        pass


# ============================================================
# HTTP
# ============================================================

def http_get(
    url,
    params=None,
    timeout=30,
    retries=4,
):
    last_error = None

    for attempt in range(retries):
        try:
            response = SESSION.get(
                url,
                params=params,
                timeout=timeout,
            )

            if response.status_code == 200:
                return response.json()

            last_error = (
                f"HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

            log(
                f"HTTP error attempt "
                f"{attempt + 1}/{retries}: "
                f"{last_error}"
            )

        except Exception as e:
            last_error = str(e)

            log(
                f"Request error attempt "
                f"{attempt + 1}/{retries}: "
                f"{e}"
            )

        time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(
        f"Request failed after {retries} attempts: "
        f"{url} | {last_error}"
    )


# ============================================================
# TIME HELPERS
# ============================================================

def iso_to_ts(value):
    if value is None:
        return None

    if isinstance(value, (int, float)):
        value = float(value)

        if value > 10_000_000_000:
            return value / 1000.0

        return value

    text = str(value).strip()

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    dt = datetime.fromisoformat(text)

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=timezone.utc
        )

    return dt.timestamp()


def ts_to_iso(ts):
    return datetime.fromtimestamp(
        float(ts),
        timezone.utc,
    ).isoformat()


def ts_to_utc_string(ts):
    return datetime.fromtimestamp(
        float(ts),
        timezone.utc,
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


# ============================================================
# OHLC PARSING
# ============================================================

def parse_ohlc_payload(payload):
    """
    Kraken chart API has returned slightly different
    structures over time.

    This parser accepts:
      result.data
      candles
      data
      list-of-dicts
      list-of-lists
    """

    raw = None

    if isinstance(payload, dict):

        if "candles" in payload:
            raw = payload["candles"]

        elif "data" in payload:
            raw = payload["data"]

        elif "result" in payload:

            result = payload["result"]

            if isinstance(result, dict):

                if "candles" in result:
                    raw = result["candles"]

                elif "data" in result:
                    raw = result["data"]

    elif isinstance(payload, list):
        raw = payload

    if raw is None:
        return []

    records = []

    for row in raw:

        # ----------------------------------------------------
        # Dictionary format
        # ----------------------------------------------------

        if isinstance(row, dict):

            timestamp = (
                row.get("timestamp")
                or row.get("time")
                or row.get("t")
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

            if (
                timestamp is None
                or open_price is None
                or high_price is None
                or low_price is None
                or close_price is None
            ):
                continue

            ts = iso_to_ts(timestamp)

            records.append(
                {
                    "timestamp": ts,
                    "open": float(open_price),
                    "high": float(high_price),
                    "low": float(low_price),
                    "close": float(close_price),
                    "volume": float(volume),
                }
            )

            continue

        # ----------------------------------------------------
        # List format
        #
        # Usually:
        # [timestamp, open, high, low, close, volume]
        #
        # ----------------------------------------------------

        if isinstance(row, (list, tuple)):

            if len(row) < 5:
                continue

            try:
                ts = iso_to_ts(row[0])

                records.append(
                    {
                        "timestamp": ts,
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": (
                            float(row[5])
                            if len(row) > 5
                            else 0.0
                        ),
                    }
                )

            except Exception:
                continue

    return records


# ============================================================
# FETCH OHLC
# ============================================================

def fetch_ohlc(
    start_ts,
    end_ts,
):
    """
    Fetch candles in chunks.

    The endpoint supports a `since` parameter.
    We advance using the latest returned candle.
    """

    all_rows = []

    current = int(start_ts)

    safety_counter = 0

    while current < end_ts:

        safety_counter += 1

        if safety_counter > 1000:
            raise RuntimeError(
                "OHLC pagination safety limit reached."
            )

        params = {
            "since": current,
        }

        payload = http_get(
            OHLC_URL,
            params=params,
        )

        rows = parse_ohlc_payload(
            payload
        )

        if not rows:
            break

        rows = [
            r
            for r in rows
            if start_ts <= r["timestamp"] <= end_ts
        ]

        if rows:
            all_rows.extend(rows)

        max_ts = max(
            r["timestamp"]
            for r in rows
        ) if rows else current

        if max_ts <= current:
            break

        current = int(max_ts) + 300

        log(
            "OHLC progress: "
            f"{ts_to_utc_string(current)}"
        )

        time.sleep(0.15)

    if not all_rows:
        raise RuntimeError(
            "No OHLC candles received."
        )

    df = pd.DataFrame(all_rows)

    df = df.drop_duplicates(
        subset=["timestamp"]
    )

    df = df.sort_values(
        "timestamp"
    )

    df = df[
        (df["timestamp"] >= start_ts)
        & (df["timestamp"] <= end_ts)
    ]

    df = df.reset_index(drop=True)

    return df


# ============================================================
# FUNDING PARSING
# ============================================================

def parse_funding_payload(payload):
    """
    Preserve BOTH Kraken funding fields.

    fundingRate:
        absolute funding rate

    relativeFundingRate:
        relative rate

    IMPORTANT:
        relativeFundingRate is used for the USD notional
        funding calculation for the linear contract.
    """

    raw = None

    if isinstance(payload, dict):

        if "rates" in payload:
            raw = payload["rates"]

        elif "result" in payload:

            result = payload["result"]

            if isinstance(result, dict):
                raw = (
                    result.get("rates")
                    or result.get("data")
                )

    if raw is None:
        return []

    records = []

    for row in raw:

        if not isinstance(row, dict):
            continue

        timestamp = (
            row.get("timestamp")
            or row.get("time")
            or row.get("ts")
        )

        if timestamp is None:
            continue

        try:
            ts = iso_to_ts(timestamp)
        except Exception:
            continue

        absolute_rate = row.get(
            "fundingRate"
        )

        relative_rate = row.get(
            "relativeFundingRate"
        )

        # Some alternate responses may use snake_case.
        if relative_rate is None:
            relative_rate = row.get(
                "relative_funding_rate"
            )

        if absolute_rate is None:
            absolute_rate = row.get(
                "absoluteFundingRate"
            )

        try:
            absolute_rate = (
                float(absolute_rate)
                if absolute_rate is not None
                else None
            )
        except Exception:
            absolute_rate = None

        try:
            relative_rate = (
                float(relative_rate)
                if relative_rate is not None
                else None
            )
        except Exception:
            relative_rate = None

        records.append(
            {
                "timestamp": ts,
                "funding_rate_absolute": (
                    absolute_rate
                ),
                "funding_rate_relative": (
                    relative_rate
                ),
            }
        )

    return records


# ============================================================
# FETCH HISTORICAL FUNDING
# ============================================================

def fetch_historical_funding(
    start_ts,
    end_ts,
):
    """
    Fetch historical funding.

    Kraken's historical endpoint is preferred.

    If the endpoint returns a complete historical set,
    it is filtered locally.

    If it fails, Market Analytics funding is attempted.
    """

    log(
        "Downloading Kraken historical funding..."
    )

    rows = []

    try:

        payload = http_get(
            FUNDING_URL,
            params={
                "symbol": SYMBOL,
            },
            timeout=45,
        )

        rows = parse_funding_payload(
            payload
        )

        log(
            f"Historical funding endpoint "
            f"returned {len(rows)} rows."
        )

    except Exception as e:

        log(
            "Historical funding endpoint failed: "
            f"{e}"
        )

    if rows:

        filtered = [
            r
            for r in rows
            if start_ts <= r["timestamp"] <= end_ts
        ]

        if filtered:

            df = pd.DataFrame(
                filtered
            )

            df = df.drop_duplicates(
                subset=["timestamp"]
            )

            df = df.sort_values(
                "timestamp"
            )

            df.reset_index(
                drop=True,
                inplace=True,
            )

            log(
                "Funding source: "
                "historical-funding-rates"
            )

            return df

    # ========================================================
    # FALLBACK: Market Analytics
    # ========================================================

    log(
        "Trying Kraken Market Analytics funding..."
    )

    analytics_url = (
        f"{KRAKEN_CHARTS_BASE}/"
        f"analytics/{SYMBOL}/funding"
    )

    try:

        payload = http_get(
            analytics_url,
            params={
                "since": int(start_ts),
                "to": int(end_ts),
                "interval": 3600,
            },
            timeout=45,
        )

        result = (
            payload.get("result", {})
            if isinstance(payload, dict)
            else {}
        )

        timestamps = result.get(
            "timestamp",
            []
        )

        data = result.get(
            "data",
            {}
        )

        if isinstance(data, dict):

            absolute_rates = (
                data.get("rate", [])
            )

            relative_rates = (
                data.get("relativeRate", [])
            )

        else:
            absolute_rates = []
            relative_rates = []

        count = min(
            len(timestamps),
            len(absolute_rates),
            len(relative_rates),
        )

        records = []

        for i in range(count):

            try:

                records.append(
                    {
                        "timestamp": float(
                            timestamps[i]
                        ),
                        "funding_rate_absolute": (
                            float(
                                absolute_rates[i]
                            )
                        ),
                        "funding_rate_relative": (
                            float(
                                relative_rates[i]
                            )
                        ),
                    }
                )

            except Exception:
                continue

        if records:

            df = pd.DataFrame(
                records
            )

            df = df[
                (df["timestamp"] >= start_ts)
                & (df["timestamp"] <= end_ts)
            ]

            df = df.drop_duplicates(
                subset=["timestamp"]
            )

            df = df.sort_values(
                "timestamp"
            )

            df.reset_index(
                drop=True,
                inplace=True,
            )

            log(
                "Funding source: "
                "Market Analytics"
            )

            return df

    except Exception as e:

        log(
            "Market Analytics funding failed: "
            f"{e}"
        )

    log(
        "WARNING: No historical funding data."
    )

    return pd.DataFrame(
        columns=[
            "timestamp",
            "funding_rate_absolute",
            "funding_rate_relative",
        ]
    )


# ============================================================
# INSTRUMENT INFORMATION
# ============================================================

def fetch_instrument_info():
    """
    Attempts to obtain current instrument metadata.

    The hard safety defaults remain:
      MIN_QTY = 0.0001
      TICK_SIZE = 1.0
    """

    info = {
        "min_qty": MIN_QTY,
        "tick_size": TICK_SIZE,
        "maintenance_margin_rate": (
            MAINTENANCE_MARGIN_RATE
        ),
    }

    try:

        payload = http_get(
            INSTRUMENTS_URL,
            timeout=30,
        )

        instruments = payload.get(
            "instruments",
            []
        )

        for item in instruments:

            if not isinstance(item, dict):
                continue

            symbol = str(
                item.get("symbol", "")
            ).upper()

            if symbol != SYMBOL:
                continue

            for key in [
                "contractSize",
                "minLot",
                "minOrderSize",
                "lotSize",
            ]:

                value = item.get(key)

                if value is not None:
                    try:
                        if key in [
                            "minLot",
                            "minOrderSize",
                            "lotSize",
                        ]:
                            info["min_qty"] = float(
                                value
                            )
                    except Exception:
                        pass

            tick = item.get(
                "tickSize"
            )

            if tick is not None:
                try:
                    info["tick_size"] = float(
                        tick
                    )
                except Exception:
                    pass

            # Try several possible names.
            mm = (
                item.get(
                    "maintenanceMarginRate"
                )
                or item.get(
                    "maintenanceMargin"
                )
                or item.get(
                    "maintenance_margin_rate"
                )
            )

            if mm is not None:

                try:
                    mm = float(mm)

                    if 0 < mm < 1:
                        info[
                            "maintenance_margin_rate"
                        ] = mm

                except Exception:
                    pass

            break

    except Exception as e:

        log(
            "Instrument metadata unavailable: "
            f"{e}"
        )

    return info


# ============================================================
# PRICE ROUNDING
# ============================================================

def round_to_tick(
    price,
    tick_size,
):
    if not tick_size or tick_size <= 0:
        return float(price)

    return round(
        round(
            float(price) / tick_size
        ) * tick_size,
        10,
    )


# ============================================================
# POSITION HELPERS
# ============================================================

def clone_positions(
    positions,
):
    return [
        dict(position)
        for position in positions
    ]


def total_qty(
    positions,
):
    return sum(
        p["qty"]
        for p in positions
    )


def total_abs_qty(
    positions,
):
    return sum(
        abs(p["qty"])
        for p in positions
    )


def total_notional(
    positions,
    price,
):
    return sum(
        abs(p["qty"]) * price
        for p in positions
    )


def unrealized_pnl(
    positions,
    price,
):
    return sum(
        p["qty"]
        * (
            price
            - p["entry_price"]
        )
        for p in positions
    )


def signed_entry_value(
    positions,
):
    return sum(
        p["qty"]
        * p["entry_price"]
        for p in positions
    )


# ============================================================
# EXECUTION PRICE
# ============================================================

def market_entry_price(
    raw_price,
    side,
):
    """
    Buy/long entry pays slippage upward.
    Sell/short entry receives downward.
    """

    raw_price = float(raw_price)

    if side == "LONG":
        return raw_price * (
            1.0 + SLIPPAGE_RATE
        )

    return raw_price * (
        1.0 - SLIPPAGE_RATE
    )


def market_exit_price(
    raw_price,
    net_side,
):
    """
    Closing a net long:
        sell -> worse by slippage

    Closing a net short:
        buy -> worse by slippage
    """

    raw_price = float(raw_price)

    if net_side == "LONG":
        return raw_price * (
            1.0 - SLIPPAGE_RATE
        )

    if net_side == "SHORT":
        return raw_price * (
            1.0 + SLIPPAGE_RATE
        )

    return raw_price


# ============================================================
# FEES
# ============================================================

def trading_fee(
    qty,
    execution_price,
):
    return (
        abs(qty)
        * execution_price
        * TAKER_FEE_RATE
    )


# ============================================================
# MARGIN
# ============================================================

def margin_required(
    positions,
    price,
):
    """
    Conservative initial-style margin approximation.

    Uses total absolute notional / leverage.
    """

    notional = total_notional(
        positions,
        price,
    )

    return notional / LEVERAGE


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


def maintenance_margin(
    positions,
    price,
    maintenance_rate,
):
    return (
        total_notional(
            positions,
            price,
        )
        * maintenance_rate
    )


# ============================================================
# EXACT MULTI-LEG LIQUIDATION
# ============================================================

def approximate_liquidation_price(
    cash,
    positions,
    maintenance_rate,
):
    """
    Solve:

        equity(P)
          =
        maintenance_margin(P)

    equity(P):

        cash
        + sum(
            qty_i * (P - entry_i)
          )

    maintenance:

        maintenance_rate
        * sum(abs(qty_i) * P)

    Therefore:

        P * (
            sum(qty)
            - mm * sum(abs(qty))
        )
        =
        sum(qty * entry)
        - cash

    This is much more appropriate for opposing
    hedge legs than a weighted-average entry.
    """

    if not positions:
        return None

    net_qty = total_qty(
        positions
    )

    abs_qty = total_abs_qty(
        positions
    )

    if abs_qty <= 0:
        return None

    numerator = (
        signed_entry_value(
            positions
        )
        - cash
    )

    denominator = (
        net_qty
        - (
            maintenance_rate
            * abs_qty
        )
    )

    if abs(denominator) < 1e-12:
        return None

    price = (
        numerator
        / denominator
    )

    if price <= 0:
        return None

    return float(price)


def liquidation_relevant(
    liq_price,
    current_price,
    positions,
):
    if liq_price is None:
        return False

    net_qty = total_qty(
        positions
    )

    if net_qty > 0:
        return (
            liq_price < current_price
        )

    if net_qty < 0:
        return (
            liq_price > current_price
        )

    return False


# ============================================================
# TARGET PRICE
# ============================================================

def target_raw_price(
    cash,
    positions,
    cycle_start_equity,
):
    """
    Solve the raw market price at which closing the
    complete hedged position should produce:

        cycle_start_equity + TARGET_NET_PROFIT

    including:
        - exit slippage
        - closing taker fees

    Funding already sits in cash.
    """

    if not positions:
        return None

    net_qty = total_qty(
        positions
    )

    abs_qty = total_abs_qty(
        positions
    )

    if abs(net_qty) < 1e-12:
        return None

    signed_entries = (
        signed_entry_value(
            positions
        )
    )

    target_equity = (
        cycle_start_equity
        + TARGET_NET_PROFIT
    )

    # Equity after close:
    #
    # cash
    # + net_qty * exec_price
    # - signed_entries
    # - abs_qty * exec_price * fee
    #
    # = target equity

    denominator = (
        net_qty
        - (
            abs_qty
            * TAKER_FEE_RATE
        )
    )

    if abs(denominator) < 1e-12:
        return None

    exec_price = (
        target_equity
        - cash
        + signed_entries
    ) / denominator

    if exec_price <= 0:
        return None

    if net_qty > 0:

        raw_price = (
            exec_price
            / (
                1.0
                - SLIPPAGE_RATE
            )
        )

    else:

        raw_price = (
            exec_price
            / (
                1.0
                + SLIPPAGE_RATE
            )
        )

    return float(raw_price)


# ============================================================
# CLOSE POSITION
# ============================================================

def close_all_positions(
    cash,
    positions,
    raw_exit_price,
):
    if not positions:
        return {
            "cash_after": cash,
            "gross_pnl": 0.0,
            "fees": 0.0,
            "slippage_cost": 0.0,
            "positions": [],
        }

    net_qty = total_qty(
        positions
    )

    if net_qty > 0:
        net_side = "LONG"
    elif net_qty < 0:
        net_side = "SHORT"
    else:
        net_side = None

    execution_price = market_exit_price(
        raw_exit_price,
        net_side,
    )

    gross_pnl = 0.0

    fees = 0.0

    for p in positions:

        gross_pnl += (
            p["qty"]
            * (
                execution_price
                - p["entry_price"]
            )
        )

        fees += trading_fee(
            p["qty"],
            execution_price,
        )

    # Slippage cost is calculated against
    # raw exit price for reporting only.
    #
    # PnL itself uses execution price.

    slippage_cost = 0.0

    for p in positions:

        ideal_close = (
            abs(p["qty"])
            * raw_exit_price
        )

        actual_close = (
            abs(p["qty"])
            * execution_price
        )

        # For reporting, take the adverse difference.
        slippage_cost += abs(
            actual_close
            - ideal_close
        )

    cash_after = (
        cash
        + gross_pnl
        - fees
    )

    return {
        "cash_after": cash_after,
        "gross_pnl": gross_pnl,
        "fees": fees,
        "slippage_cost": slippage_cost,
        "execution_price": execution_price,
        "positions": [],
    }


# ============================================================
# FUNDING LOOKUP
# ============================================================

class FundingBook:

    def __init__(
        self,
        funding_df,
    ):
        self.events = []

        if funding_df is None:
            return

        if funding_df.empty:
            return

        for _, row in funding_df.iterrows():

            relative_rate = row.get(
                "funding_rate_relative"
            )

            absolute_rate = row.get(
                "funding_rate_absolute"
            )

            if pd.isna(
                relative_rate
            ):
                relative_rate = None
            else:
                relative_rate = float(
                    relative_rate
                )

            if pd.isna(
                absolute_rate
            ):
                absolute_rate = None
            else:
                absolute_rate = float(
                    absolute_rate
                )

            self.events.append(
                {
                    "timestamp": float(
                        row["timestamp"]
                    ),
                    "relative_rate": (
                        relative_rate
                    ),
                    "absolute_rate": (
                        absolute_rate
                    ),
                }
            )

        self.events.sort(
            key=lambda x: x["timestamp"]
        )

    def events_between(
        self,
        start_ts,
        end_ts,
    ):
        return [
            e
            for e in self.events
            if (
                e["timestamp"] > start_ts
                and e["timestamp"] <= end_ts
            )
        ]


# ============================================================
# APPLY FUNDING
# ============================================================

def apply_funding_events(
    cash,
    positions,
    start_ts,
    end_ts,
    funding_book,
    candles_by_ts,
):
    """
    Kraken linear contract:

      positive funding:
          long pays short

      negative funding:
          short pays long

    Relative funding payout:

        notional
        * relativeFundingRate

    Therefore:

        long payment  = +notional * rate
        short payment = -notional * rate

    Cash is adjusted by:

        cash -= payment
    """

    total_funding = 0.0

    applied_events = []

    events = funding_book.events_between(
        start_ts,
        end_ts,
    )

    for event in events:

        ts = event["timestamp"]

        relative_rate = (
            event["relative_rate"]
        )

        if relative_rate is None:
            continue

        price = price_before_timestamp(
            candles_by_ts,
            ts,
        )

        if price is None:
            continue

        payment = 0.0

        for p in positions:

            notional = (
                abs(p["qty"])
                * price
            )

            if p["qty"] > 0:

                # Long pays when rate positive.
                payment += (
                    notional
                    * relative_rate
                )

            elif p["qty"] < 0:

                # Short receives when rate positive.
                payment -= (
                    notional
                    * relative_rate
                )

        cash -= payment

        total_funding += payment

        applied_events.append(
            {
                "timestamp": ts,
                "price": price,
                "relative_rate": (
                    relative_rate
                ),
                "absolute_rate": (
                    event["absolute_rate"]
                ),
                "payment": payment,
            }
        )

    return (
        cash,
        total_funding,
        applied_events,
    )


# ============================================================
# PRICE AT / BEFORE FUNDING EVENT
# ============================================================

def price_before_timestamp(
    candles_by_ts,
    timestamp,
):
    if not candles_by_ts:
        return None

    eligible = [
        ts
        for ts in candles_by_ts.keys()
        if ts <= timestamp
    ]

    if not eligible:
        return None

    ts = max(eligible)

    row = candles_by_ts[ts]

    return float(
        row["close"]
    )


# ============================================================
# HEDGE ENTRY
# ============================================================

def add_hedge(
    cash,
    positions,
    raw_price,
    original_qty,
    hedge_ratio,
    hedge_number,
    direction,
):
    """
    Hedge is opposite to the original direction.

    Example:

        original LONG
        50% hedge
        -> SHORT qty = original_qty * 0.50
    """

    if direction == "LONG":
        hedge_side = "SHORT"
        hedge_sign = -1.0
    else:
        hedge_side = "LONG"
        hedge_sign = 1.0

    hedge_qty = (
        abs(original_qty)
        * hedge_ratio
    )

    if hedge_qty < MIN_QTY:
        return {
            "success": False,
            "reason": "HEDGE_BELOW_MIN_LOT",
            "cash": cash,
            "positions": positions,
            "qty": hedge_qty,
        }

    execution_price = market_entry_price(
        raw_price,
        hedge_side,
    )

    fee = trading_fee(
        hedge_qty,
        execution_price,
    )

    # Fee must be available in cash.
    if cash < fee:
        return {
            "success": False,
            "reason": "INSUFFICIENT_CASH_FOR_FEE",
            "cash": cash,
            "positions": positions,
            "qty": hedge_qty,
        }

    cash -= fee

    positions.append(
        {
            "leg": (
                f"HEDGE_{hedge_number}"
            ),
            "side": hedge_side,
            "qty": (
                hedge_sign
                * hedge_qty
            ),
            "entry_price": (
                execution_price
            ),
            "entry_raw_price": (
                raw_price
            ),
            "fee": fee,
            "hedge_number": (
                hedge_number
            ),
        }
    )

    return {
        "success": True,
        "reason": "HEDGE_ADDED",
        "cash": cash,
        "positions": positions,
        "qty": hedge_qty,
        "execution_price": execution_price,
        "fee": fee,
    }


# ============================================================
# MARGIN CHECK FOR NEW HEDGE
# ============================================================

def hedge_margin_allowed(
    cash,
    positions,
    new_qty,
    price,
    maintenance_rate,
):
    """
    Conservative availability check.

    Existing total notional + new leg notional
    must fit within available collateral under
    the configured leverage approximation.
    """

    existing_notional = total_notional(
        positions,
        price,
    )

    new_notional = (
        abs(new_qty)
        * price
    )

    required_margin = (
        existing_notional
        + new_notional
    ) / LEVERAGE

    estimated_fee = trading_fee(
        new_qty,
        price,
    )

    return (
        required_margin
        + estimated_fee
        <= max(cash, 0.0)
    )


# ============================================================
# INITIAL ENTRY
# ============================================================

def initial_entry(
    raw_price,
    direction,
):
    if raw_price <= 0:
        return None

    qty = (
        INITIAL_NOTIONAL
        / raw_price
    )

    # IMPORTANT:
    # Do NOT round up to minimum lot.
    # That would silently increase the user's $10
    # initial notional.

    if qty < MIN_QTY:
        return {
            "success": False,
            "reason": (
                "INITIAL_NOTIONAL_BELOW_MIN_LOT"
            ),
            "qty": qty,
            "required_notional": (
                MIN_QTY * raw_price
            ),
        }

    side = direction

    execution_price = market_entry_price(
        raw_price,
        side,
    )

    fee = trading_fee(
        qty,
        execution_price,
    )

    signed_qty = (
        qty
        if direction == "LONG"
        else -qty
    )

    position = {
        "leg": "INITIAL",
        "side": direction,
        "qty": signed_qty,
        "entry_price": execution_price,
        "entry_raw_price": raw_price,
        "fee": fee,
        "hedge_number": 0,
    }

    return {
        "success": True,
        "position": position,
        "qty": qty,
        "execution_price": execution_price,
        "fee": fee,
    }


# ============================================================
# CANDLE RANGE HIT
# ============================================================

def price_in_candle(
    price,
    candle,
):
    if price is None:
        return False

    return (
        candle["low"]
        <= price
        <= candle["high"]
    )


# ============================================================
# MAX DRAWDOWN TRACKING
# ============================================================

def update_drawdown(
    cycle,
    equity,
):
    if equity > cycle["peak_equity"]:
        cycle["peak_equity"] = equity

    dd = (
        equity
        - cycle["peak_equity"]
    )

    if dd < cycle["max_drawdown"]:
        cycle["max_drawdown"] = dd


# ============================================================
# CYCLE SIMULATION
# ============================================================

def simulate_cycle(
    candles,
    start_index,
    direction,
    model_name,
    hedge_ratio,
    funding_book,
    candles_by_ts,
    maintenance_rate,
    account_start,
):
    """
    One independent sequential cycle.

    Entry occurs at the close of start_index.

    From the next candle onward:
      1. funding
      2. liquidation check
      3. target check
      4. one hedge maximum per candle

    Conservative rule:
      if hedge trigger and liquidation are both
      inside the same candle, liquidation wins.
    """

    if (
        start_index < 0
        or start_index >= len(candles)
    ):
        return None

    entry_candle = candles.iloc[
        start_index
    ]

    entry_raw_price = float(
        entry_candle["close"]
    )

    entry_ts = float(
        entry_candle["timestamp"]
    )

    entry_result = initial_entry(
        entry_raw_price,
        direction,
    )

    if not entry_result["success"]:

        return {
            "status": "BLOCKED",
            "blocked_reason": (
                entry_result["reason"]
            ),
            "entry_timestamp": entry_ts,
            "entry_price": entry_raw_price,
            "direction": direction,
            "model": model_name,
            "account_start": account_start,
            "required_notional": (
                entry_result.get(
                    "required_notional"
                )
            ),
        }

    positions = [
        entry_result["position"]
    ]

    cash = (
        account_start
        - entry_result["fee"]
    )

    initial_qty = (
        entry_result["qty"]
    )

    cycle = {
        "status": "OPEN",
        "model": model_name,
        "direction": direction,
        "hedge_ratio": hedge_ratio,
        "entry_timestamp": entry_ts,
        "entry_price": (
            entry_result[
                "execution_price"
            ]
        ),
        "entry_raw_price": (
            entry_raw_price
        ),
        "initial_qty": initial_qty,
        "cash_start": account_start,
        "cash": cash,
        "positions": positions,
        "hedges": [],
        "hedge_count": 0,
        "funding": 0.0,
        "fees": entry_result["fee"],
        "slippage": 0.0,
        "gross_pnl": 0.0,
        "max_drawdown": 0.0,
        "peak_equity": account_start,
        "min_equity": account_start,
        "max_margin": 0.0,
        "blocked_hedges": 0,
        "exit_timestamp": None,
        "exit_price": None,
        "exit_reason": None,
        "exit_execution_price": None,
        "duration_minutes": None,
        "equity_at_exit": None,
    }

    # --------------------------------------------------------
    # First hedge trigger
    #
    # Trigger is measured from initial entry.
    # After a hedge, the next trigger is measured from
    # the latest hedge execution price.
    # --------------------------------------------------------

    reference_price = (
        entry_result[
            "execution_price"
        ]
    )

    if direction == "LONG":
        next_trigger = (
            reference_price
            * (
                1.0
                - HEDGE_TRIGGER_PCT
            )
        )
    else:
        next_trigger = (
            reference_price
            * (
                1.0
                + HEDGE_TRIGGER_PCT
            )
        )

    # --------------------------------------------------------
    # Iterate forward
    # --------------------------------------------------------

    for i in range(
        start_index + 1,
        len(candles),
    ):

        candle = candles.iloc[i]

        ts = float(
            candle["timestamp"]
        )

        high = float(
            candle["high"]
        )

        low = float(
            candle["low"]
        )

        close = float(
            candle["close"]
        )

        # ----------------------------------------------------
        # Apply funding first for events since previous candle.
        # ----------------------------------------------------

        previous_ts = float(
            candles.iloc[i - 1]["timestamp"]
        )

        (
            cash,
            funding_payment,
            funding_events,
        ) = apply_funding_events(
            cash,
            positions,
            previous_ts,
            ts,
            funding_book,
            candles_by_ts,
        )

        cycle["cash"] = cash

        cycle["funding"] += (
            funding_payment
        )

        # ----------------------------------------------------
        # Equity at candle close
        # ----------------------------------------------------

        equity = current_equity(
            cash,
            positions,
            close,
        )

        cycle["min_equity"] = min(
            cycle["min_equity"],
            equity,
        )

        update_drawdown(
            cycle,
            equity,
        )

        margin = margin_required(
            positions,
            close,
        )

        cycle["max_margin"] = max(
            cycle["max_margin"],
            margin,
        )

        # ----------------------------------------------------
        # Liquidation price
        # ----------------------------------------------------

        liq_price = (
            approximate_liquidation_price(
                cash,
                positions,
                maintenance_rate,
            )
        )

        liq_hit = False

        if liquidation_relevant(
            liq_price,
            close,
            positions,
        ):

            net_qty = total_qty(
                positions
            )

            if net_qty > 0:
                liq_hit = (
                    low
                    <= liq_price
                )
            elif net_qty < 0:
                liq_hit = (
                    high
                    >= liq_price
                )

        # ----------------------------------------------------
        # Target price
        # ----------------------------------------------------

        target_price = (
            target_raw_price(
                cash,
                positions,
                cycle["cash_start"],
            )
        )

        target_hit = (
            target_price is not None
            and price_in_candle(
                target_price,
                candle,
            )
        )

        # ----------------------------------------------------
        # Conservative priority:
        #
        # Liquidation wins over target if both are
        # touched in same candle.
        # ----------------------------------------------------

        if liq_hit:

            exit_raw = (
                liq_price
            )

            closed = close_all_positions(
                cash,
                positions,
                exit_raw,
            )

            cash_after = max(
                0.0,
                closed["cash_after"],
            )

            cycle["cash"] = cash_after

            cycle["gross_pnl"] += (
                closed["gross_pnl"]
            )

            cycle["fees"] += (
                closed["fees"]
            )

            cycle["slippage"] += (
                closed["slippage_cost"]
            )

            cycle["exit_timestamp"] = ts

            cycle["exit_price"] = (
                exit_raw
            )

            cycle["exit_execution_price"] = (
                closed["execution_price"]
            )

            cycle["exit_reason"] = (
                "LIQUIDATION"
            )

            cycle["equity_at_exit"] = (
                cash_after
            )

            cycle["positions"] = []

            cycle["duration_minutes"] = (
                (
                    ts
                    - cycle["entry_timestamp"]
                )
                / 60.0
            )

            cycle["status"] = "CLOSED"

            return finalize_cycle(
                cycle
            )

        # ----------------------------------------------------
        # Target hit
        # ----------------------------------------------------

        if target_hit:

            exit_raw = (
                target_price
            )

            closed = close_all_positions(
                cash,
                positions,
                exit_raw,
            )

            cycle["cash"] = (
                closed["cash_after"]
            )

            cycle["gross_pnl"] += (
                closed["gross_pnl"]
            )

            cycle["fees"] += (
                closed["fees"]
            )

            cycle["slippage"] += (
                closed["slippage_cost"]
            )

            cycle["exit_timestamp"] = ts

            cycle["exit_price"] = (
                exit_raw
            )

            cycle["exit_execution_price"] = (
                closed["execution_price"]
            )

            cycle["exit_reason"] = (
                "TARGET_+0.20"
            )

            cycle["equity_at_exit"] = (
                closed["cash_after"]
            )

            cycle["positions"] = []

            cycle["duration_minutes"] = (
                (
                    ts
                    - cycle["entry_timestamp"]
                )
                / 60.0
            )

            cycle["status"] = "CLOSED"

            return finalize_cycle(
                cycle
            )

        # ----------------------------------------------------
        # Hedge trigger
        #
        # Only ONE hedge per candle.
        #
        # This is deliberately conservative because a 5m
        # OHLC candle does not tell us whether the price
        # crossed trigger #1 and trigger #2 in which order.
        # ----------------------------------------------------

        if cycle["hedge_count"] < MAX_HEDGES:

            trigger_hit = False

            if direction == "LONG":
                trigger_hit = (
                    low
                    <= next_trigger
                )
            else:
                trigger_hit = (
                    high
                    >= next_trigger
                )

            if trigger_hit:

                hedge_qty = (
                    abs(initial_qty)
                    * hedge_ratio
                )

                allowed = hedge_margin_allowed(
                    cash,
                    positions,
                    hedge_qty,
                    next_trigger,
                    maintenance_rate,
                )

                if not allowed:

                    cycle[
                        "blocked_hedges"
                    ] += 1

                else:

                    hedge_result = add_hedge(
                        cash,
                        positions,
                        next_trigger,
                        initial_qty,
                        hedge_ratio,
                        cycle[
                            "hedge_count"
                        ] + 1,
                        direction,
                    )

                    if hedge_result[
                        "success"
                    ]:

                        cash = (
                            hedge_result[
                                "cash"
                            ]
                        )

                        positions = (
                            hedge_result[
                                "positions"
                            ]
                        )

                        cycle[
                            "hedge_count"
                        ] += 1

                        cycle["fees"] += (
                            hedge_result["fee"]
                        )

                        cycle["hedges"].append(
                            {
                                "hedge_number": (
                                    cycle[
                                        "hedge_count"
                                    ]
                                ),
                                "raw_price": (
                                    next_trigger
                                ),
                                "execution_price": (
                                    hedge_result[
                                        "execution_price"
                                    ]
                                ),
                                "qty": (
                                    hedge_result[
                                        "qty"
                                    ]
                                ),
                                "fee": (
                                    hedge_result[
                                        "fee"
                                    ]
                                ),
                                "timestamp": ts,
                            }
                        )

                        # Next trigger from latest hedge.
                        reference_price = (
                            hedge_result[
                                "execution_price"
                            ]
                        )

                        if direction == "LONG":
                            next_trigger = (
                                reference_price
                                * (
                                    1.0
                                    - HEDGE_TRIGGER_PCT
                                )
                            )
                        else:
                            next_trigger = (
                                reference_price
                                * (
                                    1.0
                                    + HEDGE_TRIGGER_PCT
                                )
                            )

        # ----------------------------------------------------
        # Continue.
        # ----------------------------------------------------

    # ========================================================
    # End of data.
    #
    # Close at final candle close.
    # ========================================================

    last_candle = candles.iloc[-1]

    final_ts = float(
        last_candle["timestamp"]
    )

    final_raw = float(
        last_candle["close"]
    )

    closed = close_all_positions(
        cash,
        positions,
        final_raw,
    )

    cycle["cash"] = (
        closed["cash_after"]
    )

    cycle["gross_pnl"] += (
        closed["gross_pnl"]
    )

    cycle["fees"] += (
        closed["fees"]
    )

    cycle["slippage"] += (
        closed["slippage_cost"]
    )

    cycle["exit_timestamp"] = (
        final_ts
    )

    cycle["exit_price"] = (
        final_raw
    )

    cycle["exit_execution_price"] = (
        closed["execution_price"]
    )

    cycle["exit_reason"] = (
        "END_OF_DATA"
    )

    cycle["equity_at_exit"] = (
        closed["cash_after"]
    )

    cycle["positions"] = []

    cycle["duration_minutes"] = (
        (
            final_ts
            - cycle["entry_timestamp"]
        )
        / 60.0
    )

    cycle["status"] = "CLOSED"

    return finalize_cycle(
        cycle
    )


# ============================================================
# FINALIZE CYCLE
# ============================================================

def finalize_cycle(
    cycle,
):
    start = (
        cycle["cash_start"]
    )

    final_equity = (
        cycle["equity_at_exit"]
    )

    net_pnl = (
        final_equity
        - start
    )

    cycle["net_pnl"] = net_pnl

    cycle["return_pct"] = (
        net_pnl
        / start
        * 100.0
        if start > 0
        else 0.0
    )

    cycle["success"] = (
        net_pnl
        >= TARGET_NET_PROFIT
        - 1e-9
    )

    return cycle


# ============================================================
# SERIALIZE CYCLE
# ============================================================

def cycle_to_row(
    cycle,
    cycle_number,
):
    row = {
        "cycle": cycle_number,
        "model": cycle.get("model"),
        "direction": cycle.get(
            "direction"
        ),
        "status": cycle.get(
            "status"
        ),
        "entry_time_utc": (
            ts_to_iso(
                cycle["entry_timestamp"]
            )
            if cycle.get(
                "entry_timestamp"
            )
            else None
        ),
        "exit_time_utc": (
            ts_to_iso(
                cycle["exit_timestamp"]
            )
            if cycle.get(
                "exit_timestamp"
            )
            else None
        ),
        "entry_price": cycle.get(
            "entry_price"
        ),
        "entry_raw_price": cycle.get(
            "entry_raw_price"
        ),
        "initial_qty": cycle.get(
            "initial_qty"
        ),
        "hedge_ratio": cycle.get(
            "hedge_ratio"
        ),
        "hedge_count": cycle.get(
            "hedge_count"
        ),
        "hedge1_price": None,
        "hedge1_qty": None,
        "hedge2_price": None,
        "hedge2_qty": None,
        "exit_price": cycle.get(
            "exit_price"
        ),
        "exit_execution_price": cycle.get(
            "exit_execution_price"
        ),
        "exit_reason": cycle.get(
            "exit_reason"
        ),
        "duration_minutes": cycle.get(
            "duration_minutes"
        ),
        "gross_pnl": cycle.get(
            "gross_pnl",
            0.0,
        ),
        "fees": cycle.get(
            "fees",
            0.0,
        ),
        "slippage": cycle.get(
            "slippage",
            0.0,
        ),
        "funding": cycle.get(
            "funding",
            0.0,
        ),
        "net_pnl": cycle.get(
            "net_pnl",
            0.0,
        ),
        "return_pct": cycle.get(
            "return_pct",
            0.0,
        ),
        "max_drawdown": cycle.get(
            "max_drawdown",
            0.0,
        ),
        "min_equity": cycle.get(
            "min_equity",
            0.0,
        ),
        "max_margin": cycle.get(
            "max_margin",
            0.0,
        ),
        "blocked_hedges": cycle.get(
            "blocked_hedges",
            0,
        ),
        "account_start": cycle.get(
            "cash_start"
        ),
        "account_end": cycle.get(
            "equity_at_exit"
        ),
        "success": cycle.get(
            "success",
            False,
        ),
        "blocked_reason": cycle.get(
            "blocked_reason"
        ),
    }

    hedges = cycle.get(
        "hedges",
        []
    )

    if len(hedges) >= 1:

        row["hedge1_price"] = (
            hedges[0].get(
                "execution_price"
            )
        )

        row["hedge1_qty"] = (
            hedges[0].get(
                "qty"
            )
        )

    if len(hedges) >= 2:

        row["hedge2_price"] = (
            hedges[1].get(
                "execution_price"
            )
        )

        row["hedge2_qty"] = (
            hedges[1].get(
                "qty"
            )
        )

    return row


# ============================================================
# RUN ONE SCENARIO
# ============================================================

def run_scenario(
    candles,
    funding_book,
    candles_by_ts,
    model_name,
    direction,
    hedge_ratio,
    maintenance_rate,
):
    """
    Sequential account simulation.

    IMPORTANT:
      This does NOT add independent $2 profits together.

    The next cycle starts with the actual account balance
    produced by the previous cycle.

    Therefore:
        final_account
        is a real sequential equity result
        under this model.
    """

    account = CAPITAL_START

    cycle_rows = []

    equity_rows = []

    blocked_rows = []

    i = 0

    cycle_number = 0

    while i < len(candles):

        cycle_number += 1

        cycle = simulate_cycle(
            candles,
            i,
            direction,
            model_name,
            hedge_ratio,
            funding_book,
            candles_by_ts,
            maintenance_rate,
            account,
        )

        if cycle is None:
            break

        # ----------------------------------------------------
        # Blocked entry
        # ----------------------------------------------------

        if cycle["status"] == "BLOCKED":

            blocked_rows.append(
                {
                    "model": model_name,
                    "direction": direction,
                    "timestamp": (
                        cycle.get(
                            "entry_timestamp"
                        )
                    ),
                    "reason": (
                        cycle.get(
                            "blocked_reason"
                        )
                    ),
                    "qty": (
                        cycle.get(
                            "qty"
                        )
                    ),
                    "required_notional": (
                        cycle.get(
                            "required_notional"
                        )
                    ),
                    "account": account,
                }
            )

            # Move one candle forward.
            i += 1

            continue

        # ----------------------------------------------------
        # Save cycle
        # ----------------------------------------------------

        cycle_rows.append(
            cycle_to_row(
                cycle,
                cycle_number,
            )
        )

        account = max(
            0.0,
            float(
                cycle[
                    "equity_at_exit"
                ]
            ),
        )

        # ----------------------------------------------------
        # Equity point
        # ----------------------------------------------------

        equity_rows.append(
            {
                "model": model_name,
                "direction": direction,
                "cycle": cycle_number,
                "timestamp": (
                    cycle[
                        "exit_timestamp"
                    ]
                ),
                "equity": account,
                "cycle_pnl": (
                    cycle[
                        "net_pnl"
                    ]
                ),
            }
        )

        # ----------------------------------------------------
        # Advance to candle AFTER exit.
        #
        # This prevents overlapping cycles and prevents
        # using the same candle for multiple new entries.
        # ----------------------------------------------------

        exit_ts = cycle[
            "exit_timestamp"
        ]

        future_indexes = candles.index[
            candles["timestamp"]
            > exit_ts
        ]

        if len(future_indexes) == 0:
            break

        i = int(
            future_indexes[0]
        )

        # ----------------------------------------------------
        # If account is zero, no more trades.
        # ----------------------------------------------------

        if account <= 0:
            break

    # ========================================================
    # SUMMARY
    # ========================================================

    if cycle_rows:

        trades_df = pd.DataFrame(
            cycle_rows
        )

        closed_df = trades_df[
            trades_df["status"]
            == "CLOSED"
        ].copy()

    else:

        trades_df = pd.DataFrame()

        closed_df = pd.DataFrame()

    if not closed_df.empty:

        cycles = len(
            closed_df
        )

        successful = int(
            closed_df[
                "success"
            ].sum()
        )

        success_pct = (
            successful
            / cycles
            * 100.0
        )

        avg_pnl = float(
            closed_df[
                "net_pnl"
            ].mean()
        )

        max_loss = float(
            closed_df[
                "net_pnl"
            ].min()
        )

        max_drawdown = float(
            closed_df[
                "max_drawdown"
            ].min()
        )

        liquidation_count = int(
            (
                closed_df[
                    "exit_reason"
                ]
                == "LIQUIDATION"
            ).sum()
        )

        target_count = int(
            (
                closed_df[
                    "exit_reason"
                ]
                == "TARGET_+0.20"
            ).sum()
        )

        final_account = float(
            account
        )

        final_return_pct = (
            (
                final_account
                / CAPITAL_START
            )
            - 1.0
        ) * 100.0

        total_net_pnl = float(
            closed_df[
                "net_pnl"
            ].sum()
        )

        avg_duration = float(
            closed_df[
                "duration_minutes"
            ].mean()
        )

    else:

        cycles = 0
        successful = 0
        success_pct = 0.0
        avg_pnl = 0.0
        max_loss = 0.0
        max_drawdown = 0.0
        liquidation_count = 0
        target_count = 0
        final_account = account
        final_return_pct = (
            (
                account
                / CAPITAL_START
            )
            - 1.0
        ) * 100.0
        total_net_pnl = 0.0
        avg_duration = 0.0

    summary = {
        "model": model_name,
        "direction": direction,
        "capital_start": CAPITAL_START,
        "cycles": cycles,
        "successful_cycles": successful,
        "success_pct": success_pct,
        "avg_pnl": avg_pnl,
        "max_loss": max_loss,
        "max_drawdown": max_drawdown,
        "liquidations": liquidation_count,
        "targets": target_count,
        "total_net_pnl": total_net_pnl,
        "final_account": final_account,
        "final_return_pct": final_return_pct,
        "avg_duration_minutes": avg_duration,
        "blocked_entries": len(
            blocked_rows
        ),
    }

    return (
        trades_df,
        pd.DataFrame(
            equity_rows
        ),
        pd.DataFrame(
            blocked_rows
        ),
        summary,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print(
        "=" * 72
    )
    print(
        "BTC HEDGE BACKTEST - KRAKEN FUTURES"
    )
    print(
        f"VERSION {VERSION}"
    )
    print(
        "=" * 72
    )

    if REAL_TRADING:
        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    if not PAPER_TRADING:
        raise RuntimeError(
            "PAPER_TRADING must remain True."
        )

    log(
        "Starting BTC hedge backtest..."
    )

    # ========================================================
    # Date range
    # ========================================================

    end_dt = datetime.now(
        timezone.utc
    )

    start_dt = (
        end_dt
        - timedelta(
            days=LOOKBACK_DAYS
        )
    )

    start_ts = start_dt.timestamp()
    end_ts = end_dt.timestamp()

    log(
        "Range: "
        f"{start_dt.isoformat()} "
        "-> "
        f"{end_dt.isoformat()}"
    )

    # ========================================================
    # Instrument
    # ========================================================

    instrument = (
        fetch_instrument_info()
    )

    actual_min_qty = float(
        instrument.get(
            "min_qty",
            MIN_QTY,
        )
    )

    actual_tick_size = float(
        instrument.get(
            "tick_size",
            TICK_SIZE,
        )
    )

    maintenance_rate = float(
        instrument.get(
            "maintenance_margin_rate",
            MAINTENANCE_MARGIN_RATE,
        )
    )

    # Safety:
    # Kraken documentation currently lists PF_XBTUSD
    # min lot = 0.0001 BTC.
    #
    # Do not accept a suspiciously lower API value.
    actual_min_qty = max(
        actual_min_qty,
        MIN_QTY,
    )

    log(
        "Instrument:"
    )

    log(
        f"  symbol={SYMBOL}"
    )

    log(
        f"  min_qty={actual_min_qty}"
    )

    log(
        f"  tick_size={actual_tick_size}"
    )

    log(
        f"  maintenance_rate="
        f"{maintenance_rate}"
    )

    # ========================================================
    # OHLC
    # ========================================================

    log(
        "Downloading 5m candles..."
    )

    candles = fetch_ohlc(
        start_ts,
        end_ts,
    )

    # Enforce closed candles only.
    #
    # A candle whose timestamp is still in the future
    # relative to current time is removed.
    now_ts = datetime.now(
        timezone.utc
    ).timestamp()

    candles = candles[
        (
            candles["timestamp"]
            + 300
            <= now_ts
        )
    ].copy()

    candles = candles.sort_values(
        "timestamp"
    )

    candles = candles.drop_duplicates(
        subset=["timestamp"]
    )

    candles.reset_index(
        drop=True,
        inplace=True,
    )

    if candles.empty:
        raise RuntimeError(
            "No closed candles available."
        )

    log(
        f"Downloaded {len(candles):,} "
        "closed candles."
    )

    log(
        "First closed candle: "
        f"{ts_to_iso(candles.iloc[0]['timestamp'])}"
    )

    log(
        "Last closed candle: "
        f"{ts_to_iso(candles.iloc[-1]['timestamp'])}"
    )

    # ========================================================
    # Funding
    # ========================================================

    funding_df = fetch_historical_funding(
        start_ts,
        end_ts,
    )

    log(
        f"Funding rows used: "
        f"{len(funding_df):,}"
    )

    if not funding_df.empty:

        log(
            "Funding range: "
            f"{ts_to_iso(funding_df.iloc[0]['timestamp'])}"
            " -> "
            f"{ts_to_iso(funding_df.iloc[-1]['timestamp'])}"
        )

    funding_book = FundingBook(
        funding_df
    )

    # ========================================================
    # Candle lookup
    # ========================================================

    candles_by_ts = {}

    for _, row in candles.iterrows():

        candles_by_ts[
            float(
                row["timestamp"]
            )
        ] = {
            "open": float(
                row["open"]
            ),
            "high": float(
                row["high"]
            ),
            "low": float(
                row["low"]
            ),
            "close": float(
                row["close"]
            ),
        }

    # ========================================================
    # Save funding data
    # ========================================================

    if not funding_df.empty:

        funding_export = (
            funding_df.copy()
        )

        funding_export[
            "timestamp_utc"
        ] = funding_export[
            "timestamp"
        ].apply(
            ts_to_iso
        )

        funding_export.to_csv(
            FUNDING_FILE,
            index=False,
        )

    # ========================================================
    # Run scenarios
    # ========================================================

    all_trades = []

    all_equity = []

    all_blocked = []

    summaries = []

    print()
    print(
        "=" * 72
    )

    for model_name, hedge_ratio in (
        MODELS.items()
    ):

        for direction in DIRECTIONS:

            log(
                f"Running "
                f"{model_name} "
                f"{direction}..."
            )

            (
                trades_df,
                equity_df,
                blocked_df,
                summary,
            ) = run_scenario(
                candles,
                funding_book,
                candles_by_ts,
                model_name,
                direction,
                hedge_ratio,
                maintenance_rate,
            )

            if not trades_df.empty:
                all_trades.append(
                    trades_df
                )

            if not equity_df.empty:
                all_equity.append(
                    equity_df
                )

            if not blocked_df.empty:
                all_blocked.append(
                    blocked_df
                )

            summaries.append(
                summary
            )

            log(
                f"Finished "
                f"{model_name} "
                f"{direction}: "
                f"{summary['cycles']} cycles | "
                f"success "
                f"{summary['success_pct']:.2f}% | "
                f"liq "
                f"{summary['liquidations']} | "
                f"final "
                f"${summary['final_account']:.4f}"
            )

    # ========================================================
    # Export trades
    # ========================================================

    if all_trades:

        trades_all = pd.concat(
            all_trades,
            ignore_index=True,
        )

        trades_all.to_csv(
            TRADES_FILE,
            index=False,
        )

    else:

        trades_all = pd.DataFrame()

    # ========================================================
    # Export equity
    # ========================================================

    if all_equity:

        equity_all = pd.concat(
            all_equity,
            ignore_index=True,
        )

        equity_all.to_csv(
            EQUITY_FILE,
            index=False,
        )

    else:

        equity_all = pd.DataFrame()

    # ========================================================
    # Export blocked
    # ========================================================

    if all_blocked:

        blocked_all = pd.concat(
            all_blocked,
            ignore_index=True,
        )

        blocked_all.to_csv(
            BLOCKED_FILE,
            index=False,
        )

    else:

        blocked_all = pd.DataFrame(
            columns=[
                "model",
                "direction",
                "timestamp",
                "reason",
                "qty",
                "required_notional",
                "account",
            ]
        )

        blocked_all.to_csv(
            BLOCKED_FILE,
            index=False,
        )

    # ========================================================
    # Summary
    # ========================================================

    summary_df = pd.DataFrame(
        summaries
    )

    summary_df.to_csv(
        SUMMARY_FILE,
        index=False,
    )

    # ========================================================
    # Pretty console report
    # ========================================================

    print()
    print(
        "=" * 110
    )

    print(
        "FINAL REPORT"
    )

    print(
        "=" * 110
    )

    print(
        f"{'MODEL':<9}"
        f"{'DIR':<8}"
        f"{'CYCLES':>8}"
        f"{'SUCCESS%':>11}"
        f"{'AVG PNL':>12}"
        f"{'MAX LOSS':>12}"
        f"{'DD':>12}"
        f"{'LIQ':>8}"
        f"{'TARGET':>9}"
        f"{'FINAL $':>12}"
        f"{'RETURN%':>12}"
    )

    print(
        "-" * 110
    )

    for row in summaries:

        print(
            f"{row['model']:<9}"
            f"{row['direction']:<8}"
            f"{row['cycles']:>8}"
            f"{row['success_pct']:>11.2f}"
            f"{row['avg_pnl']:>12.4f}"
            f"{row['max_loss']:>12.4f}"
            f"{row['max_drawdown']:>12.4f}"
            f"{row['liquidations']:>8}"
            f"{row['targets']:>9}"
            f"{row['final_account']:>12.4f}"
            f"{row['final_return_pct']:>12.2f}"
        )

    print(
        "=" * 110
    )

    # ========================================================
    # Explanation
    # ========================================================

    print()
    print(
        "FILES:"
    )

    print(
        f"  {TRADES_FILE}"
    )

    print(
        f"  {SUMMARY_FILE}"
    )

    print(
        f"  {EQUITY_FILE}"
    )

    print(
        f"  {FUNDING_FILE}"
    )

    print(
        f"  {BLOCKED_FILE}"
    )

    print()

    print(
        "MODEL DEFINITIONS:"
    )

    print(
        "  A_50  = 50% hedge per trigger"
    )

    print(
        "  B_75  = 75% hedge per trigger"
    )

    print(
        "  C_100 = 100% hedge per trigger"
    )

    print()

    print(
        "COST MODEL:"
    )

    print(
        f"  Taker fee = "
        f"{TAKER_FEE_RATE * 100:.4f}%"
    )

    print(
        f"  Slippage = "
        f"{SLIPPAGE_RATE * 100:.4f}%"
    )

    print()

    print(
        "IMPORTANT:"
    )

    print(
        "  Final account is sequential."
    )

    print(
        "  Independent $2 cycles are NOT summed."
    )

    print(
        "  Funding uses relativeFundingRate."
    )

    print(
        "  Liquidation uses exact multi-leg PnL."
    )

    print(
        "  One hedge maximum is processed per 5m candle."
    )

    print(
        "  If liquidation and target are both inside"
    )

    print(
        "  the same candle, liquidation wins."
    )

    print(
        "  No real orders are sent."
    )

    print()

    log(
        "Backtest completed successfully."
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:
        main()

    except Exception as e:

        print()
        print(
            "=" * 72
        )
        print(
            "BACKTEST FAILED"
        )
        print(
            "=" * 72
        )

        print(
            str(e)
        )

        print()

        traceback.print_exc()

        try:
            with open(
                LOG_FILE,
                "a",
                encoding="utf-8",
            ) as f:
                f.write(
                    "\nBACKTEST FAILED\n"
                )
                f.write(
                    traceback.format_exc()
                )
        except Exception:
            pass

        raise
