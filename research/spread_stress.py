"""Offline, one-unit spread stress; prints JSON to stdout. No exchange client.

For a directional call the hypothetical future mid moves +win when right or
-loss when wrong, signed in the chosen direction. Assume unchanged spread
at liquidation, and cross to exit in both cases. Crossing twice costs one
full spread; joining the touch then crossing costs zero spread IF filled.
Both pay two per-side fees. This is not a fill model or measured success rate.
"""
import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import csv
import gzip
import json
import math
from pathlib import Path
from statistics import mean, median


def required_success(spread, win, loss, fee):
    def threshold(cost):
        return None if cost > win + 1e-12 else min(1., (loss + cost) / (win + loss))
    return threshold(spread + 2 * fee), threshold(2 * fee)


def match(books, times, max_age):
    """Stable timestamp sort; for equal prior timestamps use last file row."""
    books = sorted(books, key=lambda b: b[0])
    stamps = [b[0] for b in books]
    widths, skipped = [], Counter()
    for t in times:
        i = bisect_left(stamps, t) - 1
        if i < 0:
            skipped['no_prior'] += 1
        elif t - stamps[i] > max_age:
            skipped['stale'] += 1
        elif books[i][1] is None:
            skipped['invalid'] += 1
        else:
            widths.append(books[i][1])
    return widths, dict(skipped)


def rows(path):
    with gzip.open(path, 'rt', encoding='utf-8-sig', newline='') as f:
        yield from csv.DictReader(f)


def describe(widths, args):
    if not widths:
        return {'matched_prints': 0}
    spread = mean(widths)
    cross, join = required_success(spread, args.win, args.loss, args.fee)
    return dict(matched_prints=len(widths), mean_spread=round(spread, 8),
                median_spread=median(widths), cross_break_even=cross,
                passive_entry_break_even_given_fill=join)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data-dir', type=Path, default=Path.home() / 'Downloads')
    ap.add_argument('--win', type=float, default=.50, help='favorable mid move, price units')
    ap.add_argument('--loss', type=float, default=.50, help='adverse mid move magnitude')
    ap.add_argument('--fee', type=float, default=0., help='assumed fee per unit per side')
    ap.add_argument('--max-age-ms', type=int, default=1000)
    a = ap.parse_args()
    if (not all(map(math.isfinite, [a.win, a.loss, a.fee])) or
            min(a.win, a.loss) <= 0 or min(a.fee, a.max_age_ms) < 0):
        ap.error('moves must be positive and finite; fee and age nonnegative and finite')
    books, trades = defaultdict(list), defaultdict(list)
    for r in rows(a.data_dir / 'pricebooks.csv.gz'):
        bids = [p for p, q in json.loads(r['bids_json']) if p > 0 and q >= 1]
        asks = [p for p, q in json.loads(r['asks_json']) if p > 0 and q >= 1]
        width = None
        if bids and asks and min(asks) > max(bids):
            width = round(min(asks) - max(bids), 8)
        books[r['exchange_started_at'], r['instrument_id']].append((int(r['timestamp_ns']), width))
    for r in rows(a.data_dir / 'trades.csv.gz'):
        trades[r['exchange_started_at'], r['instrument_id']].append(int(r['timestamp_ns']))
    by_symbol, all_widths, skipped = defaultdict(list), [], Counter()
    for key in sorted(books.keys() | trades.keys()):
        widths, missing = match(books[key], trades[key], a.max_age_ms * 1_000_000)
        by_symbol[key[1]].extend(widths)
        all_widths.extend(widths)
        skipped.update(missing)
    report = dict(model='Hypothetical thresholds, not executable PnL or observed win rates. '
                  'Each matched public print weights one strictly prior same-session spread. '
                  'Assume unchanged exit width, common marketable exit, fixed mid moves and one unit. '
                  'Passive threshold conditions on entry fill; no queue, latency, impact or adverse-selection model. '
                  'Null threshold means impossible even at 100% success.',
                  assumptions=dict(win=a.win, loss=a.loss, fee_per_side=a.fee, max_age_ms=a.max_age_ms),
                  books=sum(map(len, books.values())), public_prints=sum(map(len, trades.values())),
                  excluded_prints=skipped, pooled=describe(all_widths, a),
                  by_instrument={s: describe(w, a) for s, w in sorted(by_symbol.items())})
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
