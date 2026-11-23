"""Offline hypothesis: top-decile prints temporarily move prices; fade for 1s.
One share per signal, pooled nearest-rank p90 size fitted on the first 70%
of elapsed common tape time. Test final 30% flat; no instrument selection.
100ms order latency both ways; assume fresh (< = 2s), visible two-sided BBO
strictly before arrival is executable, 1bp assumed per side. No market impact.
Exit retries every 100ms; report incomplete exits rather than dropping losses.
"""
import argparse
from bisect import bisect_left
from collections import defaultdict
import csv
import gzip
import json
from pathlib import Path

S = 10**9
LAT = S // 10
FEE = .0001


def replay(prints, books, threshold, end):
    busy = {}
    stats = dict(signals=0, busy=0, late=0, missed=0, closed=0, unclosed=0,
                 gross=0., fees=0., net=0.)

    def quote(key, arrival):
        rows = books.get(key, [])
        i = bisect_left(rows, (arrival,)) - 1
        if i >= 0:
            t, bid, ask = rows[i]
            if arrival - t <= 2*S and 0 < bid < ask:
                return bid, ask
        return None

    for t, key, size, side in prints:
        if size < threshold:
            continue
        stats['signals'] += 1
        if t <= busy.get(key, -1):
            stats['busy'] += 1
            continue
        entry = t + LAT
        exit_at = entry + S + LAT
        if exit_at >= end:
            stats['late'] += 1
            continue
        busy[key] = entry
        book = quote(key, entry)
        if book is None:
            stats['missed'] += 1
            continue
        long = side == 'ask'  # ask aggressor sold; buy at displayed ask
        px = book[1 if long else 0]
        while exit_at < end:
            book = quote(key, exit_at)
            if book is not None:
                close = book[0 if long else 1]
                stats['gross'] += (close-px) * (1 if long else -1)
                stats['fees'] += (close+px)*FEE
                stats['closed'] += 1
                break
            exit_at += LAT
        else:
            stats['unclosed'] += 1
        busy[key] = exit_at
    return {**stats, 'net': None if stats['unclosed'] else stats['gross'] - stats['fees']}


def rows(path):
    with gzip.open(path, 'rt', newline='', encoding='utf-8-sig') as f:
        yield from csv.DictReader(f)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path.home()/'Downloads')
    d = parser.parse_args().data_dir
    trades = []
    books = defaultdict(list)
    for r in rows(d/'trades.csv.gz'):
        assert r['aggressor_side'] in ('ask', 'bid')
        trades.append((int(r['timestamp_ns']), (r['exchange_started_at'], r['instrument_id']),
                       int(r['volume']), r['aggressor_side']))
    trades.sort(key=lambda x: x[0])
    for r in rows(d/'pricebooks.csv.gz'):
        bids, asks = (json.loads(r[f'{side}_json']) for side in ('bids', 'asks'))
        bid = max((p for p,v in bids if v >= 1), default=0)
        ask = min((p for p,v in asks if v >= 1), default=0)
        books[r['exchange_started_at'], r['instrument_id']].append((int(r['timestamp_ns']), bid, ask))
    for book in books.values():
        book.sort(key=lambda x: x[0])
    start = max(trades[0][0], min(b[0][0] for b in books.values()))
    end = min(trades[-1][0], max(b[-1][0] for b in books.values())) + 1
    split = start + (end-start)*7//10
    train = [r for r in trades if start <= r[0] < split]
    test = [r for r in trades if split <= r[0] < end]
    sizes = sorted(r[2] for r in train)
    threshold = sizes[(9*len(sizes)-1)//10]
    print(json.dumps(dict(start_ns=start, split_ns=split, end_exclusive_ns=end,
                          threshold=threshold, train_prints=len(train), holdout_prints=len(test),
                          calibration=replay(train, books, threshold, split),
                          holdout=replay(test, books, threshold, end)), indent=2))


if __name__ == '__main__':
    main()
