"""Strict one-unit simultaneous same-asset arbitrage audit. Offline only."""

import argparse
import csv
import gzip
import json
from collections import Counter
from decimal import Decimal as D
from pathlib import Path


def prices(bids, asks):
    b = [D(str(p)) for p, q in bids if q >= 1 and p > 0]
    a = [D(str(p)) for p, q in asks if q >= 1 and p > 0]
    return (max(b), min(a)) if b and a else None


def evaluate(bids, asks, atomic, roundtrip_cost):
    if roundtrip_cost < 0:
        raise ValueError("cost must be nonnegative")
    touch = prices(bids, asks)
    if not atomic or not touch:
        return 0, 0
    net = touch[0] - touch[1] - D(str(roundtrip_cost))
    return (1, net) if net > 0 else (0, 0)


def run(folder):
    data = []
    with gzip.open(folder / "pricebooks.csv.gz", "rt", newline="") as f:
        for r in csv.DictReader(f):
            data.append(
                (
                    int(r["timestamp_ns"]),
                    r["instrument_id"],
                    json.loads(r["bids_json"]),
                    json.loads(r["asks_json"]),
                )
            )
    data.sort(key=lambda r: r[0])
    start, end = data[0][0], data[-1][0]
    cut = start + (end - start) * 2 // 3
    out = {}
    for label, rows in [
        ("early", [r for r in data if r[0] < cut]),
        ("later", [r for r in data if r[0] >= cut]),
    ]:
        c = Counter()
        best = None
        symbols = set()
        net = D(0)
        for _, s, b, a in rows:
            symbols.add(s)
            touch = prices(b, a)
            c["snapshots"] += 1
            if touch:
                c["two_sided"] += 1
                edge = touch[0] - touch[1]
                best = edge if best is None else max(best, edge)
                c["crossed"] += int(edge > 0)
                c["locked"] += int(edge == 0)
                c["positive_even_with_free_atomic_execution"] += evaluate(
                    b, a, True, 0
                )[0]
            # No documented all-or-none two-leg order in the supplied venue API.
            qty, profit = evaluate(b, a, False, D(".02"))
            c["executed_packages"] += qty
            net += profit
        out[label] = dict(
            c,
            symbols=sorted(symbols),
            best_raw_sell_minus_buy=str(best),
            flat_net=str(net),
        )
    return dict(seconds=(end - start) / 1e9, split_ns=cut, results=out)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("folder", type=Path)
    print(json.dumps(run(p.parse_args().folder), indent=2))
