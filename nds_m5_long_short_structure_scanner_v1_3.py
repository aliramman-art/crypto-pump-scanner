
def detect_m15_structure_pattern(
    symbol: str,
    candles: list[dict[str, Any]],
    direction: str,
    timeframe_ms: int = M15_MS,
) -> Optional[dict[str, Any]]:
    """
    LONG:
      Start = lowest low of the complete structure.
      H1 = highest high from Start through L1.
      H2 = highest high of the complete structure and L1..L2.
      L2 = lowest low from H2 through L2, and above Start.

    SHORT is the exact inverse:
      Start = highest high of the complete structure.
      L1 = lowest low from Start through H1.
      L2 = lowest low of the complete structure and H1..H2.
      H2 = highest high from L2 through H2, and below Start.

    Only closed candles and confirmed pivots are used.
    """
    if len(candles) < PIVOT_LEFT + PIVOT_RIGHT + 20:
        return None

    lows, highs = find_confirmed_pivots(
        candles, PIVOT_LEFT, PIVOT_RIGHT
    )

    if direction == "LONG":
        # Start(low) -> H1(high) -> L1(low) -> H2(high) -> L2(low)
        for p4 in reversed(lows):
            l2 = candles[p4]["low"]

            for p3 in reversed([i for i in highs if i < p4]):
                h2 = candles[p3]["high"]

                for p2 in reversed([i for i in lows if i < p3]):
                    l1 = candles[p2]["low"]

                    if l2 <= l1:
                        continue

                    # H2 must be the highest high between L1 and L2.
                    if h2 != max(c["high"] for c in candles[p2:p4 + 1]):
                        continue

                    # L2 must be the lowest low from H2 through L2.
                    if l2 != min(c["low"] for c in candles[p3:p4 + 1]):
                        continue

                    for p1 in reversed([i for i in highs if i < p2]):
                        h1 = candles[p1]["high"]

                        if h2 <= h1:
                            continue

                        starts = [
                            i for i in lows
                            if i < p1 and i >= max(0, p1 - 240)
                        ]

                        for st in reversed(starts):
                            start_price = candles[st]["low"]

                            if start_price >= l1 or start_price >= l2:
                                continue

                            # Start must be the absolute lowest low.
                            if start_price != min(
                                c["low"] for c in candles[st:p4 + 1]
                            ):
                                continue

                            # H1 must be the highest high from Start through L1.
                            if h1 != max(
                                c["high"] for c in candles[st:p2 + 1]
                            ):
                                continue

                            # H2 must be the highest high of the whole structure.
                            if h2 != max(
                                c["high"] for c in candles[st:p4 + 1]
                            ):
                                continue

                            confirmation_i = p4 + PIVOT_RIGHT
                            if confirmation_i >= len(candles):
                                continue

                            pts = {
                                "start": st,
                                "p1": p1,
                                "p2": p2,
                                "p3": p3,
                                "p4": p4,
                            }

                            key = "LONG|" + symbol + "|" + "|".join(
                                str(candles[pts[k]]["time"])
                                for k in ("start", "p1", "p2", "p3", "p4")
                            )

                            return {
                                "symbol": symbol,
                                "direction": "LONG",
                                "points": pts,
                                "key": key,
                                "confirmation_time": (
                                    candles[confirmation_i]["time"]
                                    + timeframe_ms
                                ) / 1000.0,
                                "start_price": candles[st]["low"],
                                "p1_price": h1,
                                "p2_price": l1,
                                "p3_price": h2,
                                "p4_price": l2,
                                "terminal_time": candles[p4]["time"] / 1000.0,
                            }

    elif direction == "SHORT":
        # Start(high) -> L1(low) -> H1(high) -> L2(low) -> H2(high)
        for p4 in reversed(highs):
            h2 = candles[p4]["high"]

            for p3 in reversed([i for i in lows if i < p4]):
                l2 = candles[p3]["low"]

                for p2 in reversed([i for i in highs if i < p3]):
                    h1 = candles[p2]["high"]

                    if l2 >= h1:
                        continue

                    # L2 must be the lowest low between H1 and H2.
                    if l2 != min(c["low"] for c in candles[p2:p4 + 1]):
                        continue

                    # H2 must be the highest high from L2 through H2.
                    if h2 != max(c["high"] for c in candles[p3:p4 + 1]):
                        continue

                    for p1 in reversed([i for i in lows if i < p2]):
                        l1 = candles[p1]["low"]

                        starts = [
                            i for i in highs
                            if i < p1 and i >= max(0, p1 - 240)
                        ]

                        for st in reversed(starts):
                            start_price = candles[st]["high"]

                            if start_price <= h1 or start_price <= h2:
                                continue

                            # Start must be the absolute highest high.
                            if start_price != max(
                                c["high"] for c in candles[st:p4 + 1]
                            ):
                                continue

                            # L1 must be the lowest low from Start through H1.
                            if l1 != min(
                                c["low"] for c in candles[st:p2 + 1]
                            ):
                                continue

                            # L2 must be the lowest low of the whole structure.
                            if l2 != min(
                                c["low"] for c in candles[st:p4 + 1]
                            ):
                                continue

                            confirmation_i = p4 + PIVOT_RIGHT
                            if confirmation_i >= len(candles):
                                continue

                            pts = {
                                "start": st,
                                "p1": p1,
                                "p2": p2,
                                "p3": p3,
                                "p4": p4,
                            }

                            key = "SHORT|" + symbol + "|" + "|".join(
                                str(candles[pts[k]]["time"])
                                for k in ("start", "p1", "p2", "p3", "p4")
                            )

                            return {
                                "symbol": symbol,
                                "direction": "SHORT",
                                "points": pts,
                                "key": key,
                                "confirmation_time": (
                                    candles[confirmation_i]["time"]
                                    + timeframe_ms
                                ) / 1000.0,
                                "start_price": candles[st]["high"],
                                "p1_price": l1,
                                "p2_price": h1,
                                "p3_price": l2,
                                "p4_price": h2,
                                "terminal_time": candles[p4]["time"] / 1000.0,
                            }

    return None
