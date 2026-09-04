# ============================================================
# GLOBAL CANDIDATE COLLECTION
# ============================================================

def get_visible_candidates(results):

    candidates = []

    for result in results:

        symbol = result["symbol"]

        diagnostic = result.get(
            "diagnostic",
            {}
        )

        for candidate in diagnostic.get(
            "candidate_details",
            []
        ):

            score = candidate.get(
                "score",
                0
            )

            if score >= MIN_DISPLAY_CANDIDATE_SCORE:

                item = dict(candidate)

                item["symbol"] = symbol

                candidates.append(item)

    candidates = sorted(
        candidates,
        key=lambda x: (
            x.get("score", 0),
            x.get("volume_ratio", 0)
        ),
        reverse=True
    )

    return candidates


# ============================================================
# GLOBAL FINAL SIGNAL
# ============================================================

def get_global_final_signal(
    results,
    state
):

    ready_candidates = []

    for result in results:

        symbol = result["symbol"]

        diagnostic = result.get(
            "diagnostic",
            {}
        )

        for candidate in diagnostic.get(
            "candidate_details",
            []
        ):

            if not candidate.get(
                "final_ready",
                False
            ):

                continue

            if candidate.get(
                "score",
                0
            ) < MIN_SIGNAL_SCORE:

                continue

            if (
                not ALLOW_MULTIPLE_OPEN_PER_SYMBOL
                and has_open_trade_for_symbol(
                    state,
                    symbol
                )
            ):

                continue

            ready_candidates.append({
                "symbol": symbol,
                "candidate": candidate
            })

    if not ready_candidates:

        return None

    ready_candidates = sorted(
        ready_candidates,
        key=lambda x: (
            x["candidate"].get(
                "score",
                0
            ),
            x["candidate"].get(
                "volume_ratio",
                0
            )
        ),
        reverse=True
    )

    winner = ready_candidates[0]

    candidate = winner["candidate"]

    side = candidate["side"]

    signal_time = None

    for result in results:

        if result["symbol"] == winner["symbol"]:

            signal_time = int(
                result["df"]["time"].iloc[-1]
            )

            break

    if signal_time is None:

        return None

    return {
        "symbol":
            winner["symbol"],

        "side":
            side,

        "entry":
            candidate["entry"],

        "sl":
            candidate["sl"],

        "tp1":
            candidate["tp1"],

        "tp2":
            candidate["tp2"],

        "tp3":
            candidate["tp3"],

        "tp":
            candidate["tp1"],

        "sl_percent":
            level_percent(
                side,
                candidate["entry"],
                candidate["sl"]
            ),

        "tp1_percent":
            level_percent(
                side,
                candidate["entry"],
                candidate["tp1"]
            ),

        "tp2_percent":
            level_percent(
                side,
                candidate["entry"],
                candidate["tp2"]
            ),

        "tp3_percent":
            level_percent(
                side,
                candidate["entry"],
                candidate["tp3"]
            ),

        "risk_percent":
            abs(
                level_percent(
                    side,
                    candidate["entry"],
                    candidate["sl"]
                )
            ),

        "atr":
            None,

        "atr_multiplier":
            SL_ATR_MULTIPLIER,

        "signal_time":
            signal_time,

        "signal_time_iso":
            datetime.fromtimestamp(
                signal_time / 1000,
                tz=timezone.utc
            ).isoformat(),

        "trend_15m":
            candidate["trend_15m"],

        "trend_1h":
            candidate["trend_1h"],

        "ut_trigger":
            candidate["ut"],

        "trendline_break":
            candidate["trendline"],

        "volume_ratio":
            candidate["volume_ratio"],

        "score":
            candidate["score"],

        "score_label":
            candidate["label"],

        "score_components":
            candidate["components"],

        "reason":
            "RSI Divergence + UT/Trendline + 15M/1H Trend",
    }


# ============================================================
# SIGNAL FORMAT
# ============================================================

def format_signal(signal):

    side = str(
        signal["side"]
    ).upper()

    icon = (
        "🟢"
        if side == "BUY"
        else "🔴"
    )

    entry = signal["entry"]
    sl = signal["sl"]
    tp1 = signal["tp1"]
    tp2 = signal["tp2"]
    tp3 = signal["tp3"]

    score = signal.get(
        "score",
        0
    )

    score_label_value = signal.get(
        "score_label",
        score_label(score)
    )

    text = []

    text.append(
        f"{icon} *FINAL SIGNAL*"
    )

    text.append(
        f"*{signal['symbol']}/USDT - "
        f"{side}* ⭐ *{score}/100*"
    )

    text.append(
        f"Score: {score_label_value}"
    )

    text.append(
        f"Entry: {format_price(entry)}"
    )

    text.append(
        f"Stop Loss: "
        f"{format_price(sl)} "
        f"({level_percent(side, entry, sl):+.2f}%)"
    )

    text.append(
        f"Target 1: "
        f"{format_price(tp1)} "
        f"({level_percent(side, entry, tp1):+.2f}%) "
        f"→ {TP1_CLOSE_PERCENT}%"
    )

    text.append(
        f"Target 2: "
        f"{format_price(tp2)} "
        f"({level_percent(side, entry, tp2):+.2f}%) "
        f"→ {TP2_CLOSE_PERCENT}%"
    )

    text.append(
        f"Target 3: "
        f"{format_price(tp3)} "
        f"({level_percent(side, entry, tp3):+.2f}%) "
        f"→ {TP3_CLOSE_PERCENT}%"
    )

    text.append(
        f"Risk: "
        f"{abs(level_percent(side, entry, sl)):.2f}%"
    )

    text.append(
        f"RR: "
        f"1:{TP1_R_MULTIPLE:.1f} / "
        f"1:{TP2_R_MULTIPLE:.1f} / "
        f"1:{TP3_R_MULTIPLE:.1f}"
    )

    text.append(
        f"15M Trend: "
        f"{signal.get('trend_15m', 'N/A')}"
    )

    text.append(
        f"1H Trend: "
        f"{signal.get('trend_1h', 'N/A')}"
    )

    text.append(
        f"Volume: "
        f"{signal.get('volume_ratio', 0):.2f}x"
    )

    text.append(
        "TP Management: "
        "TP1 → SL Entry | "
        "TP2 → SL TP1"
    )

    text.append(
        f"Reason: {signal['reason']}"
    )

    return "\n".join(text)


# ============================================================
# CANDIDATE FORMAT
# ============================================================

def format_candidate(
    symbol,
    candidate,
    rank=None
):

    side = candidate["side"]

    icon = (
        "🟢"
        if side == "BUY"
        else "🔴"
    )

    score = candidate["score"]

    label = candidate["label"]

    components = candidate[
        "components"
    ]

    lines = []

    rank_text = (
        f"#{rank} "
        if rank is not None
        else ""
    )

    lines.append(
        f"{rank_text}{icon} "
        f"*{symbol}/USDT {side}* "
        f"⭐ *{score}/100* {label}"
    )

    lines.append(
        f"RSI Divergence "
        f"+{components['divergence']}"
    )

    lines.append(
        f"UT Bot "
        f"+{components['ut']}"
    )

    lines.append(
        f"Trendline "
        f"+{components['trendline']}"
    )

    lines.append(
        f"15M Trend "
        f"+{components['trend_15m']}"
    )

    lines.append(
        f"1H Trend "
        f"+{components['trend_1h']}"
    )

    lines.append(
        f"Volume "
        f"+{components['volume']} "
        f"({candidate['volume_ratio']:.2f}x)"
    )

    lines.append(
        f"Entry: "
        f"{format_price(candidate.get('entry'))}"
    )

    if candidate.get("sl") is not None:

        lines.append(
            f"SL: "
            f"{format_price(candidate.get('sl'))}"
        )

    if candidate.get("tp1") is not None:

        lines.append(
            f"TP1: "
            f"{format_price(candidate.get('tp1'))}"
        )

    if candidate.get("tp2") is not None:

        lines.append(
            f"TP2: "
            f"{format_price(candidate.get('tp2'))}"
        )

    if candidate.get("tp3") is not None:

        lines.append(
            f"TP3: "
            f"{format_price(candidate.get('tp3'))}"
        )

    if candidate["final_ready"]:

        lines.append(
            "STATUS: 🚨 READY"
        )

    elif candidate["rejection"] == "SCORE":

        lines.append(
            "STATUS: 🟡 BELOW SIGNAL THRESHOLD"
        )

    elif candidate["rejection"] == "SL":

        lines.append(
            "STATUS: ❌ INVALID SL"
        )

    elif candidate["rejection"] == "TREND_FILTER":

        lines.append(
            "STATUS: ❌ TREND FILTER"
        )

    else:

        lines.append(
            "STATUS: 🟡 WATCH"
        )

    return "\n".join(lines)


