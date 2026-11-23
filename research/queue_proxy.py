"""Historical best-bid print proxy, not a fill simulation (standard library only).

Each nonempty bid snapshot is a join opportunity, weighted equally (not by
wall-clock time). Keep its initial best bid fixed. Count at most one hit per
window (join, join+h], and use the same fully observable joins for both horizons.
Assumes both files cover the same continuous recording within each session;
the last book for each instrument marks its observation end. Windows overlap.
"""
import argparse
from bisect import bisect_right
import csv
from collections import defaultdict
import gzip
import json
from pathlib import Path

NS = 1_000_000_000


def hits(books, sells, end):
    """Return (eligible joins, inclusive/strict 2s, inclusive/strict 5s)."""
    sells = sorted(sells)
    times = [t for t, _ in sells]
    counts = [0] * 5
    for t, bid in books:
        if t + 5 * NS > end:
            continue
        counts[0] += 1
        start = bisect_right(times, t)
        for col, horizon in ((1, 2), (3, 5)):
            stop = bisect_right(times, t + horizon * NS, lo=start)
            if stop > start:
                low = min(price for _, price in sells[start:stop])
                counts[col] += low <= bid
                counts[col + 1] += low < bid
    return tuple(counts)


def rows(path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', newline='', encoding='utf-8-sig') as f:
        yield from csv.DictReader(f)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    downloads = Path.home() / 'Downloads'
    parser.add_argument('--books', type=Path, default=downloads / 'pricebooks.csv.gz')
    parser.add_argument('--trades', type=Path, default=downloads / 'trades.csv.gz')
    args = parser.parse_args()
    books, sells, ends = defaultdict(list), defaultdict(list), {}
    for row in rows(args.books):
        key = row['exchange_started_at'], row['instrument_id']
        t = int(row['timestamp_ns'])
        ends[key] = max(ends.get(key, t), t)
        bids = [p for p, size in json.loads(row['bids_json']) if size > 0]
        if bids:
            # Drop binary floating-point serialization noise, not real ticks.
            books[key].append((t, round(max(bids), 8)))
    for row in rows(args.trades):
        side = row['aggressor_side']
        if side not in ('ask', 'bid'):
            raise ValueError(f'unrecognized aggressor_side: {side!r}')
        if side == 'ask' and int(row['volume']) > 0:
            key = row['exchange_started_at'], row['instrument_id']
            sells[key].append((int(row['timestamp_ns']), round(float(row['price']), 8)))
    results = defaultdict(lambda: [0] * 5)
    for key, end in ends.items():
        counts = hits(books[key], sells[key], end)
        for i, n in enumerate(counts):
            results[key[1]][i] += n
    total = [sum(col) for col in zip(*results.values())] if results else [0] * 5
    print('instrument,joins,at_or_below_2s,strict_below_2s,at_or_below_5s,strict_below_5s,at_or_below_2s_pct,strict_below_2s_pct,at_or_below_5s_pct,strict_below_5s_pct')
    for symbol, counts in [*sorted(results.items()), ('ALL', total)]:
        rates = [f'{100 * n / counts[0]:.2f}' if counts[0] else 'NA' for n in counts[1:]]
        print(symbol, *counts, *rates, sep=',')


if __name__ == '__main__':
    main()
