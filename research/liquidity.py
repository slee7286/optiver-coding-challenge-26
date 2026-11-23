"""Rank one offline tape session; CSV on stdout. Lower score is better.

Score = mean of four ordinal ranks (ties get average rank): lower time-weighted
spread, greater executed volume, greater trades/min and lower stale time %.
Spread is conditioned on having a noncrossed, two-sided book; inspect two_sided_pct.
Stale = no book message for longer than --stale-seconds, not an unchanged price.
Pre-first-book time is unknown and counted as stale. A final book persists to tape end.
All symbols share the first-to-last timestamp window across both files. Each trade
row represents one execution. No exchange connection or third-party dependencies.
"""
import argparse
import csv
import gzip
import json
import math
import sys
from collections import defaultdict
from itertools import groupby
from pathlib import Path


def records(path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8-sig', newline='') as f:
        yield from csv.DictReader(f)


def analyze(books, trades, tick, stale):
    quotes = defaultdict(list)
    fills = defaultdict(int)
    volume = defaultdict(int)
    start, end = None, None
    sessions = set()
    for path, is_book in [(books, True), (trades, False)]:
        for r in records(path):
            symbol, ts = r['instrument_id'], int(r['timestamp_ns'])
            start = ts if start is None else min(start, ts)
            end = ts if end is None else max(end, ts)
            if r.get('exchange_started_at'):
                sessions.add(r['exchange_started_at'])
            if is_book:
                bids, asks = [json.loads(r[k]) for k in ('bids_json', 'asks_json')]
                spread = None
                if bids and asks:
                    diff = min(p for p, _ in asks) - max(p for p, _ in bids)
                    if diff >= 0:
                        spread = round(diff / tick, 9)
                quotes[symbol].append((ts, spread))
            else:
                fills[symbol] += 1
                volume[symbol] += int(r['volume'])
    if len(sessions) > 1 or start is None or end is None or start == end:
        raise ValueError('expected one session with positive duration')
    window = end - start
    rows = []
    for sym in sorted(quotes.keys() | fills.keys()):
        events = sorted(quotes[sym], key=lambda x: x[0])
        unknown = (events[0][0] if events else end) - start
        stale_ns, intervals = unknown, 0
        valid_ns = weighted = max_gap = 0
        for i, (ts, spread) in enumerate(events):
            gap = (events[i+1][0] if i+1 < len(events) else end) - ts
            max_gap = max(max_gap, gap)
            if gap > stale * 1e9:
                intervals += 1
                stale_ns += gap - stale * 1e9
            if spread is not None:
                valid_ns += gap
                weighted += gap * spread
        rows.append(dict(symbol=sym, spread_ticks=weighted/valid_ns if valid_ns else None,
                         volume=volume[sym], trades=fills[sym], trades_min=fills[sym]*60e9/window,
                         stale_intervals=intervals, stale_pct=100*stale_ns/window,
                         max_book_gap_s=max_gap/1e9 if events else None,
                         two_sided_pct=100*valid_ns/window, window_s=window/1e9))
    for metric, sign in [('spread_ticks', 1), ('volume', -1), ('trades_min', -1), ('stale_pct', 1)]:
        def key(r):
            return math.inf if r[metric] is None else sign*r[metric]
        offset = 0
        for _, tied in groupby(sorted(rows, key=key), key):
            group = list(tied)
            for r in group:
                r['score'] = r.get('score', 0) + (offset + (len(group)+1)/2)/4
            offset += len(group)
    return sorted(rows, key=lambda r: (r['score'], r['symbol']))


def main():
    d = Path.home() / 'Downloads'
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--books', type=Path, default=d/'pricebooks.csv.gz')
    p.add_argument('--trades', type=Path, default=d/'trades.csv.gz')
    p.add_argument('--tick-size', type=float, default=0.1, help='common price tick (default: 0.1)')
    p.add_argument('--stale-seconds', type=float, default=1, help='book silence threshold (default: 1 second)')
    a = p.parse_args()
    if not all(math.isfinite(v) and v > 0 for v in (a.tick_size, a.stale_seconds)):
        p.error('tick and stale threshold must be finite and positive')
    rows = analyze(a.books, a.trades, a.tick_size, a.stale_seconds)
    w = csv.DictWriter(sys.stdout, ['rank', 'score', *[k for k in rows[0] if k != 'score']], lineterminator='\n')
    w.writeheader()
    for i, r in enumerate(rows, 1):
        w.writerow(dict(rank=i, **{k: round(v, 5) if isinstance(v, float) else v for k, v in r.items()}))


if __name__ == '__main__':
    main()
