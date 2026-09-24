# ============================================================
# KRAKEN FUTURES BTC HEDGE BACKTEST
# VERSION 3.0
# ============================================================
#
# PAPER / BACKTEST ONLY
#
# PURPOSE:
#   Test whether a REAL $2 account using 10x leverage
#   can reach +10% net equity after:
#
#       - trading fees
#       - slippage
#       - funding
#       - hedge costs
#       - liquidation risk
#
# IMPORTANT CHANGE FROM VERSION 2.x:
#
#   CAPITAL IS NOW A REAL ACCOUNT EQUITY.
#
#   The account is NOT reset to $2 after every cycle.
#
#   Example:
#
#       START      $2.00
#       cycle 1    $1.96
#       cycle 2    $2.03
#       cycle 3    $2.17
#       cycle 4    $2.20  -> TARGET REACHED
#
#   OR:
#
#       START      $2.00
#       cycle 1    $1.40
#       cycle 2    $0.70
#       cycle 3    $0.00  -> LIQUIDATED
#
# No artificial equity reconstruction is allowed.
#
# ============================================================
#
# LEVERAGE:
#   10x
#
# START CAPITAL:
#   $2.00
#
# INITIAL NOTIONAL:
#   Dynamic:
#       equity * leverage
#
#   Therefore:
#       $2.00 * 10 = $20 initial notional
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
# TARGET:
#   +$0.20 net equity
#   = +10% from original $2
#
# CONSERVATIVE INTRABAR RULE:
#   If liquidation and hedge trigger happen in the
#   same 5m candle, liquidation wins.
#
# ============================================================

import csv
import math
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta

import requests


# ============================================================
# CONFIG
# ============================================================

VERSION = "3.0"

REAL_TRADING = False
PAPER_TRADING = True

SYMBOL = "PF_XBTUSD"
TIMEFRAME = "5m"

LOOKBACK_DAYS = 182

# ------------------------------------------------------------
# REAL ACCOUNT EQUITY
# ------------------------------------------------------------

CAPITAL_START = 2.00

LEVERAGE = 10.0

# Dynamic notional:
#
# At $2 equity:
#       $2 * 10 = $20
#
# After equity changes:
#       equity * 10
#
# This keeps leverage consistent instead of keeping
# a fixed $10 position while account equity changes.
DYNAMIC_INITIAL_NOTIONAL = True

FIXED_INITIAL_NOTIONAL = 10.00

# ------------------------------------------------------------
# HEDGE
# ------------------------------------------------------------

HEDGE_TRIGGER_PCT = 0.02

MAX_HEDGES = 2

HEDGE_MODELS = {
    "A_50": 0.50,
    "B_75": 0.75,
    "C_100": 1.00,
}

# ------------------------------------------------------------
# TARGET
# ------------------------------------------------------------

TARGET_NET_PROFIT = 0.20

TARGET_EQUITY = (
    CAPITAL_START
    + TARGET_NET_PROFIT
)

TARGET_RETURN_PCT = (
    TARGET_NET_PROFIT
    / CAPITAL_START
    * 100.0
)

# ------------------------------------------------------------
# COST MODEL
# ------------------------------------------------------------

TAKER_FEE_RATE = 0.0005

SLIPPAGE_RATE = 0.0002

# ------------------------------------------------------------
# LIQUIDATION
# ------------------------------------------------------------

LIQUIDATION_BUFFER_USD = 0.0

DEFAULT_MAINTENANCE_MARGIN_RATE = 0.005

# ------------------------------------------------------------
# CANDLE
# ------------------------------------------------------------

CANDLE_SECONDS = 300

CHUNK_CANDLES = 1000

# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

REQUEST_TIMEOUT = 30

MAX_RETRIES = 5

RETRY_SLEEP = 2.0

# ------------------------------------------------------------
# FUNDING
# ------------------------------------------------------------

FUNDING_INTERVAL_SECONDS = 14400

# ------------------------------------------------------------
# OUTPUTS
# ------------------------------------------------------------

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
            "(compatible; BTC-Hedge-Backtest/3.0)"
        ),
        "Accept": "application/json",
    }
)


# ============================================================
# LOGGING
# ============================================================

def log(message=""):

    now = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    print(
        f"[{now}] {message}",
        flush=True,
    )


# ============================================================
# HTTP
# ============================================================

def request_json(
    url,
    params=None,
    allow_empty=False,
):

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

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
                f"HTTP error "
                f"{response.status_code} "
                f"(attempt "
                f"{attempt}/{MAX_RETRIES})"
            )

        except Exception as exc:

            last_error = exc

            log(
                f"Request error "
                f"(attempt "
                f"{attempt}/{MAX_RETRIES}): "
                f"{exc}"
            )

        if attempt < MAX_RETRIES:

            time.sleep(
                RETRY_SLEEP * attempt
            )

    raise last_error


# ============================================================
# TIME HELPERS
# ============================================================

def utc_now():

    return datetime.now(
        timezone.utc
    )


def dt_to_ts(dt):

    return int(
        dt.timestamp()
    )


def ts_to_dt(ts):

    return datetime.fromtimestamp(
        float(ts),
        tz=timezone.utc,
    )


def parse_timestamp(value):

    if value is None:
        return None

    if isinstance(
        value,
        (int, float),
    ):

        x = float(value)

        if x > 1_000_000_000_000:
            x /= 1000.0

        return int(x)

    text_value = str(
        value
    ).strip()

    if not text_value:
        return None

    try:

        numeric = float(
            text_value
        )

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

        return int(
            dt.timestamp()
        )

    except Exception:

        return None


# ============================================================
# CANDLE PARSING
# ============================================================