# ============================================================
# CLOSED SIGNAL FORMAT
# ============================================================

def format_closed_signal(
    trade
):

    side = normalize_side(
        trade
    )

    coin = normalize_coin(
        trade
    )

    icon = (
        "🟢"
        if side == "BUY"
        else "🔴"
    )

    reason = (
        trade.get(
            "result_reason"
        )
        or trade.get(
            "exit_reason"
        )
        or "UNKNOWN"
    )

    reason_upper = str(
        reason
    ).upper()

    result_icon = (

        "✅"
        if reason_upper.startswith("TP")

        else "❌"
        if reason_upper == "SL"

        else "⚪"

    )

    entry = get_trade_entry(
        trade
    )

    exit_price = trade.get(
        "result_price",
        trade.get("exit_price")
    )

    try:

        exit_price = float(
            exit_price
        )

    except Exception:

        exit_price = None

    try:

        pnl = float(
            trade.get(
                "pnl_percent",
                0
            )
        )

    except Exception:

        pnl = 0

    try:

        r_multiple = float(
            trade.get(
                "result_r",
                trade.get(
                    "r_multiple",
                    0
                )
            )
        )

    except Exception:

        r_multiple = 0

    duration = trade.get(
        "duration_minutes",
        0
    )

    score = trade.get(
        "score"
    )

    score_text = ""

    if score is not None:

        try:

            score_text = (
                f" | ⭐ "
                f"{float(score):.0f}/100"
            )

        except Exception:

            pass

    return "\n".join([

        f"{icon} {coin} {side} | "
        f"{result_icon} {reason}"
        f"{score_text}",

        f"Entry: "
        f"{format_price(entry)}",

        f"Exit: "
        f"{format_price(exit_price)}",

        f"P&L: "
        f"{pnl:+.2f}%",

        f"R: "
        f"{r_multiple:+.2f}R",

        f"Duration: "
        f"{float(duration):.0f} min",

    ])


# ============================================================
# OPEN PERFORMANCE
# ============================================================

def calculate_open_performance(
    state,
    data_cache
):

    result = []

    for trade in get_all_trades(
        state
    ):

        if str(
            trade.get(
                "status",
                ""
            )
        ).upper() != "OPEN":

            continue

        initialize_trade_tp_state(
            trade
        )

        coin = normalize_coin(
            trade
        )

        df = data_cache.get(
            coin
        )

        if df is None or df.empty:

            continue

        entry = get_trade_entry(
            trade
        )

        sl = get_trade_sl(
            trade
        )

        side = normalize_side(
            trade
        )

        if (
            entry is None
            or sl is None
            or side not in (
                "BUY",
                "SELL"
            )
        ):

            continue

        current_price = float(
            df["close"].iloc[-1]
        )

        if side == "BUY":

            pnl = (
                current_price
                - entry
            ) / entry * 100

        else:

            pnl = (
                entry
                - current_price
            ) / entry * 100

        risk = abs(
            entry - sl
        )

        if risk > 0:

            if side == "BUY":

                current_r = (
                    current_price
                    - entry
                ) / risk

            else:

                current_r = (
                    entry
                    - current_price
                ) / risk

        else:

            current_r = 0

        signal_time = (
            parse_trade_time(
                trade.get(
                    "signal_time"
                )
            )
        )

        if signal_time is None:

            signal_time = (
                parse_trade_time(
                    trade.get(
                        "signal_time_iso"
                    )
                )
            )

        if signal_time is None:

            mfe = 0

            mae = 0

            duration = 0

        else:

            after_signal = df[
                df["time"]
                > signal_time
            ]

            if after_signal.empty:

                mfe = 0

                mae = 0

            elif side == "BUY":

                best = float(
                    after_signal[
                        "high"
                    ].max()
                )

                worst = float(
                    after_signal[
                        "low"
                    ].min()
                )

                mfe = (
                    best - entry
                ) / entry * 100

                mae = (
                    worst - entry
                ) / entry * 100

            else:

                best = float(
                    after_signal[
                        "low"
                    ].min()
                )

                worst = float(
                    after_signal[
                        "high"
                    ].max()
                )

                mfe = (
                    entry - best
                ) / entry * 100

                mae = (
                    entry - worst
                ) / entry * 100

            last_time = int(
                df["time"].iloc[-1]
            )

            duration = max(
                0,
                (
                    last_time
                    - signal_time
                ) / 60000
            )

        result.append({

            "symbol":
                coin,

            "side":
                side,

            "entry":
                entry,

            "sl":
                sl,

            "tp1":
                get_trade_tp1(trade),

            "tp2":
                get_trade_tp2(trade),

            "tp3":
                get_trade_tp3(trade),

            "current_price":
                current_price,

            "pnl_percent":
                pnl,

            "current_r":
                current_r,

            "mfe":
                mfe,

            "mae":
                mae,

            "duration_minutes":
                duration,

            "score":
                trade.get("score"),

            "tp1_hit":
                get_tp1_hit(trade),

            "tp2_hit":
                get_tp2_hit(trade),

            "tp3_hit":
                get_tp3_hit(trade),

            "remaining_percent":
                get_remaining_percent(
                    trade
                ),

        })

    return result


# ============================================================
# PARTIAL TP / TRADE EVALUATION
# ============================================================

