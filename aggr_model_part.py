"""Aggressive directional research; import-safe, standard library only.
Run: python -c "from aggr_model_part import replay_cli; replay_cli(r'C:/Users/slee7/Downloads')"
"""

import math as _ag_math


def target(history, tick):
    """Absolute inventory or None (keep existing inventory, not old order)."""
    if len(history) < 12 or not _ag_math.isfinite(tick) or tick <= 0:
        return None
    end = history[-1][0]
    window = []
    for row in reversed(history):
        if end - row[0] > 30:
            break
        if (
            not all(map(_ag_math.isfinite, row))
            or not (0 < row[1] < row[2])
            or min(row[3:5]) <= 0
            or min(row[5:]) < 0
        ):
            return None
        if window and not 0 < window[-1][0] - row[0] <= 3:
            break
        window.append(row)
    if len(window) < 12 or end - window[-1][0] < 15:
        return None

    def mid(r):
        return (r[1] + r[2]) / 2

    current = window[0]
    spread = current[2] - current[1]
    recent = [r for r in window if end - r[0] <= 5]
    old = [r for r in window if r[0] - window[-1][0] <= 5]
    move = sum(map(mid, recent)) / len(recent) - sum(map(mid, old)) / len(old)
    buys = sum(r[5] for r in window if end - r[0] <= 12)
    sells = sum(r[6] for r in window if end - r[0] <= 12)
    flow = (buys - sells) / (buys + sells) if buys + sells else 0.0
    # Entry pays both crossings and a fixed fee cushion; neutral band holds.
    if (
        abs(move) > max(spread + 0.02, 1.5 * tick)
        and move * flow > 0
        and abs(flow) >= 0.15
    ):
        return 20 if move > 0 else -20
    if abs(move) < 0.4 * tick and spread <= 2.01 * tick:
        return 0
    return None


def _ag_frames(books, trades, lo, hi):
    """Integer-second decisions, using only events timestamped <= decision."""
    from collections import defaultdict

    ns = 10**9
    books = [e for e in books if lo <= e[0] < hi]
    trades = [e for e in trades if lo <= e[0] < hi]
    i = j = 0
    latest = {}
    frames = []
    for sec in range((lo + ns - 1) // ns, (hi - 1) // ns + 1):
        now = sec * ns
        flow = defaultdict(lambda: [0, 0])
        while i < len(books) and books[i][0] <= now:
            t, k, q = books[i]
            latest[k] = (t, q)
            i += 1
        while j < len(trades) and trades[j][0] <= now:
            _, k, side, qty = trades[j]
            flow[k][side] += qty
            j += 1
        quotes = {
            k: (sec, *q, *flow[k])
            for k, (t, q) in latest.items()
            if q is not None and now - t <= 2 * ns
        }
        frames.append((sec, quotes))
    return frames


def _ag_replay(frames, model, fee=0.01):
    """One globally scheduled IOC at next second's touch; no stale quote fills.
    Flat-start; scheduled close begins 18 seconds before known segment end.
    Missing close liquidity leaves residuals, never a fictitious liquidation.
    """
    from collections import defaultdict, deque

    history = defaultdict(lambda: deque(maxlen=64))
    pos = defaultdict(int)
    pending = None
    peak = units = fills = misses = orders = gross_time = 0
    cash = cross = 0.0
    first = None
    last = {}
    end = frames[-1][0] if frames else 0
    sym_cash = defaultdict(float)
    for s, quotes in frames:
        if pending is not None:
            k, order = pending
            row = quotes.get(k)
            pending = None
            if row is None:
                misses += 1
            else:
                buy = order > 0
                size = min(abs(order), int(row[4 if buy else 3]))
                qty = size if buy else -size
                if size:
                    price = row[2 if buy else 1]
                    delta = -qty * price - size * fee
                    cash += delta
                    sym_cash[k] += delta
                    pos[k] += qty
                    units += size
                    fills += 1
                    cross += size * (row[2] - row[1]) / 2
                    if first is None:
                        first = s
        gross = sum(abs(x) for x in pos.values())
        peak = max(peak, gross)
        gross_time += gross
        choices = []
        for k, row in quotes.items():
            last[k] = row
            h = history[k]
            if h and s - h[-1][0] > 3:
                h.clear()
            h.append(row)
            p = pos[k]
            dest = 0 if s >= end - 18 else model(h, 0.1)
            if dest is None or dest == p or s == end:
                continue
            step = max(-5, min(5, dest - p))
            close = p * step < 0
            if not close:
                room = 80 - gross
                step = max(-room, min(room, step))
            if step:
                choices.append((not close, -abs(dest - p), k, step))
        if choices:
            _, _, k, step = min(choices)
            pending = (k, step)
            orders += 1
    residual = {k: v for k, v in pos.items() if v}
    # Diagnostic only: last observed liquidation-side mark, may be stale.
    mark = cash + sum(
        v * last[k][1 if v > 0 else 2] - abs(v) * fee for k, v in residual.items()
    )
    return dict(
        net_executable=None if residual else round(cash, 4),
        residual=residual,
        last_touch_mark=round(mark, 4),
        cash=round(cash, 4),
        fees=round(units * fee, 4),
        half_spreads_paid=round(cross, 4),
        units=units,
        fills=fills,
        orders=orders,
        missing_quote_orders=misses,
        peak_gross=peak,
        mean_gross=round(gross_time / max(1, len(frames)), 3),
        first_fill_sec=first,
        closed_symbol_net={
            k: round(v, 4) for k, v in sorted(sym_cash.items()) if k not in residual
        },
    )


def replay_cli(data_dir):
    """Print JSON report and return it. No hidden fit, calibration or I/O on import."""
    import csv
    import gzip
    import json
    from pathlib import Path

    root = Path(data_dir)
    books = []
    trades = []
    sessions = set()
    for name in ("pricebooks", "trades"):
        with gzip.open(
            root / (name + ".csv.gz"), "rt", newline="", encoding="utf-8-sig"
        ) as f:
            for row in csv.DictReader(f):
                t = int(row["timestamp_ns"])
                k = row["instrument_id"]
                sessions.add(row["exchange_started_at"])
                if name == "trades":
                    side = {"bid": 0, "ask": 1}[row["aggressor_side"]]
                    trades.append((t, k, side, int(row["volume"])))
                else:
                    bids = json.loads(row["bids_json"])
                    asks = json.loads(row["asks_json"])
                    q = None
                    if bids and asks:
                        b, bs = max(bids)
                        a, az = min(asks)
                        if 0 < b < a and min(bs, az) >= 1:
                            q = (b, a, bs, az)
                    books.append((t, k, q))
    if len(sessions) != 1 or not books:
        raise ValueError("expected one nonempty exchange session")
    books.sort(key=lambda x: x[0])
    trades.sort(key=lambda x: x[0])
    lo = books[0][0]
    hi = books[-1][0] + 1
    split = lo + (hi - lo) * 2 // 3
    report = {"books": len(books), "trades": len(trades), "split_ns": split}
    for name, a, b in [("first_two_thirds", lo, split), ("last_third", split, hi)]:
        frames = _ag_frames(books, trades, a, b)
        report[name] = {
            "seconds": len(frames),
            "model": _ag_replay(frames, target),
            "long_10_each": _ag_replay(frames, lambda h, t: 10),
            "no_trade": 0.0,
        }
    print(json.dumps(report, indent=2))
    return report