def parse_candle(row):

    if isinstance(
        row,
        dict,
    ):

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

    elif isinstance(
        row,
        (list, tuple),
    ):

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

    ts = parse_timestamp(
        timestamp
    )

    if ts is None:
        return None

    try:

        o = float(
            open_price
        )

        h = float(
            high_price
        )

        l = float(
            low_price
        )

        c = float(
            close_price
        )

        v = float(
            volume or 0
        )

    except Exception:

        return None

    if not all(
        math.isfinite(x)
        for x in (
            o,
            h,
            l,
            c,
            v,
        )
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
# CANDLE RESPONSE
# ============================================================

def extract_candle_rows(data):

    if not isinstance(
        data,
        dict,
    ):

        return []

    candidates = [
        data.get("candles"),
        data.get("data"),
        data.get("results"),
    ]

    result = data.get(
        "result"
    )

    if isinstance(
        result,
        dict,
    ):

        candidates.extend(
            [
                result.get("candles"),
                result.get("data"),
                result.get("results"),
            ]
        )

    for candidate in candidates:

        if isinstance(
            candidate,
            list,
        ):

            return candidate

    return []


# ============================================================
# DOWNLOAD CANDLES
# ============================================================

def download_candles():

    end_dt = utc_now()

    start_dt = (
        end_dt
        - timedelta(
            days=LOOKBACK_DAYS
        )
    )

    start_ts = dt_to_ts(
        start_dt
    )

    end_ts = dt_to_ts(
        end_dt
    )

    log(
        f"Downloading Kraken "
        f"{TIMEFRAME} candles..."
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

        rows = extract_candle_rows(
            data
        )

        parsed_count = 0

        for row in rows:

            candle = parse_candle(
                row
            )

            if candle is None:
                continue

            ts = candle[
                "timestamp"
            ]

            if (
                start_ts
                <= ts
                < end_ts
            ):

                candles_by_ts[
                    ts
                ] = candle

                parsed_count += 1

        if candles_by_ts:

            latest_ts = max(
                candles_by_ts.keys()
            )

            log(
                "Candles downloaded: "
                f"{len(candles_by_ts):,} | "
                f"latest="
                f"{ts_to_dt(latest_ts).isoformat()}"
            )

        else:

            log(
                "Candles downloaded: 0"
            )

        cursor = chunk_end

        time.sleep(
            0.05
        )

    candles = [
        candles_by_ts[k]
        for k in sorted(
            candles_by_ts
        )
    ]

    # --------------------------------------------------------
    # Closed candles only.
    # --------------------------------------------------------

    now_ts = dt_to_ts(
        utc_now()
    )

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
        f"Final closed candles: "
        f"{len(candles):,}"
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

    log(
        "Loading Kraken instrument information..."
    )

    data = request_json(
        INSTRUMENTS_URL
    )

    instruments = []

    if isinstance(
        data,
        dict,
    ):

        instruments = (
            data.get(
                "instruments"
            )
            or data.get(
                "result",
                {},
            ).get(
                "instruments",
                [],
            )
        )

    if not isinstance(
        instruments,
        list,
    ):

        instruments = []

    target = None

    for item in instruments:

        if not isinstance(
            item,
            dict,
        ):
            continue

        symbol = str(
            item.get(
                "symbol",
                "",
            )
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
            target.get(
                "maintenanceMarginRate"
            ),
            target.get(
                "maintenanceMargin"
            ),
            target.get(
                "mm"
            ),
        ]

        for value in (
            possible_maintenance
        ):

            try:

                x = float(
                    value
                )

                if (
                    0
                    < x
                    < 1
                ):

                    maintenance_rate = x

                    break

            except Exception:
                pass

        possible_initial = [
            target.get(
                "initialMarginRate"
            ),
            target.get(
                "initialMargin"
            ),
        ]

        for value in (
            possible_initial
        ):

            try:

                x = float(
                    value
                )

                if (
                    0
                    < x
                    < 1
                ):

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

    if (
        reference_initial_margin
        is not None
    ):

        log(
            "Exchange reference initial margin: "
            f"{reference_initial_margin:.4%}"
        )

    return {
        "symbol": SYMBOL,
        "type": instrument_type,
        "tradeable": tradeable,
        "maintenance_margin_rate": (
            maintenance_rate
        ),
        "reference_initial_margin": (
            reference_initial_margin
        ),
    }


# ============================================================
# FUNDING
# ============================================================

def normalize_funding_rate(value):

    try:

        x = float(
            value
        )

    except Exception:

        return None

    if not math.isfinite(x):

        return None

    return x


def extract_funding_records_from_standard(
    data,
):

    if not isinstance(
        data,
        dict,
    ):

        return []

    candidates = [
        data.get(
            "rates"
        )
    ]

    result = data.get(
        "result"
    )

    if isinstance(
        result,
        dict,
    ):

        candidates.append(
            result.get(
                "rates"
            )
        )

    for candidate in candidates:

        if not isinstance(
            candidate,
            list,
        ):

            continue

        records = []

        for item in candidate:

            if not isinstance(
                item,
                dict,
            ):

                continue

            ts = parse_timestamp(
                item.get(
                    "timestamp"
                )
                or item.get(
                    "time"
                )
            )

            if (
                item.get(
                    "fundingRate"
                )
                is not None
            ):

                rate = normalize_funding_rate(
                    item.get(
                        "fundingRate"
                    )
                )

            else:

                rate = normalize_funding_rate(
                    item.get(
                        "relativeFundingRate"
                    )
                )

            if (
                ts is None
                or rate is None
            ):

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


def extract_funding_records_from_analytics(
    data,
):

    if not isinstance(
        data,
        dict,
    ):

        return []

    root = data

    if isinstance(
        data.get("result"),
        dict,
    ):

        root = data[
            "result"
        ]

    timestamps = (
        root.get(
            "timestamp"
        )
        or root.get(
            "timestamps"
        )
        or []
    )

    payload = root.get(
        "data"
    )

    if isinstance(
        payload,
        dict,
    ):

        rate_values = (
            payload.get(
                "rate"
            )
            or payload.get(
                "fundingRate"
            )
            or payload.get(
                "funding_rate"
            )
            or []
        )

        if not timestamps:

            timestamps = (
                payload.get(
                    "timestamp"
                )
                or payload.get(
                    "timestamps"
                )
                or []
            )

    else:

        rate_values = (
            root.get(
                "rate"
            )
            or root.get(
                "fundingRate"
            )
            or []
        )

    if not isinstance(
        timestamps,
        list,
    ):

        return []

    if not isinstance(
        rate_values,
        list,
    ):

        return []

    records = []

    for (
        ts_value,
        rate_value,
    ) in zip(
        timestamps,
        rate_values,
    ):

        ts = parse_timestamp(
            ts_value
        )

        rate = normalize_funding_rate(
            rate_value
        )

        if (
            ts is None
            or rate is None
        ):

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
        - timedelta(
            days=LOOKBACK_DAYS
        )
    )

    records = []

    # --------------------------------------------------------
    # Historical endpoint
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
    # Analytics fallback
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
                    "interval": (
                        FUNDING_INTERVAL_SECONDS
                    ),
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

    filtered = []

    for row in records:

        ts = row[
            "timestamp"
        ]

        if (
            start_ts
            <= ts
            <= end_ts
        ):

            filtered.append(
                row
            )

    dedup = {}

    for row in filtered:

        dedup[
            row["timestamp"]
        ] = row

    funding = [
        dedup[k]
        for k in sorted(
            dedup
        )
    ]

    if not funding:

        log(
            "WARNING: Kraken returned no usable "
            "funding rows for the requested period."
        )

        log(
            "Backtest will continue with "
            "funding cost = 0 where no funding "
            "event exists."
        )

    else:

        log(
            f"Final funding rows: "
            f"{len(funding):,}"
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
# PRICE LOOKUP
# ============================================================

def price_before_timestamp(
    candles,
    timestamp,
):

    lo = 0

    hi = len(candles) - 1

    answer = None

    while lo <= hi:

        mid = (
            lo + hi
        ) // 2

        ts = candles[
            mid
        ]["timestamp"]

        if ts <= timestamp:

            answer = candles[
                mid
            ]["close"]

            lo = mid + 1

        else:

            hi = mid - 1

    return answer


# ============================================================
# POSITION HELPERS
# ============================================================

def apply_slippage(
    price,
    side,
):

    if side == "BUY":

        return price * (
            1.0
            + SLIPPAGE_RATE
        )

    return price * (
        1.0
        - SLIPPAGE_RATE
    )


def execution_fee(
    notional,
):

    return (
        abs(notional)
        * TAKER_FEE_RATE
    )


def signed_pnl(
    qty,
    entry_price,
    exit_price,
):

    return (
        qty
        * (
            exit_price
            - entry_price
        )
    )


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
        abs(pos["qty"])
        * price
        for pos in positions
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


def position_direction(
    qty,
):

    if qty > 0:
        return "LONG"

    if qty < 0:
        return "SHORT"

    return "FLAT"


# ============================================================
# MARGIN
# ============================================================

def margin_requirement(
    positions,
    price,
):

    notional = total_open_notional(
        positions,
        price,
    )

    return (
        notional
        / LEVERAGE
    )


def available_margin(
    cash,
    positions,
    price,
):

    used_margin = margin_requirement(
        positions,
        price,
    )

    return max(
        0.0,
        cash
        - used_margin,
    )


def can_add_position(
    cash,
    positions,
    new_qty,
    price,
):

    if cash <= 0:
        return False

    new_notional = (
        abs(new_qty)
        * price
    )

    new_margin = (
        new_notional
        / LEVERAGE
    )

    new_fee = execution_fee(
        new_notional
    )

    used_margin = margin_requirement(
        positions,
        price,
    )

    total_required = (
        used_margin
        + new_margin
        + new_fee
    )

    return (
        total_required
        <= cash
        + 1e-12
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

    net_qty = sum(
        pos["qty"]
        for pos in positions
    )

    total_abs_qty = sum(
        abs(pos["qty"])
        for pos in positions
    )

    if (
        abs(net_qty)
        < 1e-12
    ):

        # Fully hedged.
        # There is no directional liquidation
        # under this simplified cross-margin model.
        return None

    weighted_entry = (
        sum(
            abs(pos["qty"])
            * pos["entry_price"]
            for pos in positions
        )
        / total_abs_qty
    )

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

    liq = (
        -b
        / a
    )

    if liq <= 0:
        return None

    return liq


def is_liquidation_hit(
    candle,
    liq_price,
    net_qty,
):

    if (
        liq_price is None
        or abs(net_qty) < 1e-12
    ):

        return False

    if net_qty > 0:

        return (
            candle["low"]
            <= liq_price
        )

    return (
        candle["high"]
        >= liq_price
    )


# ============================================================
# DRAWDOWN
# ============================================================

def update_drawdown(
    account,
    equity,
):

    if equity > account[
        "peak_equity"
    ]:

        account[
            "peak_equity"
        ] = equity

    dd = (
        equity
        - account[
            "peak_equity"
        ]
    )

    if dd < account[
        "max_drawdown"
    ]:

        account[
            "max_drawdown"
        ] = dd


# ============================================================
# POSITION CREATION
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
# CLOSE POSITIONS
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

        qty = pos[
            "qty"
        ]

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

        slippage_cost = (
            abs(qty)
            * abs(
                exit_price
                - raw_price
            )
        )

        total_pnl += pnl

        total_fee += fee

        total_slippage += (
            slippage_cost
        )

        fills.append(
            {
                "label": pos["label"],
                "qty": qty,
                "entry_price": pos[
                    "entry_price"
                ],
                "exit_price": exit_price,
                "pnl": pnl,
                "fee": fee,
                "slippage": (
                    slippage_cost
                ),
            }
        )

    return (
        total_pnl,
        total_fee,
        total_slippage,
        fills,
    )


# ============================================================
# FUNDING
# ============================================================

def apply_funding_events(
    account,
    positions,
    funding,
    candles,
    last_funding_ts,
    current_ts,
    cycle,
):

    if not positions:

        return (
            last_funding_ts
        )

    events = get_funding_events_between(
        funding,
        last_funding_ts,
        current_ts,
    )

    for event in events:

        event_ts = event[
            "timestamp"
        ]

        if (
            event_ts
            <= last_funding_ts
        ):

            continue

        funding_price = price_before_timestamp(
            candles,
            event_ts,
        )

        if funding_price is None:

            funding_price = (
                positions[0][
                    "entry_price"
                ]
            )

        rate = event[
            "rate"
        ]

        for pos in positions:

            notional = (
                abs(pos["qty"])
                * funding_price
            )

            # Positive funding:
            #
            # LONG pays
            # SHORT receives
            #
            # Negative funding:
            #
            # LONG receives
            # SHORT pays
            payment = (
                pos["qty"]
                * funding_price
                * rate
            )

            account[
                "equity_cash"
            ] -= payment

            cycle[
                "funding_total"
            ] += (
                -payment
            )

            cycle[
                "funding_events"
            ] += 1

            cycle[
                "funding_records"
            ].append(
                {
                    "timestamp": event_ts,
                    "datetime": (
                        ts_to_dt(
                            event_ts
                        ).isoformat()
                    ),
                    "rate": rate,
                    "position_label": (
                        pos["label"]
                    ),
                    "qty": pos["qty"],
                    "price": funding_price,
                    "notional": notional,
                    "payment": payment,
                    "cash_effect": (
                        -payment
                    ),
                    "source": event.get(
                        "source",
                        "unknown",
                    ),
                }
            )

        last_funding_ts = event_ts

    return (
        last_funding_ts
    )


# ============================================================
# HEDGE SIZE
# ============================================================

def hedge_qty_for_model(
    original_qty,
    hedge_ratio,
):

    return (
        -original_qty
        * hedge_ratio
    )


# ============================================================
# CREATE ACCOUNT
# ============================================================

def create_account():

    return {
        "equity_cash": CAPITAL_START,
        "peak_equity": CAPITAL_START,
        "max_drawdown": 0.0,
        "total_realized_pnl": 0.0,
        "total_fees": 0.0,
        "total_slippage": 0.0,
        "total_funding": 0.0,
        "liquidations": 0,
        "target_reached": False,
        "account_failed": False,
    }


# ============================================================
# INITIAL NOTIONAL
# ============================================================

def calculate_initial_notional(
    equity,
):

    if DYNAMIC_INITIAL_NOTIONAL:

        return (
            equity
            * LEVERAGE
        )

    return FIXED_INITIAL_NOTIONAL


# ============================================================
# CREATE CYCLE
# ============================================================

def create_cycle(
    account,
    model_name,
    direction,
    first_candle,
):

    raw_entry = first_candle[
        "close"
    ]

    equity_before = (
        account[
            "equity_cash"
        ]
    )

    initial_notional = (
        calculate_initial_notional(
            equity_before
        )
    )

    if (
        initial_notional
        <= 0
    ):

        return None

    initial_qty_abs = (
        initial_notional
        / raw_entry
    )

    if direction == "LONG":

        initial_qty = abs(
            initial_qty_abs
        )

    else:

        initial_qty = -abs(
            initial_qty_abs
        )

    position = create_position(
        initial_qty,
        raw_entry,
        first_candle[
            "timestamp"
        ],
        "INITIAL",
    )

    entry_fee = position[
        "entry_fee"
    ]

    # Entry fee is immediately deducted
    # from real account cash.
    account[
        "equity_cash"
    ] -= entry_fee

    account[
        "total_fees"
    ] += entry_fee

    if (
        account[
            "equity_cash"
        ]
        <= 0
    ):

        account[
            "account_failed"
        ] = True

        return None

    initial_equity = current_equity(
        account[
            "equity_cash"
        ],
        [position],
        raw_entry,
    )

    cycle = {
        "model": model_name,
        "direction": direction,

        "start_timestamp": (
            first_candle[
                "timestamp"
            ]
        ),

        "entry_price": (
            position[
                "entry_price"
            ]
        ),

        "entry_raw_price": raw_entry,

        "initial_notional": (
            initial_notional
        ),

        "initial_qty": (
            initial_qty
        ),

        "equity_before": (
            equity_before
        ),

        "positions": [
            position
        ],

        "hedge_count": 0,

        "hedge_prices": [],

        "hedge_sizes": [],

        "gross_pnl": 0.0,

        "fees": entry_fee,

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

        "equity_after": None,

        "net_pnl": None,

        "net_return_pct": None,
    }

    return cycle


# ============================================================
# FINALIZE CYCLE
# ============================================================

def finalize_cycle(
    cycle,
    account,
):

    final_equity = (
        account[
            "equity_cash"
        ]
    )

    equity_before = (
        cycle[
            "equity_before"
        ]
    )

    cycle[
        "equity_after"
    ] = final_equity

    cycle[
        "net_pnl"
    ] = (
        final_equity
        - equity_before
    )

    if equity_before > 0:

        cycle[
            "net_return_pct"
        ] = (
            cycle["net_pnl"]
            / equity_before
            * 100.0
        )

    else:

        cycle[
            "net_return_pct"
        ] = -100.0

    cycle[
        "duration_hours"
    ] = (
        cycle[
            "duration_seconds"
        ]
        / 3600.0
    )

    cycle[
        "hedge1_price"
    ] = (
        cycle[
            "hedge_prices"
        ][0]
        if len(
            cycle[
                "hedge_prices"
            ]
        ) >= 1
        else None
    )

    cycle[
        "hedge2_price"
    ] = (
        cycle[
            "hedge_prices"
        ][1]
        if len(
            cycle[
                "hedge_prices"
            ]
        ) >= 2
        else None
    )

    cycle[
        "hedge1_size"
    ] = (
        cycle[
            "hedge_sizes"
        ][0]
        if len(
            cycle[
                "hedge_sizes"
            ]
        ) >= 1
        else None
    )

    cycle[
        "hedge2_size"
    ] = (
        cycle[
            "hedge_sizes"
        ][1]
        if len(
            cycle[
                "hedge_sizes"
            ]
        ) >= 2
        else None
    )

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

        cycle[
            "liquidation_distance_pct"
        ] = (
            abs(
                liq_price
                - entry_price
            )
            / entry_price
            * 100.0
        )

    else:

        cycle[
            "liquidation_distance_pct"
        ] = None

    return cycle


# ============================================================
# CLOSE CYCLE
# ============================================================

def close_cycle(
    cycle,
    account,
    raw_exit_price,
    timestamp,
    reason,
):

    (
        pnl,
        fee,
        slippage,
        fills,
    ) = close_all_positions(
        cycle[
            "positions"
        ],
        raw_exit_price,
        timestamp,
    )

    cycle[
        "gross_pnl"
    ] += pnl

    cycle[
        "fees"
    ] += fee

    cycle[
        "exit_fees"
    ] += fee

    cycle[
        "slippage"
    ] += slippage

    cycle[
        "exit_slippage"
    ] += slippage

    # Real account cash changes only here
    # by realized PnL and exit fee.
    account[
        "equity_cash"
    ] += pnl

    account[
        "equity_cash"
    ] -= fee

    account[
        "total_realized_pnl"
    ] += pnl

    account[
        "total_fees"
    ] += fee

    account[
        "total_slippage"
    ] += slippage

    cycle[
        "exit_raw_price"
    ] = raw_exit_price

    cycle[
        "exit_price"
    ] = (
        fills[-1][
            "exit_price"
        ]
        if fills
        else raw_exit_price
    )

    cycle[
        "exit_timestamp"
    ] = timestamp

    cycle[
        "exit_reason"
    ] = reason

    cycle[
        "duration_seconds"
    ] = (
        timestamp
        - cycle[
            "start_timestamp"
        ]
    )

    return finalize_cycle(
        cycle,
        account,
    )


# ============================================================
# RUN CYCLE
# ============================================================

def run_cycle(
    candles,
    start_index,
    model_name,
    hedge_ratio,
    direction,
    funding,
    maintenance_rate,
    account,
):

    if (
        start_index
        >= len(candles)
    ):

        return (
            None,
            len(candles),
        )

    if (
        account[
            "account_failed"
        ]
        or account[
            "target_reached"
        ]
    ):

        return (
            None,
            len(candles),
        )

    first_candle = candles[
        start_index
    ]

    cycle = create_cycle(
        account=account,
        model_name=model_name,
        direction=direction,
        first_candle=first_candle,
    )

    if cycle is None:

        return (
            None,
            len(candles),
        )

    initial_qty = cycle[
        "initial_qty"
    ]

    first_entry_price = cycle[
        "entry_price"
    ]

    if direction == "LONG":

        hedge_trigger_price = (
            first_entry_price
            * (
                1.0
                - HEDGE_TRIGGER_PCT
            )
        )

    else:

        hedge_trigger_price = (
            first_entry_price
            * (
                1.0
                + HEDGE_TRIGGER_PCT
            )
        )

    last_funding_ts = (
        first_candle[
            "timestamp"
        ]
    )

    i = start_index + 1

    while i < len(candles):

        candle = candles[i]

        # ----------------------------------------------------
        # FUNDING
        # ----------------------------------------------------

        last_funding_ts = (
            apply_funding_events(
                account=account,
                positions=cycle[
                    "positions"
                ],
                funding=funding,
                candles=candles,
                last_funding_ts=last_funding_ts,
                current_ts=candle[
                    "timestamp"
                ],
                cycle=cycle,
            )
        )

        account_equity_open = current_equity(
            account[
                "equity_cash"
            ],
            cycle[
                "positions"
            ],
            candle[
                "open"
            ],
        )

        update_drawdown(
            account,
            account_equity_open,
        )

        update_cycle_drawdown(
            cycle,
            account_equity_open,
        )

        # ----------------------------------------------------
        # NET POSITION
        # ----------------------------------------------------

        net_qty = sum(
            pos["qty"]
            for pos in cycle[
                "positions"
            ]
        )

        # ----------------------------------------------------
        # LIQUIDATION
        # ----------------------------------------------------

        liq_price = (
            approximate_liquidation_price(
                account[
                    "equity_cash"
                ],
                cycle[
                    "positions"
                ],
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
        # HEDGE TRIGGER
        # ----------------------------------------------------

        hedge_trigger_hit = False

        if (
            cycle[
                "hedge_count"
            ]
            < MAX_HEDGES
        ):

            if direction == "LONG":

                hedge_trigger_hit = (
                    candle[
                        "low"
                    ]
                    <= hedge_trigger_price
                )

            else:

                hedge_trigger_hit = (
                    candle[
                        "high"
                    ]
                    >= hedge_trigger_price
                )

        # ----------------------------------------------------
        # LIQUIDATION WINS SAME CANDLE
        # ----------------------------------------------------

        if liquidation_hit:

            raw_exit = (
                liq_price
                if liq_price is not None
                else candle[
                    "close"
                ]
            )

            cycle = close_cycle(
                cycle=cycle,
                account=account,
                raw_exit_price=raw_exit,
                timestamp=candle[
                    "timestamp"
                ],
                reason="LIQUIDATION",
            )

            cycle[
                "liquidation_count"
            ] = 1

            account[
                "liquidations"
            ] += 1

            # Liquidation means the account cannot
            # continue as if nothing happened.
            if (
                account[
                    "equity_cash"
                ]
                < 0
            ):

                account[
                    "equity_cash"
                ] = 0.0

            update_drawdown(
                account,
                account[
                    "equity_cash"
                ],
            )

            update_cycle_drawdown(
                cycle,
                account[
                    "equity_cash"
                ],
            )

            if (
                account[
                    "equity_cash"
                ]
                <= 0
            ):

                account[
                    "account_failed"
                ] = True

            return (
                cycle,
                i + 1,
            )

        # ----------------------------------------------------
        # HEDGE
        # ----------------------------------------------------

        if hedge_trigger_hit:

            hedge_qty = hedge_qty_for_model(
                initial_qty,
                hedge_ratio,
            )

            # Do not allow a hedge to use more
            # margin than the account actually has.
            if can_add_position(
                account[
                    "equity_cash"
                ],
                cycle[
                    "positions"
                ],
                hedge_qty,
                hedge_trigger_price,
            ):

                hedge_label = (
                    "HEDGE_"
                    + str(
                        cycle[
                            "hedge_count"
                        ]
                        + 1
                    )
                )

                hedge_position = create_position(
                    hedge_qty,
                    hedge_trigger_price,
                    candle[
                        "timestamp"
                    ],
                    hedge_label,
                )

                hedge_fee = hedge_position[
                    "entry_fee"
                ]

                account[
                    "equity_cash"
                ] -= hedge_fee

                account[
                    "total_fees"
                ] += hedge_fee

                cycle[
                    "fees"
                ] += hedge_fee

                cycle[
                    "hedge_count"
                ] += 1

                cycle[
                    "hedge_prices"
                ].append(
                    hedge_position[
                        "entry_price"
                    ]
                )

                cycle[
                    "hedge_sizes"
                ].append(
                    abs(
                        hedge_qty
                    )
                )

                cycle[
                    "positions"
                ].append(
                    hedge_position
                )

                # Next hedge is another 2%
                # against the original direction.
                if (
                    cycle[
                        "hedge_count"
                    ]
                    < MAX_HEDGES
                ):

                    if direction == "LONG":

                        hedge_trigger_price *= (
                            1.0
                            - HEDGE_TRIGGER_PCT
                        )

                    else:

                        hedge_trigger_price *= (
                            1.0
                            + HEDGE_TRIGGER_PCT
                        )

            else:

                cycle[
                    "blocked_hedges"
                ] += 1

                # Important:
                # once a hedge is blocked, count that
                # hedge slot as consumed so the system
                # does not repeatedly attempt it.
                cycle[
                    "hedge_count"
                ] += 1

        # ----------------------------------------------------
        # TARGET CHECK
        #
        # Calculate the actual amount the account would
        # have after closing all positions at candle close,
        # including exit fee and exit slippage.
        # ----------------------------------------------------

        raw_target_price = candle[
            "close"
        ]

        projected_pnl = unrealized_pnl(
            cycle[
                "positions"
            ],
            raw_target_price,
        )

        projected_exit_notional = (
            total_open_notional(
                cycle[
                    "positions"
                ],
                raw_target_price,
            )
        )

        projected_exit_fee = (
            execution_fee(
                projected_exit_notional
            )
        )

        projected_exit_slippage = sum(
            abs(
                pos["qty"]
            )
            * (
                SLIPPAGE_RATE
                * raw_target_price
            )
            for pos in cycle[
                "positions"
            ]
        )

        projected_equity = (
            account[
                "equity_cash"
            ]
            + projected_pnl
            - projected_exit_fee
            - projected_exit_slippage
        )

        update_drawdown(
            account,
            projected_equity,
        )

        update_cycle_drawdown(
            cycle,
            projected_equity,
        )

        # ----------------------------------------------------
        # TARGET
        # ----------------------------------------------------

        if (
            projected_equity
            >= TARGET_EQUITY
        ):

            cycle = close_cycle(
                cycle=cycle,
                account=account,
                raw_exit_price=raw_target_price,
                timestamp=candle[
                    "timestamp"
                ],
                reason="TARGET",
            )

            cycle[
                "target_hit"
            ] = True

            account[
                "target_reached"
            ] = True

            update_drawdown(
                account,
                account[
                    "equity_cash"
                ],
            )

            update_cycle_drawdown(
                cycle,
                account[
                    "equity_cash"
                ],
            )

            return (
                cycle,
                i + 1,
            )

        # ----------------------------------------------------
        # CLOSE MARK
        # ----------------------------------------------------

        close_equity = current_equity(
            account[
                "equity_cash"
            ],
            cycle[
                "positions"
            ],
            candle[
                "close"
            ],
        )

        update_drawdown(
            account,
            close_equity,
        )

        update_cycle_drawdown(
            cycle,
            close_equity,
        )

        # ----------------------------------------------------
        # ACCOUNT FAILURE
        # ----------------------------------------------------

        if (
            close_equity
            <= 0
        ):

            cycle = close_cycle(
                cycle=cycle,
                account=account,
                raw_exit_price=candle[
                    "close"
                ],
                timestamp=candle[
                    "timestamp"
                ],
                reason="ACCOUNT_BLOWN",
            )

            account[
                "equity_cash"
            ] = 0.0

            account[
                "account_failed"
            ] = True

            return (
                cycle,
                i + 1,
            )

        i += 1

    # ========================================================
    # END OF DATA
    # ========================================================

    final_candle = candles[
        -1
    ]

    cycle = close_cycle(
        cycle=cycle,
        account=account,
        raw_exit_price=final_candle[
            "close"
        ],
        timestamp=final_candle[
            "timestamp"
        ],
        reason="END_OF_DATA",
    )

    update_drawdown(
        account,
        account[
            "equity_cash"
        ],
    )

    update_cycle_drawdown(
        cycle,
        account[
            "equity_cash"
        ],
    )

    return (
        cycle,
        len(candles),
    )


# ============================================================
# CYCLE DRAWDOWN
# ============================================================

def update_cycle_drawdown(
    cycle,
    equity,
):

    if equity > cycle[
        "peak_equity"
    ]:

        cycle[
            "peak_equity"
        ] = equity

    dd = (
        equity
        - cycle[
            "peak_equity"
        ]
    )

    if dd < cycle[
        "max_drawdown"
    ]:

        cycle[
            "max_drawdown"
        ] = dd


# ============================================================
# RUN SCENARIO
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

    log(
        "=" * 80
    )

    log(
        f"SCENARIO: "
        f"{model_name} | "
        f"{direction}"
    )

    log(
        "=" * 80
    )

    account = create_account()

    cycles = []

    equity_history = []

    index = 0

    cycle_number = 0

    while (
        index
        < len(candles) - 1
    ):

        if account[
            "target_reached"
        ]:

            break

        if account[
            "account_failed"
        ]:

            break

        equity_before = (
            account[
                "equity_cash"
            ]
        )

        result, next_index = run_cycle(
            candles=candles,
            start_index=index,
            model_name=model_name,
            hedge_ratio=hedge_ratio,
            direction=direction,
            funding=funding,
            maintenance_rate=maintenance_rate,
            account=account,
        )

        if result is None:

            break

        cycle_number += 1

        result[
            "cycle_number"
        ] = cycle_number

        result[
            "account_equity_after"
        ] = account[
            "equity_cash"
        ]

        result[
            "account_return_pct"
        ] = (
            (
                account[
                    "equity_cash"
                ]
                - CAPITAL_START
            )
            / CAPITAL_START
            * 100.0
        )

        cycles.append(
            result
        )

        equity_history.append(
            {
                "model": model_name,
                "direction": direction,
                "cycle_number": cycle_number,
                "start_timestamp": (
                    result[
                        "start_timestamp"
                    ]
                ),
                "exit_timestamp": (
                    result[
                        "exit_timestamp"
                    ]
                ),
                "exit_reason": (
                    result[
                        "exit_reason"
                    ]
                ),
                "equity_before": equity_before,
                "equity_after": (
                    account[
                        "equity_cash"
                    ]
                ),
                "cycle_pnl": (
                    result[
                        "net_pnl"
                    ]
                ),
                "account_return_pct": (
                    result[
                        "account_return_pct"
                    ]
                ),
            }
        )

        if next_index <= index:

            next_index = (
                index + 1
            )

        index = next_index

    log(
        f"Scenario cycles: "
        f"{len(cycles)}"
    )

    log(
        f"Final equity: "
        f"${account['equity_cash']:.6f}"
    )

    # --------------------------------------------------------
    # FIX:
    # Do not place a multiline expression directly inside
    # an f-string format expression.
    # Compatible with Python 3.11.
    # --------------------------------------------------------

    scenario_return_pct = (
        (
            account["equity_cash"]
            - CAPITAL_START
        )
        / CAPITAL_START
        * 100.0
    )

    log(
        f"Return: "
        f"{scenario_return_pct:.4f}%"
    )

    if account[
        "target_reached"
    ]:

        log(
            "TARGET: REACHED"
        )

    elif account[
        "account_failed"
    ]:

        log(
            "ACCOUNT: LIQUIDATED / FAILED"
        )

    else:

        log(
            "TARGET: NOT REACHED"
        )

    return (
        cycles,
        equity_history,
        account,
    )


# ============================================================
# SUMMARY
# ============================================================

def summarize_scenario(
    cycles,
    equity_history,
    account,
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
            "average_cycle_pnl": 0.0,
            "average_cycle_return_pct": 0.0,
            "max_cycle_loss": 0.0,
            "max_drawdown_usd": (
                account[
                    "max_drawdown"
                ]
            ),
            "max_drawdown_pct": (
                account[
                    "max_drawdown"
                ]
                / CAPITAL_START
                * 100.0
            ),
            "liquidations": (
                account[
                    "liquidations"
                ]
            ),
            "target_hits": 0,
            "end_of_data": 0,
            "account_blown": (
                1
                if account[
                    "account_failed"
                ]
                else 0
            ),
            "blocked_hedges": 0,
            "starting_equity": CAPITAL_START,
            "final_equity": (
                account[
                    "equity_cash"
                ]
            ),
            "net_pnl": (
                account[
                    "equity_cash"
                ]
                - CAPITAL_START
            ),
            "return_pct": (
                (
                    account[
                        "equity_cash"
                    ]
                    - CAPITAL_START
                )
                / CAPITAL_START
                * 100.0
            ),
            "target_equity": TARGET_EQUITY,
            "target_reached": (
                1
                if account[
                    "target_reached"
                ]
                else 0
            ),
            "total_fees": (
                account[
                    "total_fees"
                ]
            ),
            "total_slippage": (
                account[
                    "total_slippage"
                ]
            ),
            "total_funding": (
                account[
                    "total_funding"
                ]
            ),
            "funding_events": 0,
        }

    profits = [
        c[
            "net_pnl"
        ]
        for c in cycles
    ]

    returns = [
        c[
            "net_return_pct"
        ]
        for c in cycles
    ]

    successful = [
        c
        for c in cycles
        if c[
            "net_pnl"
        ] > 0
    ]

    liquidations = sum(
        1
        for c in cycles
        if c[
            "exit_reason"
        ]
        == "LIQUIDATION"
    )

    targets = sum(
        1
        for c in cycles
        if c[
            "exit_reason"
        ]
        == "TARGET"
    )

    end_data = sum(
        1
        for c in cycles
        if c[
            "exit_reason"
        ]
        == "END_OF_DATA"
    )

    account_blown = sum(
        1
        for c in cycles
        if c[
            "exit_reason"
        ]
        == "ACCOUNT_BLOWN"
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

        "average_cycle_pnl": (
            sum(profits)
            / len(profits)
        ),

        "average_cycle_return_pct": (
            sum(returns)
            / len(returns)
        ),

        "max_cycle_loss": min(
            profits
        ),

        "max_drawdown_usd": (
            account[
                "max_drawdown"
            ]
        ),

        "max_drawdown_pct": (
            account[
                "max_drawdown"
            ]
            / CAPITAL_START
            * 100.0
        ),

        "liquidations": liquidations,

        "target_hits": targets,

        "end_of_data": end_data,

        "account_blown": account_blown,

        "blocked_hedges": sum(
            c[
                "blocked_hedges"
            ]
            for c in cycles
        ),

        "starting_equity": (
            CAPITAL_START
        ),

        "final_equity": (
            account[
                "equity_cash"
            ]
        ),

        "net_pnl": (
            account[
                "equity_cash"
            ]
            - CAPITAL_START
        ),

        "return_pct": (
            (
                account[
                    "equity_cash"
                ]
                - CAPITAL_START
            )
            / CAPITAL_START
            * 100.0
        ),

        "target_equity": TARGET_EQUITY,

        "target_reached": (
            1
            if account[
                "target_reached"
            ]
            else 0
        ),

        "total_fees": (
            account[
                "total_fees"
            ]
        ),

        "total_slippage": (
            account[
                "total_slippage"
            ]
        ),

        "total_funding": (
            account[
                "total_funding"
            ]
        ),

        "funding_events": sum(
            c[
                "funding_events"
            ]
            for c in cycles
        ),
    }


# ============================================================
# CSV: TRADES
# ============================================================

def write_trades_csv(
    all_cycles,
):

    fields = [
        "model",
        "direction",
        "cycle_number",

        "start_datetime",

        "equity_before",

        "initial_notional",

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

        "equity_after",

        "account_equity_after",

        "account_return_pct",

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
                    "model": c[
                        "model"
                    ],

                    "direction": c[
                        "direction"
                    ],

                    "cycle_number": c.get(
                        "cycle_number",
                        "",
                    ),

                    "start_datetime": (
                        ts_to_dt(
                            c[
                                "start_timestamp"
                            ]
                        ).isoformat()
                    ),

                    "equity_before": c[
                        "equity_before"
                    ],

                    "initial_notional": c[
                        "initial_notional"
                    ],

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
                            c[
                                "exit_timestamp"
                            ]
                        ).isoformat()
                        if c[
                            "exit_timestamp"
                        ] is not None
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

                    "equity_after": c[
                        "equity_after"
                    ],

                    "account_equity_after": c[
                        "account_equity_after"
                    ],

                    "account_return_pct": c[
                        "account_return_pct"
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
    summaries,
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

            writer.writerow(
                row
            )


# ============================================================
# CSV: FUNDING
# ============================================================

def write_funding_csv(
    funding,
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
                            row[
                                "timestamp"
                            ]
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
    all_equity_history,
):

    fields = [
        "model",
        "direction",
        "cycle_number",
        "start_datetime",
        "exit_datetime",
        "exit_reason",
        "equity_before",
        "cycle_pnl",
        "equity_after",
        "account_return_pct",
    ]

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

        for row in all_equity_history:

            writer.writerow(
                {
                    "model": row[
                        "model"
                    ],

                    "direction": row[
                        "direction"
                    ],

                    "cycle_number": row[
                        "cycle_number"
                    ],

                    "start_datetime": (
                        ts_to_dt(
                            row[
                                "start_timestamp"
                            ]
                        ).isoformat()
                    ),

                    "exit_datetime": (
                        ts_to_dt(
                            row[
                                "exit_timestamp"
                            ]
                        ).isoformat()
                        if row[
                            "exit_timestamp"
                        ] is not None
                        else ""
                    ),

                    "exit_reason": row[
                        "exit_reason"
                    ],

                    "equity_before": row[
                        "equity_before"
                    ],

                    "cycle_pnl": row[
                        "cycle_pnl"
                    ],

                    "equity_after": row[
                        "equity_after"
                    ],

                    "account_return_pct": row[
                        "account_return_pct"
                    ],
                }
            )


# ============================================================
# CONSOLE REPORT
# ============================================================

def print_summary(
    summaries,
):

    log("")

    log(
        "=" * 120
    )

    log(
        "FINAL BTC HEDGE BACKTEST REPORT"
    )

    log(
        "=" * 120
    )

    log(
        f"Version: {VERSION}"
    )

    log(
        f"Capital start: "
        f"${CAPITAL_START:.2f}"
    )

    log(
        f"Leverage: "
        f"{LEVERAGE:.1f}x"
    )

    if DYNAMIC_INITIAL_NOTIONAL:

        log(
            "Initial notional: "
            "DYNAMIC = equity × leverage"
        )

        log(
            f"Initial notional at start: "
            f"${CAPITAL_START * LEVERAGE:.2f}"
        )

    else:

        log(
            f"Initial notional: "
            f"${FIXED_INITIAL_NOTIONAL:.2f}"
        )

    log(
        f"Hedge trigger: "
        f"{HEDGE_TRIGGER_PCT:.2%}"
    )

    log(
        f"Maximum hedges: "
        f"{MAX_HEDGES}"
    )

    log(
        f"Target equity: "
        f"${TARGET_EQUITY:.2f}"
    )

    log(
        f"Target return: "
        f"+{TARGET_RETURN_PCT:.2f}%"
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
        "MAX DD $   LIQ   TARGET   "
        "FINAL $   RETURN%   STATUS"
    )

    print(header)

    print(
        "-" * len(header)
    )

    for s in summaries:

        if s[
            "target_reached"
        ]:

            status = "TARGET"

        elif s[
            "account_blown"
        ]:

            status = "BLOWN"

        else:

            status = "NOT_REACHED"

        print(
            f"{s['model']:<10} "
            f"{s['direction']:<6} "
            f"{s['cycles']:>7} "
            f"{s['success_pct']:>10.2f} "
            f"{s['average_cycle_pnl']:>10.5f} "
            f"{s['max_cycle_loss']:>10.5f} "
            f"{s['max_drawdown_usd']:>10.5f} "
            f"{s['liquidations']:>5} "
            f"{s['target_hits']:>8} "
            f"{s['final_equity']:>9.5f} "
            f"{s['return_pct']:>9.2f} "
            f"{status}"
        )

    log("")

    log(
        "=" * 120
    )

    log(
        "ACCOUNT MODEL:"
    )

    log(
        "Each scenario starts with exactly "
        f"${CAPITAL_START:.2f}."
    )

    log(
        "Equity is carried forward between cycles."
    )

    log(
        "Equity is NEVER reset to $2 after a cycle."
    )

    log(
        "If target is reached, that scenario stops."
    )

    log(
        "If the account is blown, that scenario stops."
    )

    log("")

    log(
        "IMPORTANT:"
    )

    log(
        "The six scenarios are independent."
    )

    log(
        "They are NOT traded simultaneously."
    )

    log(
        "FINAL EQUITY is the actual simulated "
        "account equity for each independent scenario."
    )

    log(
        "=" * 120
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("")

    print(
        "=" * 80
    )

    print(
        "KRAKEN FUTURES BTC HEDGE BACKTEST"
    )

    print(
        f"VERSION {VERSION}"
    )

    print(
        "=" * 80
    )

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
    # Validation
    # --------------------------------------------------------

    if CAPITAL_START <= 0:

        raise RuntimeError(
            "CAPITAL_START must be positive."
        )

    if LEVERAGE <= 0:

        raise RuntimeError(
            "LEVERAGE must be positive."
        )

    if (
        not DYNAMIC_INITIAL_NOTIONAL
        and FIXED_INITIAL_NOTIONAL <= 0
    ):

        raise RuntimeError(
            "FIXED_INITIAL_NOTIONAL must be positive."
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

    write_funding_csv(
        funding
    )

    # --------------------------------------------------------
    # Scenarios
    # --------------------------------------------------------

    all_cycles = []

    all_equity_history = []

    summaries = []

    for (
        model_name,
        hedge_ratio,
    ) in HEDGE_MODELS.items():

        for direction in (
            "LONG",
            "SHORT",
        ):

            (
                cycles,
                equity_history,
                account,
            ) = run_scenario(
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

            all_equity_history.extend(
                equity_history
            )

            summary = summarize_scenario(
                cycles=cycles,
                equity_history=equity_history,
                account=account,
                model_name=model_name,
                direction=direction,
            )

            summaries.append(
                summary
            )

    # --------------------------------------------------------
    # OUTPUTS
    # --------------------------------------------------------

    write_trades_csv(
        all_cycles
    )

    write_summary_csv(
        summaries
    )

    write_equity_csv(
        all_equity_history
    )

    # --------------------------------------------------------
    # REPORT
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

        print(
            "=" * 80
        )

        print(
            "BACKTEST FAILED"
        )

        print(
            "=" * 80
        )

        print(
            f"{type(exc).__name__}: {exc}"
        )

        traceback.print_exc()

        print(
            "=" * 80
        )

        sys.exit(1)