def evaluate_open_trades(
    state,
    data_cache
):

    changed = False

    for trade in get_all_trades(
        state
    ):

        if str(
            trade.get(
                "status",
                ""
            )
        ).upper() != "OPEN":

            continue

        initialize_trade_tp_state(
            trade
        )

        coin = normalize_coin(
            trade
        )

        df = data_cache.get(
            coin
        )

        if df is None or df.empty:

            continue

        side = normalize_side(
            trade
        )

        entry = get_trade_entry(
            trade
        )

        original_sl = (
            trade.get(
                "original_sl",
                trade.get("sl")
            )
        )

        try:

            original_sl = float(
                original_sl
            )

        except Exception:

            continue

        if (
            entry is None
            or side not in (
                "BUY",
                "SELL"
            )
        ):

            continue

        if "original_sl" not in trade:

            trade["original_sl"] = (
                original_sl
            )

        tp1 = get_trade_tp1(
            trade
        )

        tp2 = get_trade_tp2(
            trade
        )

        tp3 = get_trade_tp3(
            trade
        )

        signal_time = (
            parse_trade_time(
                trade.get(
                    "signal_time"
                )
            )
        )

        if signal_time is None:

            signal_time = (
                parse_trade_time(
                    trade.get(
                        "signal_time_iso"
                    )
                )
            )

        if signal_time is None:

            continue

        after_signal = df[
            df["time"]
            > signal_time
        ]

        if after_signal.empty:

            continue

        risk = abs(
            entry
            - original_sl
        )

        if risk <= 0:

            continue

        for _, row in after_signal.iterrows():

            high = float(
                row["high"]
            )

            low = float(
                row["low"]
            )

            candle_time = int(
                row["time"]
            )

            # =================================================
            # CURRENT SL
            # =================================================

            current_sl = get_trade_sl(
                trade
            )

            if current_sl is None:

                current_sl = original_sl

            # =================================================
            # SL CHECK
            # =================================================

            sl_hit = False

            if side == "BUY":

                if low <= current_sl:

                    sl_hit = True

            else:

                if high >= current_sl:

                    sl_hit = True

            if sl_hit:

                remaining = (
                    get_remaining_percent(
                        trade
                    )
                )

                if remaining <= 0:

                    trade["status"] = (
                        "CLOSED"
                    )

                    break

                if side == "BUY":

                    pnl_remaining = (
                        current_sl
                        - entry
                    ) / entry * 100

                    r_remaining = (
                        current_sl
                        - entry
                    ) / risk

                else:

                    pnl_remaining = (
                        entry
                        - current_sl
                    ) / entry * 100

                    r_remaining = (
                        entry
                        - current_sl
                    ) / risk

                weighted_pnl = (
                    pnl_remaining
                    * remaining
                    / 100
                )

                weighted_r = (
                    r_remaining
                    * remaining
                    / 100
                )

                trade[
                    "realized_pnl_percent"
                ] = (
                    float(
                        trade.get(
                            "realized_pnl_percent",
                            0
                        )
                    )
                    + weighted_pnl
                )

                trade[
                    "realized_r"
                ] = (
                    float(
                        trade.get(
                            "realized_r",
                            0
                        )
                    )
                    + weighted_r
                )

                trade[
                    "remaining_percent"
                ] = 0

                trade[
                    "status"
                ] = "CLOSED"

                trade[
                    "exit_reason"
                ] = "SL"

                trade[
                    "result_reason"
                ] = "SL"

                trade[
                    "exit_price"
                ] = current_sl

                trade[
                    "result_price"
                ] = current_sl

                trade[
                    "exit_time"
                ] = candle_time

                trade[
                    "pnl_percent"
                ] = float(
                    trade.get(
                        "realized_pnl_percent",
                        0
                    )
                )

                trade[
                    "r_multiple"
                ] = float(
                    trade.get(
                        "realized_r",
                        0
                    )
                )

                trade[
                    "result_r"
                ] = float(
                    trade.get(
                        "realized_r",
                        0
                    )
                )

                trade[
                    "duration_minutes"
                ] = max(
                    0,
                    (
                        candle_time
                        - signal_time
                    ) / 60000
                )

                trade[
                    "closed_at"
                ] = datetime.fromtimestamp(
                    candle_time / 1000,
                    tz=timezone.utc
                ).isoformat()

                changed = True

                break

            # =================================================
            # TP1
            # =================================================

            if (
                not get_tp1_hit(trade)
                and tp1 is not None
            ):

                tp1_hit = (

                    high >= tp1

                    if side == "BUY"

                    else low <= tp1

                )

                if tp1_hit:

                    realized = (
                        TP1_CLOSE_PERCENT
                        / 100
                    )

                    if side == "BUY":

                        pnl_part = (
                            tp1 - entry
                        ) / entry * 100

                        r_part = (
                            tp1 - entry
                        ) / risk

                    else:

                        pnl_part = (
                            entry - tp1
                        ) / entry * 100

                        r_part = (
                            entry - tp1
                        ) / risk

                    trade[
                        "realized_pnl_percent"
                    ] += (
                        pnl_part
                        * realized
                    )

                    trade[
                        "realized_r"
                    ] += (
                        r_part
                        * realized
                    )

                    trade[
                        "remaining_percent"
                    ] -= (
                        TP1_CLOSE_PERCENT
                    )

                    trade[
                        "tp1_hit"
                    ] = True

                    trade[
                        "tp1_time"
                    ] = candle_time

                    if (
                        MOVE_SL_TO_ENTRY_AFTER_TP1
                    ):

                        trade[
                            "sl"
                        ] = entry

                        trade[
                            "sl_moved_to_entry"
                        ] = True

                    changed = True

            # =================================================
            # TP2
            # =================================================

            if (
                get_tp1_hit(trade)
                and not get_tp2_hit(trade)
                and tp2 is not None
            ):

                tp2_hit = (

                    high >= tp2

                    if side == "BUY"

                    else low <= tp2

                )

                if tp2_hit:

                    realized = (
                        TP2_CLOSE_PERCENT
                        / 100
                    )

                    if side == "BUY":

                        pnl_part = (
                            tp2 - entry
                        ) / entry * 100

                        r_part = (
                            tp2 - entry
                        ) / risk

                    else:

                        pnl_part = (
                            entry - tp2
                        ) / entry * 100

                        r_part = (
                            entry - tp2
                        ) / risk

                    trade[
                        "realized_pnl_percent"
                    ] += (
                        pnl_part
                        * realized
                    )

                    trade[
                        "realized_r"
                    ] += (
                        r_part
                        * realized
                    )

                    trade[
                        "remaining_percent"
                    ] -= (
                        TP2_CLOSE_PERCENT
                    )

                    trade[
                        "tp2_hit"
                    ] = True

                    trade[
                        "tp2_time"
                    ] = candle_time

                    if (
                        MOVE_SL_TO_TP1_AFTER_TP2
                    ):

                        trade[
                            "sl"
                        ] = tp1

                        trade[
                            "sl_moved_to_tp1"
                        ] = True

                    changed = True

            # =================================================
            # TP3
            # =================================================

            if (
                get_tp2_hit(trade)
                and not get_tp3_hit(trade)
                and tp3 is not None
            ):

                tp3_hit = (

                    high >= tp3

                    if side == "BUY"

                    else low <= tp3

                )

                if tp3_hit:

                    realized = (
                        get_remaining_percent(
                            trade
                        ) / 100
                    )

                    if side == "BUY":

                        pnl_part = (
                            tp3 - entry
                        ) / entry * 100

                        r_part = (
                            tp3 - entry
                        ) / risk

                    else:

                        pnl_part = (
                            entry - tp3
                        ) / entry * 100

                        r_part = (
                            entry - tp3
                        ) / risk

                    trade[
                        "realized_pnl_percent"
                    ] += (
                        pnl_part
                        * realized
                    )

                    trade[
                        "realized_r"
                    ] += (
                        r_part
                        * realized
                    )

                    trade[
                        "remaining_percent"
                    ] = 0

                    trade[
                        "tp3_hit"
                    ] = True

                    trade[
                        "tp3_time"
                    ] = candle_time

                    trade[
                        "status"
                    ] = "CLOSED"

                    trade[
                        "exit_reason"
                    ] = "TP3"

                    trade[
                        "result_reason"
                    ] = "TP3"

                    trade[
                        "exit_price"
                    ] = tp3

                    trade[
                        "result_price"
                    ] = tp3

                    trade[
                        "exit_time"
                    ] = candle_time

                    trade[
                        "pnl_percent"
                    ] = float(
                        trade[
                            "realized_pnl_percent"
                        ]
                    )

                    trade[
                        "r_multiple"
                    ] = float(
                        trade[
                            "realized_r"
                        ]
                    )

                    trade[
                        "result_r"
                    ] = float(
                        trade[
                            "realized_r"
                        ]
                    )

                    trade[
                        "duration_minutes"
                    ] = max(
                        0,
                        (
                            candle_time
                            - signal_time
                        ) / 60000
                    )

                    trade[
                        "closed_at"
                    ] = datetime.fromtimestamp(
                        candle_time / 1000,
                        tz=timezone.utc
                    ).isoformat()

                    changed = True

                    break

    return changed


# ============================================================
# NEWLY CLOSED TRADES
# ============================================================

def get_newly_closed_trades(
    state,
    previous_closed_ids
):

    result = []

    for trade in get_all_trades(
        state
    ):

        if str(
            trade.get(
                "status",
                ""
            )
        ).upper() != "CLOSED":

            continue

        trade_id = get_trade_id(
            trade
        )

        if (
            trade_id
            and trade_id
            not in previous_closed_ids
        ):

            result.append(
                trade
            )

    return result


# ============================================================
# STATISTICS
# ============================================================

def calculate_statistics(
    state
):

    trades = get_all_trades(
        state
    )

    open_trades = [

        x for x in trades

        if str(
            x.get(
                "status",
                ""
            )
        ).upper()
        == "OPEN"

    ]

    closed_trades = [

        x for x in trades

        if str(
            x.get(
                "status",
                ""
            )
        ).upper()
        == "CLOSED"

    ]

    wins = []

    losses = []

    for trade in closed_trades:

        try:

            pnl = float(
                trade.get(
                    "pnl_percent",
                    0
                )
            )

        except Exception:

            pnl = 0

        if pnl > 0:

            wins.append(
                trade
            )

        else:

            losses.append(
                trade
            )

    total_pnl = sum(

        float(
            x.get(
                "pnl_percent",
                0
            )
        )

        for x in closed_trades

    )

    win_rate = (

        len(wins)
        / len(closed_trades)
        * 100

        if closed_trades

        else 0

    )

    avg_win = (

        float(
            np.mean([

                float(
                    x.get(
                        "pnl_percent",
                        0
                    )
                )

                for x in wins

            ])
        )

        if wins

        else 0

    )

    avg_loss = (

        float(
            np.mean([

                float(
                    x.get(
                        "pnl_percent",
                        0
                    )
                )

                for x in losses

            ])
        )

        if losses

        else 0

    )

    return {

        "total":
            len(trades),

        "open":
            len(open_trades),

        "closed":
            len(closed_trades),

        "wins":
            len(wins),

        "losses":
            len(losses),

        "win_rate":
            win_rate,

        "total_pnl":
            total_pnl,

        "avg_win":
            avg_win,

        "avg_loss":
            avg_loss,

    }


# ============================================================
# DIAGNOSTICS
# ============================================================

