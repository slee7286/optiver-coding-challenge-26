"""Offline, one-share OFI taker replay. Run: python research/ofi.py [books.csv.gz]

The final 30% of elapsed recording time is held out. No strategy parameters
are estimated. Reset all history, orders and positions between partitions.
Assumes a small taker can execute at historical displayed prices, with no
market impact. Quotes are snapshots; OFI is not a count of actual trades.
"""
import argparse
import csv
import gzip
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

DEFAULT = Path('C:/Users/slee7/Downloads/pricebooks.csv.gz')
DELAY = 100_000_000  # nanoseconds
HOLD = 1_000_000_000
FEE = 0.01  # assumed currency units per share per fill, not a venue fee claim


def ofi(prev, curr):
    """Signed L1 flow between snapshots, not the standing size imbalance.

    >>> ofi((100, 9, 102, 7), (100, 9, 102, 7))
    0
    >>> ofi((100, 9, 102, 7), (101, 5, 103, 11))
    12
    >>> ofi((100, 9, 102, 7), (99, 5, 101, 11))
    -20
    >>> ofi((100, 9, 102, 7), (100, 11, 102, 4))
    5
    """
    b, v, a, w = prev
    B, V, A, W = curr
    return (B >= b)*V - (B <= b)*v - (A <= a)*W + (A >= a)*w


def read(path):
    rows = []
    with gzip.open(path, 'rt', newline='', encoding='utf-8') as stream:
        for row in csv.DictReader(stream):
            t = int(row['timestamp_ns'])
            if rows and t < rows[-1][0]:
                raise ValueError('input must be chronological')
            bids, asks = (json.loads(row[k]) for k in ('bids_json', 'asks_json'))
            q = None
            if bids and asks:
                b, v = max(bids, key=lambda x: x[0])
                a, w = min(asks, key=lambda x: x[0])
                if all(map(math.isfinite, (b, v, a, w))) and 0 < b < a and min(v, w) >= 1:
                    q = (b, v, a, w)
            rows.append((t, (row['exchange_started_at'], row['instrument_id']), q))
    if not rows or rows[-1][0] == rows[0][0]:
        raise ValueError('need at least two distinct timestamps')
    return rows


def replay(rows):
    """Model nonoverlapping round trips per instrument. Exit intent is fixed at entry.

    >>> key = ('session', 'stock'); base = (100, 1, 102, 1)
    >>> data = [(0,key,base),(1,key,(100,3,102,1)),(DELAY,key,base),
    ...         (1+DELAY,key,(101,1,103,1)),(1+DELAY+HOLD,key,(106,1,108,1))]
    >>> r = replay(data); (r['completed'], r['net'], r['spread_cost'])
    (1, 2.98, 2.0)
    """
    books = defaultdict(list)
    for t, key, q in rows:
        books[key].append((t, q))
    mid = spread = fees = 0.0
    completed = marked = signals = cancelled = invalid = 0
    by_instrument = defaultdict(float)
    for (_, symbol), events in books.items():
        prev = order = position = last = None
        for t, q in events:
            if q is None:
                invalid += 1
                cancelled += int(order is not None)
                prev = order = None
                continue
            last = q
            if position is not None and t >= position[0]:
                _, side, entry = position
                movement = side * ((q[0]+q[2]) - (entry[0]+entry[2])) / 2
                crossing = ((q[2]-q[0]) + (entry[2]-entry[0])) / 2
                mid += movement
                spread += crossing
                fees += 2*FEE
                by_instrument[symbol] += movement-crossing-2*FEE
                completed += 1
                position = None
            elif order is not None and t >= order[0]:
                position = (t+HOLD, order[1], q)
                order = None
            elif order is None and position is None and prev is not None:
                flow = ofi(prev, q)
                if abs(flow) >= (prev[1]+prev[3])/2:
                    signals += 1
                    order = (t+DELAY, 1 if flow > 0 else -1)
            prev = q
        cancelled += int(order is not None)
        if position is not None:
            # Terminal valuation, NOT an observed timed exit; include liquidation
            # spread and fee, even if the last usable quote is the entry itself.
            _, side, entry = position
            assert last is not None  # opening a position requires a usable quote
            movement = side * ((last[0]+last[2]) - (entry[0]+entry[2])) / 2
            crossing = ((last[2]-last[0]) + (entry[2]-entry[0])) / 2
            mid += movement
            spread += crossing
            fees += 2*FEE
            by_instrument[symbol] += movement-crossing-2*FEE
            marked += 1
    return dict(rows=len(rows), unusable=invalid, signals=signals, cancelled=cancelled,
                completed=completed, terminal_marks=marked, mid_pnl=round(mid,8),
                spread_cost=round(spread,8), fees=round(fees,8), net=round(mid-spread-fees,8),
                net_by_instrument={k:round(v,8) for k,v in sorted(by_instrument.items())})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('books', nargs='?', type=Path, default=DEFAULT)
    path = parser.parse_args().books
    rows = read(path)
    split = rows[0][0] + (rows[-1][0]-rows[0][0])*7//10
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    print(json.dumps(dict(sha256=digest, first_ns=rows[0][0], last_ns=rows[-1][0],
        holdout_start_ns=split, entry_delay_ns=DELAY, hold_ns=HOLD, fee_per_share_per_fill=FEE,
        rule='One share in sign of L1 OFI when abs(OFI) >= prior mean L1 size. First valid quote at/after delay; exit first valid quote at/after hold. Unusable quotes cancel pending entries. Terminal positions marked at last usable bid/ask with exit fee.',
        initial=replay([r for r in rows if r[0]<split]),
        holdout=replay([r for r in rows if r[0]>=split])), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
