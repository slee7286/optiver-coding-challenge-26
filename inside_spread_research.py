"""Offline two-sided spread capture; no network, account, or live orders.
Post one tick inside only when spread >=4 ticks. After entry, try passive exit;
otherwise cross after 5s. Print-through is a fill proxy, not queue reconstruction.
"""

import argparse
import bisect
import csv
import gzip
import json
import math
from collections import defaultdict
from pathlib import Path

NS = 10**9


def sources(root):
    by = defaultdict(list)
    tr = defaultdict(list)
    for feed, dst in [("pricebooks", by), ("trades", tr)]:
        with gzip.open(root / (feed + ".csv.gz"), "rt", newline="") as f:
            for r in csv.DictReader(f):
                t = int(r["timestamp_ns"])
                s = r["instrument_id"]
                if feed == "trades":
                    v = (
                        r["aggressor_side"],
                        round(float(r["price"]) * 100),
                        int(r["volume"]),
                    )
                else:
                    b, a = json.loads(r["bids_json"]), json.loads(r["asks_json"])
                    v = None
                    if b and a and 0 < b[0][0] < a[0][0] and min(b[0][1], a[0][1]) >= 1:
                        v = (round(b[0][0] * 100), round(a[0][0] * 100))
                dst[s].append((t, v))
    for dst in (by, tr):
        for rows in dst.values():
            rows.sort(key=lambda e: e[0])
    return by, tr


def scan(data, start, end, latency_ms=200, cost=1):
    by, tr = data
    pnl = 0
    entries = closed = passive = unresolved = posts = 0
    ticker_pnl = {}
    lat = latency_ms * 10**6
    for s, B in sorted(by.items()):
        T = tr[s]
        ts = [t for t, _ in B]
        tt = [t for t, _ in T]
        net = 0

        def quote(t):
            j = bisect.bisect_left(ts, t) - 1
            return B[j][1] if j >= 0 and start <= ts[j] and t - ts[j] <= NS else None

        def prints(a, b):
            return T[bisect.bisect_right(tt, a) : bisect.bisect_left(tt, b)]

        t = start + NS
        while t + 10 * NS < end:
            q = quote(t)
            arr = quote(t + lat)
            if not q or not arr or q[1] - q[0] < 40:
                t += NS
                continue
            bid, ask = q[0] + 10, q[1] - 10
            if bid >= arr[1] or ask <= arr[0]:
                t += NS
                continue
            # Independent clocks per symbol; model submits a pair, not actual fills.
            posts += 2
            side = 0
            for hit, (ag, p, v) in prints(t + lat, t + lat + 2 * NS):
                if ag == "ask" and p < bid:
                    side = 1
                    entry = bid
                    break
                if ag == "bid" and p > ask:
                    side = -1
                    entry = ask
                    break
            if not side:
                t += 3 * NS
                continue
            entries += 1
            exit_t = hit + 5 * NS
            px = None
            # Opposite order remains resting. Strict penetration required for exit.
            for h, (ag, p, v) in prints(hit, exit_t):
                if (side == 1 and ag == "bid" and p > ask) or (
                    side == -1 and ag == "ask" and p < bid
                ):
                    px = ask if side == 1 else bid
                    exit_t = h
                    passive += 1
                    break
            if px is None:
                # At timeout cancel remaining passive order first, then one IOC with latency.
                late = exit_t + 2 * lat
                before = quote(exit_t + lat)
                after = quote(late)
                if (
                    not before
                    or not after
                    or (after[0] < before[0] if side == 1 else after[1] > before[1])
                ):
                    unresolved += 1
                    break
                px = after[0] if side == 1 else after[1]
                exit_t = late
            closed += 1
            net += side * (px - entry) - 2 * cost
            t = exit_t + NS
        pnl += net
        ticker_pnl[s] = net / 100
    return dict(
        posts=posts,
        entries=entries,
        closed=closed,
        passive_exits=passive,
        unresolved=unresolved,
        closed_pnl=pnl / 100,
        by_symbol=ticker_pnl,
    )


def run(path):
    d = sources(path)
    lo = min(v[0][0] for v in d[0].values())
    hi = max(v[-1][0] for v in d[0].values())
    cut = lo + (hi - lo) * 2 // 3
    dev = scan(d, lo, cut)
    test = scan(d, cut, hi)
    stress = scan(d, cut, hi, 400, 2)
    ok = all(
        r["closed"] >= 20 and not r["unresolved"] and r["closed_pnl"] > 0
        for r in (dev, test, stress)
    )
    return dict(
        development=dev,
        later=test,
        later_stress=stress,
        decision="VALIDATE ON NEW DATA" if ok else "NO NEW EXPOSURE",
        no_trade_from_flat=0,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path, nargs="?", default=Path.home() / "Downloads")
    print(json.dumps(run(p.parse_args().directory), indent=2))