def calculate_diagnostics(
    results
):

    stats = {

        "bullish_divergence":
            0,

        "bearish_divergence":
            0,

        "bullish_break":
            0,

        "bearish_break":
            0,

        "ut_buy":
            0,

        "ut_sell":
            0,

        "buy_candidates":
            0,

        "sell_candidates":
            0,

        "setup_candidates":
            0,

        "visible_candidates":
            0,

        "ready_candidates":
            0,

        "buy_trend_ready":
            0,

        "sell_trend_ready":
            0,

    }

    for result in results:

        diagnostic = result.get(
            "diagnostic",
            {}
        )

        if diagnostic.get(
            "bullish_divergence"
        ):

            stats[
                "bullish_divergence"
            ] += 1

        if diagnostic.get(
            "bearish_divergence"
        ):

            stats[
                "bearish_divergence"
            ] += 1

        if diagnostic.get(
            "bullish_break"
        ):

            stats[
                "bullish_break"
            ] += 1

        if diagnostic.get(
            "bearish_break"
        ):

            stats[
                "bearish_break"
            ] += 1

        if diagnostic.get(
            "ut_buy"
        ):

            stats[
                "ut_buy"
            ] += 1

        if diagnostic.get(
            "ut_sell"
        ):

            stats[
                "ut_sell"
            ] += 1

        candidate_sides = (
            diagnostic.get(
                "candidate_sides",
                []
            )
        )

        if "BUY" in candidate_sides:

            stats[
                "buy_candidates"
            ] += 1

        if "SELL" in candidate_sides:

            stats[
                "sell_candidates"
            ] += 1

        stats[
            "setup_candidates"
        ] += len(
            candidate_sides
        )

        for candidate in diagnostic.get(
            "candidate_details",
            []
        ):

            score = candidate.get(
                "score",
                0
            )

            if (
                score
                >= MIN_DISPLAY_CANDIDATE_SCORE
            ):

                stats[
                    "visible_candidates"
                ] += 1

            if candidate.get(
                "final_ready",
                False
            ):

                stats[
                    "ready_candidates"
                ] += 1

        if diagnostic.get(
            "buy_trend_ok"
        ):

            stats[
                "buy_trend_ready"
            ] += 1

        if diagnostic.get(
            "sell_trend_ok"
        ):

            stats[
                "sell_trend_ready"
            ] += 1

    return stats
    # ============================================================
# GLOBAL CANDIDATE COLLECTION
# ============================================================

def get_visible_candidates(results):

    candidates = []

    for result in results:

        symbol = result["symbol"]

        diagnostic = result.get(
            "diagnostic",
            {}
        )

        for candidate in diagnostic.get(
            "candidate_details",
            []
        ):

            score = candidate.get(
                "score",
                0
            )

            if score >= MIN_DISPLAY_CANDIDATE_SCORE:

                item = dict(candidate)

                item["symbol"] = symbol

                candidates.append(item)

    candidates = sorted(
        candidates,
        key=lambda x: (
            x.get("score", 0),
            x.get("volume_ratio", 0)
        ),
        reverse=True
    )

    return candidates


# ============================================================
# GLOBAL FINAL SIGNAL
# ============================================================

def get_global_final_signal(
    results,
    state
):

    ready_candidates = []

    for result in results:

        symbol = result["symbol"]

        diagnostic = result.get(
            "diagnostic",
            {}
        )

        for candidate in diagnostic.get(
            "candidate_details",
            []
        ):

            if not candidate.get(
                "final_ready",
                False
            ):

                continue

            if candidate.get(
                "score",
                0
            ) < MIN_SIGNAL_SCORE:

                continue

            if (
                not ALLOW_MULTIPLE_OPEN_PER_SYMBOL
                and has_open_trade_for_symbol(
                    state,
                    symbol
                )
            ):

                continue

            ready_candidates.append({
                "symbol": symbol,
                "candidate": candidate
            })

    if not ready_candidates:

        return None

    ready_candidates = sorted(
        ready_candidates,
        key=lambda x: (
            x["candidate"].get(
                "score",
                0
            ),
            x["candidate"].get(
                "volume_ratio",
                0
            )
        ),
        reverse=True
    )

    winner = ready_candidates[0]

    candidate = winner["candidate"]

    side = candidate["side"]

    signal_time = None

    for result in results:

        if result["symbol"] == winner["symbol"]:

            signal_time = int(
                result["df"]["time"].iloc[-1]
            )

            break

    if signal_time is None:

        return None

    return {
        "symbol":
            winner["symbol"],

        "side":
            side,

        "entry":
            candidate["entry"],

        "sl":
            candidate["sl"],

        "tp1":
            candidate["tp1"],

        "tp2":
            candidate["tp2"],

        "tp3":
            candidate["tp3"],

        "tp":
            candidate["tp1"],

        "sl_percent":
            level_percent(
                side,
                candidate["entry"],
                candidate["sl"]
            ),

        "tp1_percent":
            level_percent(
                side,
                candidate["entry"],
                candidate["tp1"]
            ),

        "tp2_percent":
            level_percent(
                side,
                candidate["entry"],
                candidate["tp2"]
            ),

        "tp3_percent":
            level_percent(
                side,
                candidate["entry"],
                candidate["tp3"]
            ),

        "risk_percent":
            abs(
                level_percent(
                    side,
                    candidate["entry"],
                    candidate["sl"]
                )
            ),

        "atr":
            None,

        "atr_multiplier":
            SL_ATR_MULTIPLIER,

        "signal_time":
            signal_time,

        "signal_time_iso":
            datetime.fromtimestamp(
                signal_time / 1000,
                tz=timezone.utc
            ).isoformat(),

        "trend_15m":
            candidate["trend_15m"],

        "trend_1h":
            candidate["trend_1h"],

        "ut_trigger":
            candidate["ut"],

        "trendline_break":
            candidate["trendline"],

        "volume_ratio":
            candidate["volume_ratio"],

        "score":
            candidate["score"],

        "score_label":
            candidate["label"],

        "score_components":
            candidate["components"],

        "reason":
            "RSI Divergence + UT/Trendline + 15M/1H Trend",
    }


# ============================================================
# SIGNAL FORMAT
# ============================================================

def format_signal(signal):

    side = str(
        signal["side"]
    ).upper()

    icon = (
        "🟢"
        if side == "BUY"
        else "🔴"
    )

    entry = signal["entry"]
    sl = signal["sl"]
    tp1 = signal["tp1"]
    tp2 = signal["tp2"]
    tp3 = signal["tp3"]

    score = signal.get(
        "score",
        0
    )

    score_label_value = signal.get(
        "score_label",
        score_label(score)
    )

    text = []

    text.append(
        f"{icon} *FINAL SIGNAL*"
    )

    text.append(
        f"*{signal['symbol']}/USDT - "
        f"{side}* ⭐ *{score}/100*"
    )

    text.append(
        f"Score: {score_label_value}"
    )

    text.append(
        f"Entry: {format_price(entry)}"
    )

    text.append(
        f"Stop Loss: "
        f"{format_price(sl)} "
        f"({level_percent(side, entry, sl):+.2f}%)"
    )

    text.append(
        f"Target 1: "
        f"{format_price(tp1)} "
        f"({level_percent(side, entry, tp1):+.2f}%) "
        f"→ {TP1_CLOSE_PERCENT}%"
    )

    text.append(
        f"Target 2: "
        f"{format_price(tp2)} "
        f"({level_percent(side, entry, tp2):+.2f}%) "
        f"→ {TP2_CLOSE_PERCENT}%"
    )

    text.append(
        f"Target 3: "
        f"{format_price(tp3)} "
        f"({level_percent(side, entry, tp3):+.2f}%) "
        f"→ {TP3_CLOSE_PERCENT}%"
    )

    text.append(
        f"Risk: "
        f"{abs(level_percent(side, entry, sl)):.2f}%"
    )

    text.append(
        f"RR: "
        f"1:{TP1_R_MULTIPLE:.1f} / "
        f"1:{TP2_R_MULTIPLE:.1f} / "
        f"1:{TP3_R_MULTIPLE:.1f}"
    )

    text.append(
        f"15M Trend: "
        f"{signal.get('trend_15m', 'N/A')}"
    )

    text.append(
        f"1H Trend: "
        f"{signal.get('trend_1h', 'N/A')}"
    )

    text.append(
        f"Volume: "
        f"{signal.get('volume_ratio', 0):.2f}x"
    )

    text.append(
        "TP Management: "
        "TP1 → SL Entry | "
        "TP2 → SL TP1"
    )

    text.append(
        f"Reason: {signal['reason']}"
    )

    return "\n".join(text)


# ============================================================
# CANDIDATE FORMAT
# ============================================================

