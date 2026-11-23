"""Local candidate. Default: offline paired replay. Hosted live mode requires --live.
Uses the existing basket_hold.py and its dependencies in the same directory.
"""

import argparse
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

import basket_hold as base
from quick_strategy import load


def entry_book(q, tick):
    return q if q and 0 < q[1] - q[0] <= 2.001 * tick else None


def replay(data, start, end, threshold):
    # Approximation: flat start, same t=0.1 tick universe, 250ms IOC lag, 1s
    # quote freshness, $0.01/lot/side, terminal liquidation mark. No live fills.
    times = {s: [t for t, q in rows] for s, rows in data.items()}
    pos = {s: 0 for s in base.NAMES}
    cash = 0.0
    idx = 0
    t = start
    unresolved = False

    def quote(s, t):
        if s not in data:
            return None
        j = bisect_right(times[s], t) - 1
        return data[s][j][1] if j >= 0 and t - times[s][j] <= 10**9 else None

    def value(t):
        result = cash
        for s, v in pos.items():
            if v:
                q = quote(s, t)
                if not q or q[2] < v:
                    return None
                result += v * (q[0] - 0.01)
        return result

    while t + 3 * 10**9 < end:
        mark = value(t)
        if mark is not None and mark < -5:
            break
        choices = []
        for j in range(len(base.NAMES)):
            s = base.NAMES[(idx + j) % len(base.NAMES)]
            q = quote(s, t)
            if q and pos[s] < 10 and 0 < q[1] - q[0] <= threshold and q[3] >= 1:
                choices.append((pos[s], j, s, q))
        if not choices:
            t += 2 * 10**9
            continue
        _, _, s, decision = min(choices)
        idx = (base.NAMES.index(s) + 1) % len(base.NAMES)
        arrival = quote(s, t + 250_000_000)
        if arrival and arrival[1] <= decision[1]:
            v = min(2, 10 - pos[s], int(decision[3]), int(arrival[3]))
            pos[s] += v
            cash -= v * (arrival[1] + 0.01)
        t += 8_500_000_000
    net = value(end)
    return sum(pos.values()), None if net is None else round(net, 4)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--archive", type=Path, default=Path.home() / "Downloads/pricebooks.csv.gz"
    )
    p.add_argument("--live", action="store_true")
    args = p.parse_args()
    if args.live:
        now = datetime.now(timezone.utc)
        budget = base.seconds_left(now)
        if budget < 10:
            raise SystemExit("Outside inherited event trading window; no connection.")
        from optibook.synchronous_client import Exchange

        original = base.book
        ticks = {}

        def filtered(x, s):
            if s not in ticks:
                ticks.update({k: v.tick_size for k, v in x.get_instruments().items()})
            return entry_book(original(x, s), ticks[s])

        base.book = filtered
        base.supervise(Exchange, budget=budget, runner=base.basket)
        return
    data = load(args.archive)
    lo = min(rows[0][0] for rows in data.values())
    hi = max(rows[-1][0] for rows in data.values())
    split = lo + (hi - lo) * 2 // 3
    for label, a, b in [("development", lo, split), ("later segment", split, hi)]:
        for name, threshold in [("current", 0.3001), ("candidate", 0.2001)]:
            lots, net = replay(data, a, b, threshold)
            print(
                label,
                name,
                "lots",
                lots,
                "net liquidation mark",
                net
                if net is not None
                else "UNVERIFIED (no fresh executable exit for all lots)",
            )
    print(
        "Exploratory tape only; not independent forward evidence. No automatic promotion."
    )


if __name__ == "__main__":
    main()
