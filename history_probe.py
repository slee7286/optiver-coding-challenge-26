"""Hosted VS Code: open this file and select Run Python File. No live exchange session.

Reads the final five minutes of the previous UTC hour. Prints aggregates only;
raw loader downloads stay in the user's home cache, not the workspace.
"""
import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


def spread(row):
    """The documented JSON sides are best-first [price, volume] pairs."""
    try:
        bid, ask = [json.loads(row[key])[0] for key in ('bids_json', 'asks_json')]
        bp, bv = map(float, bid)
        ap, av = map(float, ask)
        if all(map(math.isfinite, (bp, bv, ap, av))) and bv > 0 and av > 0 and ap >= bp:
            return ap - bp
    except (TypeError, KeyError, ValueError, IndexError, OverflowError):
        pass
    return None


def summarize(feed, frame, instruments):
    for values in frame.itertuples(index=False, name=None):
        row = dict(zip(frame.columns, values))
        iid = row.get('instrument_id')
        if not isinstance(iid, str) or not iid:
            continue
        if iid not in instruments:
            instruments[iid] = dict(instrument_id=iid, trades=0, orders=0, pricebooks=0,
                                    traded_volume=0, valid_two_sided=0, mean_spread=None)
        entry = instruments[iid]
        entry[feed] += 1
        if feed == 'trades':
            try:
                entry['traded_volume'] += max(0, int(row['volume']))
            except (KeyError, TypeError, ValueError, OverflowError):
                pass
        elif feed == 'pricebooks':
            width = spread(row)
            if width is not None:
                entry['valid_two_sided'] += 1
                mean = entry['mean_spread'] or 0
                entry['mean_spread'] = mean + (width - mean) / entry['valid_two_sided']


def probe(loader, *, now=None, sleep=time.sleep):
    now = now or datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise ValueError('now must be timezone-aware')
    # Hosted loader examples use naive datetimes representing UTC.
    end = now.astimezone(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    start = end - timedelta(minutes=5)
    report = {'start_utc': start.isoformat() + 'Z', 'end_utc': end.isoformat() + 'Z',
              'feeds': {}, 'candidates': []}
    instruments = {}
    for i, feed in enumerate(('trades', 'orders', 'pricebooks')):
        if i:
            sleep(2.1)  # 30 queries/min/user, burst of 10; never retry on 429.
        frame = getattr(loader, 'get_' + feed + '_df')(start, end)
        report['feeds'][feed] = {'rows': len(frame), 'columns': list(frame.columns)}
        summarize(feed, frame, instruments)
        del frame
    report['instrument_count'] = len(instruments)
    report['candidates'] = sorted(instruments.values(), key=lambda r: (
        -r['traded_volume'], -r['trades'], -r['valid_two_sided'], r['instrument_id']))[:10]
    return report


def main():
    try:
        from optibook.historical_data_loader import HistoricalDataLoader
    except ImportError:
        raise SystemExit('Run this file with the preinstalled Python in hosted VS Code.') from None
    loader = HistoricalDataLoader(data_dir=Path.home() / '.cache' / 'optibook')
    try:
        report = probe(loader)
        print('HISTORY UTC', report['start_utc'], report['end_utc'], flush=True)
        print('API ROWS', {k: v['rows'] for k, v in report['feeds'].items()}, flush=True)
        print('TOP SYMBOLS', [v['instrument_id'] for v in report['candidates']], flush=True)
    except Exception as exc:
        print('HISTORY API ERROR:', type(exc).__name__, flush=True)
    # An organizer-provided archive is an independent data source, not API output.
    import gzip
    import csv
    for name in ('orders', 'pricebooks', 'trades'):
        path = Path(__file__).resolve().parent / (name + '.csv.gz')
        if path.is_file():
            with gzip.open(path, 'rt', encoding='utf-8-sig', newline='') as f:
                rows = csv.reader(f)
                print('ARCHIVE', path.name, 'columns', next(rows, []),
                      'first row present', bool(next(rows, [])), flush=True)
    print('HISTORY PROBE FINISHED', flush=True)


if __name__ == '__main__':
    main()