def format_candidate(
    symbol,
    candidate,
    rank=None
):

    side = candidate["side"]

    icon = (
        "🟢"
        if side == "BUY"
        else "🔴"
    )

    score = candidate["score"]

    label = candidate["label"]

    components = candidate[
        "components"
    ]

    lines = []

    rank_text = (
        f"#{rank} "
        if rank is not None
        else ""
    )

    lines.append(
        f"{rank_text}{icon} "
        f"*{symbol}/USDT {side}* "
        f"⭐ *{score}/100* {label}"
    )

    lines.append(
        f"RSI Divergence "
        f"+{components['divergence']}"
    )

    lines.append(
        f"UT Bot "
        f"+{components['ut']}"
    )

    lines.append(
        f"Trendline "
        f"+{components['trendline']}"
    )

    lines.append(
        f"15M Trend "
        f"+{components['trend_15m']}"
    )

    lines.append(
        f"1H Trend "
        f"+{components['trend_1h']}"
    )

    lines.append(
        f"Volume "
        f"+{components['volume']} "
        f"({candidate['volume_ratio']:.2f}x)"
    )

    lines.append(
        f"Entry: "
        f"{format_price(candidate.get('entry'))}"
    )

    if candidate.get("sl") is not None:

        lines.append(
            f"SL: "
            f"{format_price(candidate.get('sl'))}"
        )

    if candidate.get("tp1") is not None:

        lines.append(
            f"TP1: "
            f"{format_price(candidate.get('tp1'))}"
        )

    if candidate.get("tp2") is not None:

        lines.append(
            f"TP2: "
            f"{format_price(candidate.get('tp2'))}"
        )

    if candidate.get("tp3") is not None:

        lines.append(
            f"TP3: "
            f"{format_price(candidate.get('tp3'))}"
        )

    if candidate["final_ready"]:

        lines.append(
            "STATUS: 🚨 READY"
        )

    elif candidate["rejection"] == "SCORE":

        lines.append(
            "STATUS: 🟡 BELOW SIGNAL THRESHOLD"
        )

    elif candidate["rejection"] == "SL":

        lines.append(
            "STATUS: ❌ INVALID SL"
        )

    elif candidate["rejection"] == "TREND_FILTER":

        lines.append(
            "STATUS: ❌ TREND FILTER"
        )

    else:

        lines.append(
            "STATUS: 🟡 WATCH"
        )

    return "\n".join(lines)


# ============================================================
# CLOSED SIGNAL FORMAT
# ============================================================

def format_closed_signal(
    trade
):

    side = normalize_side(
        trade
    )

    coin = normalize_coin(
        trade
    )

    icon = (
        "🟢"
        if side == "BUY"
        else "🔴"
    )

    reason = (
        trade.get(
            "result_reason"
        )
        or trade.get(
            "exit_reason"
        )
        or "UNKNOWN"
    )

    reason_upper = str(
        reason
    ).upper()

    result_icon = (

        "✅"
        if reason_upper.startswith("TP")

        else "❌"
        if reason_upper == "SL"

        else "⚪"

    )

    entry = get_trade_entry(
        trade
    )

    exit_price = trade.get(
        "result_price",
        trade.get("exit_price")
    )

    try:

        exit_price = float(
            exit_price
        )

    except Exception:

        exit_price = None

    try:

        pnl = float(
            trade.get(
                "pnl_percent",
                0
            )
        )

    except Exception:

        pnl = 0

    try:

        r_multiple = float(
            trade.get(
                "result_r",
                trade.get(
                    "r_multiple",
                    0
                )
            )
        )

    except Exception:

        r_multiple = 0

    duration = trade.get(
        "duration_minutes",
        0
    )

    score = trade.get(
        "score"
    )

    score_text = ""

    if score is not None:

        try:

            score_text = (
                f" | ⭐ "
                f"{float(score):.0f}/100"
            )

        except Exception:

            pass

    return "\n".join([

        f"{icon} {coin} {side} | "
        f"{result_icon} {reason}"
        f"{score_text}",

        f"Entry: "
        f"{format_price(entry)}",

        f"Exit: "
        f"{format_price(exit_price)}",

        f"P&L: "
        f"{pnl:+.2f}%",

        f"R: "
        f"{r_multiple:+.2f}R",

        f"Duration: "
        f"{float(duration):.0f} min",

    ])


# ============================================================
# OPEN PERFORMANCE
# ============================================================

def calculate_open_performance(
    state,
    data_cache
):

    result = []

    for trade in get_all_trades(
        state
    ):

        if str(
            trade.get(
                "status",
                ""
            )
        ).upper() != "OPEN":

            continue

        initialize_trade_tp_state(
            trade
        )

        coin = normalize_coin(
            trade
        )

        df = data_cache.get(
            coin
        )

        if df is None or df.empty:

            continue

        entry = get_trade_entry(
            trade
        )

        sl = get_trade_sl(
            trade
        )

        side = normalize_side(
            trade
        )

        if (
            entry is None
            or sl is None
            or side not in (
                "BUY",
                "SELL"
            )
        ):

            continue

        current_price = float(
            df["close"].iloc[-1]
        )

        if side == "BUY":

            pnl = (
                current_price
                - entry
            ) / entry * 100

        else:

            pnl = (
                entry
                - current_price
            ) / entry * 100

        risk = abs(
            entry - sl
        )

        if risk > 0:

            if side == "BUY":

                current_r = (
                    current_price
                    - entry
                ) / risk

            else:

                current_r = (
                    entry
                    - current_price
                ) / risk

        else:

            current_r = 0

        signal_time = (
            parse_trade_time(
                trade.get(
                    "signal_time"
                )
            )
        )

        if signal_time is None:

            signal_time = (
                parse_trade_time(
                    trade.get(
                        "signal_time_iso"
                    )
                )
            )

        if signal_time is None:

            mfe = 0

            mae = 0

            duration = 0

        else:

            after_signal = df[
                df["time"]
                > signal_time
            ]

            if after_signal.empty:

                mfe = 0

                mae = 0

            elif side == "BUY":

                best = float(
                    after_signal[
                        "high"
                    ].max()
                )

                worst = float(
                    after_signal[
                        "low"
                    ].min()
                )

                mfe = (
                    best - entry
                ) / entry * 100

                mae = (
                    worst - entry
                ) / entry * 100

            else:

                best = float(
                    after_signal[
                        "low"
                    ].min()
                )

                worst = float(
                    after_signal[
                        "high"
                    ].max()
                )

                mfe = (
                    entry - best
                ) / entry * 100

                mae = (
                    entry - worst
                ) / entry * 100

            last_time = int(
                df["time"].iloc[-1]
            )

            duration = max(
                0,
                (
                    last_time
                    - signal_time
                ) / 60000
            )

        result.append({

            "symbol":
                coin,

            "side":
                side,

            "entry":
                entry,

            "sl":
                sl,

            "tp1":
                get_trade_tp1(trade),

            "tp2":
                get_trade_tp2(trade),

            "tp3":
                get_trade_tp3(trade),

            "current_price":
                current_price,

            "pnl_percent":
                pnl,

            "current_r":
                current_r,

            "mfe":
                mfe,

            "mae":
                mae,

            "duration_minutes":
                duration,

            "score":
                trade.get("score"),

            "tp1_hit":
                get_tp1_hit(trade),

            "tp2_hit":
                get_tp2_hit(trade),

            "tp3_hit":
                get_tp3_hit(trade),

            "remaining_percent":
                get_remaining_percent(
                    trade
                ),

        })

    return result


# ============================================================
# PARTIAL TP / TRADE EVALUATION
# ============================================================

def evaluate_open_trades(
    state,
    data_cache
):

    changed = False

    for trade in get_all_trades(
        state
    ):

        if str(
            trade.get(
                "status",
                ""
            )
        ).upper() != "OPEN":

            continue

        initialize_trade_tp_state(
            trade
        )

        coin = normalize_coin(
            trade
        )

        df = data_cache.get(
            coin
        )

        if df is None or df.empty:

            continue

        side = normalize_side(
            trade
        )

        entry = get_trade_entry(
            trade
        )

        original_sl = (
            trade.get(
                "original_sl",
                trade.get("sl")
            )
        )

        try:

            original_sl = float(
                original_sl
            )

        except Exception:

            continue

        if (
            entry is None
            or side not in (
                "BUY",
                "SELL"
            )
        ):

            continue

        if "original_sl" not in trade:

            trade["original_sl"] = (
                original_sl
            )

        tp1 = get_trade_tp1(
            trade
        )

        tp2 = get_trade_tp2(
            trade
        )

        tp3 = get_trade_tp3(
            trade
        )

        signal_time = (
            parse_trade_time(
                trade.get(
                    "signal_time"
                )
            )
        )

        if signal_time is None:

            signal_time = (
                parse_trade_time(
                    trade.get(
                        "signal_time_iso"
                    )
                )
            )

        if signal_time is None:

            continue

        after_signal = df[
            df["time"]
            > signal_time
        ]

        if after_signal.empty:

            continue

        risk = abs(
            entry
            - original_sl
        )

        if risk <= 0:

            continue

        for _, row in after_signal.iterrows():

            high = float(
                row["high"]
            )

            low = float(
                row["low"]
            )

            candle_time = int(
                row["time"]
            )

            # =================================================
            # CURRENT SL
            # =================================================

            current_sl = get_trade_sl(
                trade
            )

            if current_sl is None:

                current_sl = original_sl

            # =================================================
            # SL CHECK
            # =================================================

            sl_hit = False

            if side == "BUY":

                if low <= current_sl:

                    sl_hit = True

            else:

                if high >= current_sl:

                    sl_hit = True

            if sl_hit:

                remaining = (
                    get_remaining_percent(
                        trade
                    )
                )

                if remaining <= 0:

                    trade["status"] = (
                        "CLOSED"
                    )

                    break

                if side == "BUY":

                    pnl_remaining = (
                        current_sl
                        - entry
                    ) / entry * 100

                    r_remaining = (
                        current_sl
                        - entry
                    ) / risk

                else:

                    pnl_remaining = (
                        entry
                        - current_sl
                    ) / entry * 100

                    r_remaining = (
                        entry
                        - current_sl
                    ) / risk

                weighted_pnl = (
                    pnl_remaining
                    * remaining
                    / 100
                )

                weighted_r = (
                    r_remaining
                    * remaining
                    / 100
                )

                trade[
                    "realized_pnl_percent"
                ] = (
                    float(
                        trade.get(
                            "realized_pnl_percent",
                            0
                        )
                    )
                    + weighted_pnl
                )

                trade[
                    "realized_r"
                ] = (
                    float(
                        trade.get(
                            "realized_r",
                            0
                        )
                    )
                    + weighted_r
                )

                trade[
                    "remaining_percent"
                ] = 0

                trade[
                    "status"
                ] = "CLOSED"

                trade[
                    "exit_reason"
                ] = "SL"

                trade[
                    "result_reason"
                ] = "SL"

                trade[
                    "exit_price"
                ] = current_sl

                trade[
                    "result_price"
                ] = current_sl

                trade[
                    "exit_time"
                ] = candle_time

                trade[
                    "pnl_percent"
                ] = float(
                    trade.get(
                        "realized_pnl_percent",
                        0
                    )
                )

                trade[
                    "r_multiple"
                ] = float(
                    trade.get(
                        "realized_r",
                        0
                    )
                )

                trade[
                    "result_r"
                ] = float(
                    trade.get(
                        "realized_r",
                        0
                    )
                )

                trade[
                    "duration_minutes"
                ] = max(
                    0,
                    (
                        candle_time
                        - signal_time
                    ) / 60000
                )

                trade[
                    "closed_at"
                ] = datetime.fromtimestamp(
                    candle_time / 1000,
                    tz=timezone.utc
                ).isoformat()

                changed = True

                break

            # =================================================
            # TP1
            # =================================================

            if (
                not get_tp1_hit(trade)
                and tp1 is not None
            ):

                tp1_hit = (

                    high >= tp1

                    if side == "BUY"

                    else low <= tp1

                )

                if tp1_hit:

                    realized = (
                        TP1_CLOSE_PERCENT
                        / 100
                    )

                    if side == "BUY":

                        pnl_part = (
                            tp1 - entry
                        ) / entry * 100

                        r_part = (
                            tp1 - entry
                        ) / risk

                    else:

                        pnl_part = (
                            entry - tp1
                        ) / entry * 100

                        r_part = (
                            entry - tp1
                        ) / risk

                    trade[
                        "realized_pnl_percent"
                    ] += (
                        pnl_part
                        * realized
                    )

                    trade[
                        "realized_r"
                    ] += (
                        r_part
                        * realized
                    )

                    trade[
                        "remaining_percent"
                    ] -= (
                        TP1_CLOSE_PERCENT
                    )

                    trade[
                        "tp1_hit"
                    ] = True

                    trade[
                        "tp1_time"
                    ] = candle_time

                    if (
                        MOVE_SL_TO_ENTRY_AFTER_TP1
                    ):

                        trade[
                            "sl"
                        ] = entry

                        trade[
                            "sl_moved_to_entry"
                        ] = True

                    changed = True

            # =================================================
            # TP2
            # =================================================

            if (
                get_tp1_hit(trade)
                and not get_tp2_hit(trade)
                and tp2 is not None
            ):

                tp2_hit = (

                    high >= tp2

                    if side == "BUY"

                    else low <= tp2

                )

                if tp2_hit:

                    realized = (
                        TP2_CLOSE_PERCENT
                        / 100
                    )

                    if side == "BUY":

                        pnl_part = (
                            tp2 - entry
                        ) / entry * 100

                        r_part = (
                            tp2 - entry
                        ) / risk

                    else:

                        pnl_part = (
                            entry - tp2
                        ) / entry * 100

                        r_part = (
                            entry - tp2
                        ) / risk

                    trade[
                        "realized_pnl_percent"
                    ] += (
                        pnl_part
                        * realized
                    )

                    trade[
                        "realized_r"
                    ] += (
                        r_part
                        * realized
                    )

                    trade[
                        "remaining_percent"
                    ] -= (
                        TP2_CLOSE_PERCENT
                    )

                    trade[
                        "tp2_hit"
                    ] = True

                    trade[
                        "tp2_time"
                    ] = candle_time

                    if (
                        MOVE_SL_TO_TP1_AFTER_TP2
                    ):

                        trade[
                            "sl"
                        ] = tp1

                        trade[
                            "sl_moved_to_tp1"
                        ] = True

                    changed = True

            # =================================================
            # TP3
            # =================================================

            if (
                get_tp2_hit(trade)
                and not get_tp3_hit(trade)
                and tp3 is not None
            ):

                tp3_hit = (

                    high >= tp3

                    if side == "BUY"

                    else low <= tp3

                )

                if tp3_hit:

                    realized = (
                        get_remaining_percent(
                            trade
                        ) / 100
                    )

                    if side == "BUY":

                        pnl_part = (
                            tp3 - entry
                        ) / entry * 100

                        r_part = (
                            tp3 - entry
                        ) / risk

                    else:

                        pnl_part = (
                            entry - tp3
                        ) / entry * 100

                        r_part = (
                            entry - tp3
                        ) / risk

                    trade[
                        "realized_pnl_percent"
                    ] += (
                        pnl_part
                        * realized
                    )

                    trade[
                        "realized_r"
                    ] += (
                        r_part
                        * realized
                    )

                    trade[
                        "remaining_percent"
                    ] = 0

                    trade[
                        "tp3_hit"
                    ] = True

                    trade[
                        "tp3_time"
                    ] = candle_time

                    trade[
                        "status"
                    ] = "CLOSED"

                    trade[
                        "exit_reason"
                    ] = "TP3"

                    trade[
                        "result_reason"
                    ] = "TP3"

                    trade[
                        "exit_price"
                    ] = tp3

                    trade[
                        "result_price"
                    ] = tp3

                    trade[
                        "exit_time"
                    ] = candle_time

                    trade[
                        "pnl_percent"
                    ] = float(
                        trade[
                            "realized_pnl_percent"
                        ]
                    )

                    trade[
                        "r_multiple"
                    ] = float(
                        trade[
                            "realized_r"
                        ]
                    )

                    trade[
                        "result_r"
                    ] = float(
                        trade[
                            "realized_r"
                        ]
                    )

                    trade[
                        "duration_minutes"
                    ] = max(
                        0,
                        (
                            candle_time
                            - signal_time
                        ) / 60000
                    )

                    trade[
                        "closed_at"
                    ] = datetime.fromtimestamp(
                        candle_time / 1000,
                        tz=timezone.utc
                    ).isoformat()

                    changed = True

                    break

    return changed


# ============================================================
# NEWLY CLOSED TRADES
# ============================================================

def get_newly_closed_trades(
    state,
    previous_closed_ids
):

    result = []

    for trade in get_all_trades(
        state
    ):

        if str(
            trade.get(
                "status",
                ""
            )
        ).upper() != "CLOSED":

            continue

        trade_id = get_trade_id(
            trade
        )

        if (
            trade_id
            and trade_id
            not in previous_closed_ids
        ):

            result.append(
                trade
            )

    return result


# ============================================================
# STATISTICS
# ============================================================

def calculate_statistics(
    state
):

    trades = get_all_trades(
        state
    )

    open_trades = [

        x for x in trades

        if str(
            x.get(
                "status",
                ""
            )
        ).upper()
        == "OPEN"

    ]

    closed_trades = [

        x for x in trades

        if str(
            x.get(
                "status",
                ""
            )
        ).upper()
        == "CLOSED"

    ]

    wins = []

    losses = []

    for trade in closed_trades:

        try:

            pnl = float(
                trade.get(
                    "pnl_percent",
                    0
                )
            )

        except Exception:

            pnl = 0

        if pnl > 0:

            wins.append(
                trade
            )

        else:

            losses.append(
                trade
            )

    total_pnl = sum(

        float(
            x.get(
                "pnl_percent",
                0
            )
        )

        for x in closed_trades

    )

    win_rate = (

        len(wins)
        / len(closed_trades)
        * 100

        if closed_trades

        else 0

    )

    avg_win = (

        float(
            np.mean([

                float(
                    x.get(
                        "pnl_percent",
                        0
                    )
                )

                for x in wins

            ])
        )

        if wins

        else 0

    )

    avg_loss = (

        float(
            np.mean([

                float(
                    x.get(
                        "pnl_percent",
                        0
                    )
                )

                for x in losses

            ])
        )

        if losses

        else 0

    )

    return {

        "total":
            len(trades),

        "open":
            len(open_trades),

        "closed":
            len(closed_trades),

        "wins":
            len(wins),

        "losses":
            len(losses),

        "win_rate":
            win_rate,

        "total_pnl":
            total_pnl,

        "avg_win":
            avg_win,

        "avg_loss":
            avg_loss,

    }


# ============================================================
# DIAGNOSTICS
# ============================================================

def calculate_diagnostics(
    results
):

    stats = {

        "bullish_divergence":
            0,

        "bearish_divergence":
            0,

        "bullish_break":
            0,

        "bearish_break":
            0,

        "ut_buy":
            0,

        "ut_sell":
            0,

        "buy_candidates":
            0,

        "sell_candidates":
            0,

        "setup_candidates":
            0,

        "visible_candidates":
            0,

        "ready_candidates":
            0,

        "buy_trend_ready":
            0,

        "sell_trend_ready":
            0,

    }

    for result in results:

        diagnostic = result.get(
            "diagnostic",
            {}
        )

        if diagnostic.get(
            "bullish_divergence"
        ):

            stats[
                "bullish_divergence"
            ] += 1

        if diagnostic.get(
            "bearish_divergence"
        ):

            stats[
                "bearish_divergence"
            ] += 1

        if diagnostic.get(
            "bullish_break"
        ):

            stats[
                "bullish_break"
            ] += 1

        if diagnostic.get(
            "bearish_break"
        ):

            stats[
                "bearish_break"
            ] += 1

        if diagnostic.get(
            "ut_buy"
        ):

            stats[
                "ut_buy"
            ] += 1

        if diagnostic.get(
            "ut_sell"
        ):

            stats[
                "ut_sell"
            ] += 1

        candidate_sides = (
            diagnostic.get(
                "candidate_sides",
                []
            )
        )

        if "BUY" in candidate_sides:

            stats[
                "buy_candidates"
            ] += 1

        if "SELL" in candidate_sides:

            stats[
                "sell_candidates"
            ] += 1

        stats[
            "setup_candidates"
        ] += len(
            candidate_sides
        )

        for candidate in diagnostic.get(
            "candidate_details",
            []
        ):

            score = candidate.get(
                "score",
                0
            )

            if (
                score
                >= MIN_DISPLAY_CANDIDATE_SCORE
            ):

                stats[
                    "visible_candidates"
                ] += 1

            if candidate.get(
                "final_ready",
                False
            ):

                stats[
                    "ready_candidates"
                ] += 1

        if diagnostic.get(
            "buy_trend_ok"
        ):

            stats[
                "buy_trend_ready"
            ] += 1

        if diagnostic.get(
            "sell_trend_ok"
        ):

            stats[
                "sell_trend_ready"
            ] += 1

    return stats
    # ============================================================
# REPORT
# ============================================================

def format_report(
    state,
    results,
    errors,
    open_performance,
    closed_this_run,
    blocked_symbols=None,
    registered_signals=None
):

    if blocked_symbols is None:
        blocked_symbols = []

    if registered_signals is None:
        registered_signals = []

    stats = calculate_statistics(
        state
    )

    diagnostic = calculate_diagnostics(
        results
    )

    visible_candidates = (
        get_visible_candidates(
            results
        )
    )

    lines = []

    lines.append(
        "📡 CRYPTO DIVERGENCE "
        "SCANNER v10.8 SCORE"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"🕐 "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} "
        f"UTC"
    )

    lines.append(
        f"⏱ Timeframe: "
        f"{TIMEFRAME.upper()} CLOSED"
    )

    lines.append(
        f"🤖 UT Bot: "
        f"Key {UT_KEY_VALUE:g} / "
        f"ATR {UT_ATR_PERIOD}"
    )

    lines.append(
        f"🎯 Candidate: "
        f"{MIN_DISPLAY_CANDIDATE_SCORE}+ / 100"
    )

    lines.append(
        f"🚨 Final Signal: "
        f"{MIN_SIGNAL_SCORE}+ / 100"
    )

    lines.append(
        f"🏆 GLOBAL FINAL: "
        f"ONLY ONE"
    )

    lines.append(
        f"📊 DATA OK: "
        f"{len(results)}/{len(COINS)}"
    )

    lines.append(
        f"⚠️ DATA ERROR: "
        f"{len(errors)}"
    )

    # ========================================================
    # PERFORMANCE
    # ========================================================

    lines.append("")

    lines.append(
        "📊 CUMULATIVE PERFORMANCE"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"Total Trades: "
        f"{stats['total']}"
    )

    lines.append(
        f"Open: "
        f"{stats['open']}"
    )

    lines.append(
        f"Closed: "
        f"{stats['closed']}"
    )

    lines.append(
        f"Wins: "
        f"{stats['wins']}"
    )

    lines.append(
        f"Losses: "
        f"{stats['losses']}"
    )

    lines.append(
        f"Win Rate: "
        f"{stats['win_rate']:.2f}%"
    )

    lines.append(
        f"Closed P&L: "
        f"{stats['total_pnl']:.2f}%"
    )

    lines.append(
        f"Avg Win: "
        f"{stats['avg_win']:.2f}%"
    )

    lines.append(
        f"Avg Loss: "
        f"{stats['avg_loss']:.2f}%"
    )

    # ========================================================
    # DIAGNOSTIC
    # ========================================================

    lines.append("")

    lines.append(
        "📊 SIGNAL DIAGNOSTIC"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"RSI Bullish Divergence: "
        f"{diagnostic['bullish_divergence']}"
    )

    lines.append(
        f"RSI Bearish Divergence: "
        f"{diagnostic['bearish_divergence']}"
    )

    lines.append(
        f"Trendline Breakout: "
        f"{diagnostic['bullish_break']}"
    )

    lines.append(
        f"Trendline Breakdown: "
        f"{diagnostic['bearish_break']}"
    )

    lines.append(
        f"UT Bot BUY CROSS: "
        f"{diagnostic['ut_buy']}"
    )

    lines.append(
        f"UT Bot SELL CROSS: "
        f"{diagnostic['ut_sell']}"
    )

    lines.append(
        f"SETUP CANDIDATES: "
        f"{diagnostic['setup_candidates']}"
    )

    lines.append(
        f"DISPLAYED 65+: "
        f"{diagnostic['visible_candidates']}"
    )

    lines.append(
        f"READY 75+: "
        f"{diagnostic['ready_candidates']}"
    )

    lines.append(
        f"GLOBAL FINAL SIGNALS: "
        f"{len(registered_signals)}"
    )

    # ========================================================
    # SCORE
    # ========================================================

    lines.append("")

    lines.append(
        "🎯 SCORE SYSTEM"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "RSI Divergence +30"
    )

    lines.append(
        "UT Bot Trigger +20"
    )

    lines.append(
        "Trendline +15"
    )

    lines.append(
        "15M Trend +15"
    )

    lines.append(
        "1H Trend +15"
    )

    lines.append(
        "Volume +5"
    )

    lines.append(
        "🔥 85-100 STRONG"
    )

    lines.append(
        "🟢 75-84 GOOD"
    )

    lines.append(
        "🟡 65-74 WATCH"
    )

    # ========================================================
    # CANDIDATES
    # ========================================================

    lines.append("")

    lines.append(
        "🎯 GLOBAL SIGNAL CANDIDATES"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    if visible_candidates:

        for rank, candidate in enumerate(
            visible_candidates,
            start=1
        ):

            lines.append(
                format_candidate(
                    candidate["symbol"],
                    candidate,
                    rank
                )
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

    else:

        lines.append(
            "فعلاً کاندیدای "
            f"{MIN_DISPLAY_CANDIDATE_SCORE}+ "
            "وجود ندارد."
        )

    # ========================================================
    # BLOCKED SYMBOLS
    # ========================================================

    if blocked_symbols:

        lines.append("")

        lines.append(
            "🔒 SYMBOL LOCK"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        unique_blocked = sorted(
            set(blocked_symbols)
        )

        lines.append(
            f"{len(unique_blocked)} "
            "symbol blocked بسبب "
            "OPEN trade"
        )

        lines.append(
            ", ".join(
                unique_blocked
            )
        )

    # ========================================================
    # CLOSED SIGNALS
    # ========================================================

    lines.append("")

    lines.append(
        "🏁 CLOSED SIGNALS"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    if closed_this_run:

        for trade in closed_this_run:

            lines.append(
                format_closed_signal(
                    trade
                )
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

    else:

        lines.append(
            "در این اجرا معامله‌ای "
            "بسته نشده است."
        )

    # ========================================================
    # FINAL SIGNAL
    # ========================================================

    lines.append("")

    lines.append(
        "🚨 FINAL GLOBAL SIGNAL"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    if registered_signals:

        for signal in registered_signals:

            lines.append(
                format_signal(
                    signal
                )
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

    else:

        lines.append(
            "فعلاً سیگنال نهایی "
            "وجود ندارد."
        )

    # ========================================================
    # OPEN PERFORMANCE
    # ========================================================

    lines.append("")

    lines.append(
        "📌 OPEN SIGNAL P&L"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    if open_performance:

        for item in open_performance:

            icon = (
                "🟢"
                if item["side"] == "BUY"
                else "🔴"
            )

            side = item["side"]

            entry = item["entry"]

            sl = item["sl"]

            pnl = item["pnl_percent"]

            score = item.get(
                "score"
            )

            score_text = ""

            if score is not None:

                try:

                    score_text = (
                        f" ⭐"
                        f"{float(score):.0f}/100"
                    )

                except Exception:

                    pass

            lines.append(
                f"{icon} "
                f"{item['symbol']} "
                f"{side}"
                f"{score_text}"
            )

            lines.append(
                f"Entry: "
                f"{format_price(entry)}"
            )

            lines.append(
                f"Current: "
                f"{format_price(item['current_price'])}"
            )

            lines.append(
                f"SL: "
                f"{format_price(sl)} "
                f"({level_percent(side, entry, sl):+.2f}%)"
            )

            if item.get("tp1") is not None:

                tp1_status = (
                    "✅ HIT"
                    if item["tp1_hit"]
                    else "⏳ WAIT"
                )

                lines.append(
                    f"TP1: "
                    f"{format_price(item['tp1'])} "
                    f"{tp1_status} "
                    f"→ {TP1_CLOSE_PERCENT}%"
                )

            if item.get("tp2") is not None:

                tp2_status = (
                    "✅ HIT"
                    if item["tp2_hit"]
                    else "⏳ WAIT"
                )

                lines.append(
                    f"TP2: "
                    f"{format_price(item['tp2'])} "
                    f"{tp2_status} "
                    f"→ {TP2_CLOSE_PERCENT}%"
                )

            if item.get("tp3") is not None:

                tp3_status = (
                    "✅ HIT"
                    if item["tp3_hit"]
                    else "⏳ WAIT"
                )

                lines.append(
                    f"TP3: "
                    f"{format_price(item['tp3'])} "
                    f"{tp3_status} "
                    f"→ {TP3_CLOSE_PERCENT}%"
                )

            lines.append(
                f"Remaining: "
                f"{item['remaining_percent']:.0f}%"
            )

            lines.append(
                f"Current P&L: "
                f"*{pnl:+.2f}%*"
            )

            lines.append(
                f"Current R: "
                f"{item['current_r']:+.2f}R"
            )

            lines.append(
                f"MFE: "
                f"{item['mfe']:+.2f}%"
            )

            lines.append(
                f"MAE: "
                f"{item['mae']:+.2f}%"
            )

            lines.append(
                f"Duration: "
                f"{item['duration_minutes']:.0f} min"
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

    else:

        lines.append(
            "هیچ سیگنال بازی وجود ندارد."
        )

    # ========================================================
    # ERRORS
    # ========================================================

    if errors:

        lines.append("")

        lines.append(
            "⚠️ ERRORS"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        for symbol, error in errors.items():

            lines.append(
                f"{symbol}: {error}"
            )

    # ========================================================
    # SUMMARY
    # ========================================================

    lines.append("")

    lines.append(
        "📋 SCAN SUMMARY"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"Symbols Scanned: "
        f"{len(COINS)}"
    )

    lines.append(
        f"Data OK: "
        f"{len(results)}"
    )

    lines.append(
        f"Data Errors: "
        f"{len(errors)}"
    )

    lines.append(
        f"Setup Candidates: "
        f"{diagnostic['setup_candidates']}"
    )

    lines.append(
        f"Displayed 65+: "
        f"{diagnostic['visible_candidates']}"
    )

    lines.append(
        f"Ready 75+: "
        f"{diagnostic['ready_candidates']}"
    )

    lines.append(
        f"GLOBAL FINAL: "
        f"{len(registered_signals)}"
    )

    lines.append(
        f"Open Trades: "
        f"{stats['open']}"
    )

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "Loading Kraken Futures instruments..."
    )

    try:

        market_map = load_market_map()

        print(
            f"Loaded "
            f"{len(market_map)} markets."
        )

    except Exception as e:

        print(
            f"FATAL: {e}"
        )

        return

    state = load_state()

    rebuild_trade_container(
        state
    )

    previous_closed_ids = set()

    for trade in get_all_trades(
        state
    ):

        if str(
            trade.get(
                "status",
                ""
            )
        ).upper() == "CLOSED":

            trade_id = get_trade_id(
                trade
            )

            if trade_id:

                previous_closed_ids.add(
                    trade_id
                )

    # ========================================================
    # SCAN
    # ========================================================

    results = []

    errors = {}

    data_cache = {}

    blocked_symbols = []

    for symbol in COINS:

        try:

            result = analyze_coin(
                symbol
            )

            results.append(
                result
            )

            data_cache[
                symbol
            ] = result["df"]

        except Exception as e:

            errors[
                symbol
            ] = str(e)

        time.sleep(
            0.05
        )

    # ========================================================
    # MANAGE EXISTING TRADES
    # ========================================================

    changed = evaluate_open_trades(
        state,
        data_cache
    )

    if changed:

        save_state(
            state
        )

    # ========================================================
    # GLOBAL CANDIDATES
    # ========================================================

    visible_candidates = (
        get_visible_candidates(
            results
        )
    )

    # ========================================================
    # ONLY ONE GLOBAL SIGNAL
    # ========================================================

    global_signal = (
        get_global_final_signal(
            results,
            state
        )
    )

    new_registered = []

    if global_signal is not None:

        symbol = (
            global_signal["symbol"]
        )

        side = (
            global_signal["side"]
        )

        # ----------------------------------------------------
        # SAME SYMBOL LOCK
        # ----------------------------------------------------

        if (
            not ALLOW_MULTIPLE_OPEN_PER_SYMBOL
            and has_open_trade_for_symbol(
                state,
                symbol
            )
        ):

            blocked_symbols.append(
                symbol
            )

        else:

            signal_id = (
                make_signal_id(
                    symbol,
                    side,
                    global_signal[
                        "signal_time"
                    ],
                    global_signal[
                        "entry"
                    ]
                )
            )

            global_signal[
                "signal_id"
            ] = signal_id

            global_signal[
                "id"
            ] = signal_id

            # ------------------------------------------------
            # REGISTER ONLY ONE
            # ------------------------------------------------

            if register_signal(
                state,
                global_signal
            ):

                new_registered.append(
                    global_signal
                )

    # ========================================================
    # SAVE
    # ========================================================

    save_state(
        state
    )

    # ========================================================
    # EVALUATE NEWLY REGISTERED SIGNAL
    # ========================================================

    changed = evaluate_open_trades(
        state,
        data_cache
    )

    if changed:

        save_state(
            state
        )

    # ========================================================
    # CLOSED THIS RUN
    # ========================================================

    closed_this_run = (
        get_newly_closed_trades(
            state,
            previous_closed_ids
        )
    )

    # ========================================================
    # OPEN PERFORMANCE
    # ========================================================

    open_performance = (
        calculate_open_performance(
            state,
            data_cache
        )
    )

    # ========================================================
    # REPORT
    # ========================================================

    report = format_report(
        state,
        results,
        errors,
        open_performance,
        closed_this_run,
        blocked_symbols,
        new_registered
    )

    print()

    print(
        report
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    send_telegram(
        report
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
