"""Offline one-lot challenger; no exchange client or output files.

Frozen before replay: join the heavier touch when its displayed size is >=2x
its opposite. One order/position per instrument. No parameter search. Split
by elapsed time (first 2/3, final 1/3); reset all state at the boundary.
100 ms outbound latency, 1 s maximum book age, 5 s resting entry lifetime,
5 s from entry fill to exit cancellation, then 100 ms to cross. Orders have
exchange-side expiry. Already-resting orders need a strictly later opposite
aggressor print strictly through their price; quote touches never suffice.
Pay our limit, not the better print. A missing exit book delays liquidation
until an executable fresh two-sided snapshot; end-of-window inventory blocks
a pass. Both segments need >=20 completed trips and positive net P&L.
Fee stress assumption: one cent per lot per side, not an exchange fee.
Tied timestamp convention: books, timers, prints, new signals. No same-time
fill at activation. A through-print is a counterfactual proxy, not a live fill.
"""
import argparse
from collections import Counter, defaultdict
import csv
from dataclasses import dataclass
import gzip
import hashlib
import heapq
from itertools import groupby, count
import json
from pathlib import Path

MS = 1_000_000
LATENCY, MAX_AGE, LIFE = 100 * MS, 1000 * MS, 5000 * MS
KEYS = 'signals entry_posts entry_rejects expired entry_fills exit_posts exit_rejects passive_exits unwinds incomplete pending_entries completed wins gross_cents net_cents'.split()


def book(ms, symbol, bid, bsize, ask, asize):
    return ms * MS, symbol, 'B', (bid, bsize, ask, asize)


def trade(ms, symbol, side, price):
    return ms * MS, symbol, 'T', (side, price, 1)


@dataclass
class Order:
    side: int  # +1 long, -1 short
    entry: int
    stage: str = 'placing'
    limit: int = 0
    arrival: int = 0


def _symbol(events, end):
    r = Counter(dict.fromkeys(KEYS, 0))
    state = None
    quote = (0, 0, 0, 0)
    stamp = None
    timers, seq = [], count()

    def schedule(t, action):
        heapq.heappush(timers, (t, next(seq), action, state))

    def fresh(t):
        return stamp is not None and t - stamp <= MAX_AGE and 0 < quote[0] < quote[2] and min(quote[1], quote[3]) >= 1

    def close(px, kind):
        nonlocal state
        assert state is not None
        gross = state.side * (px - state.entry)
        r[kind] += 1
        r['completed'] += 1
        r['gross_cents'] += gross
        r['net_cents'] += gross - 2
        r['wins'] += gross > 2
        state = None

    def unwind(t):
        if state and state.stage == 'liquidate' and fresh(t):
            close(quote[0 if state.side == 1 else 2], 'unwinds')

    def advance(t, inclusive):
        nonlocal state
        while timers and (timers[0][0] < t or (inclusive and timers[0][0] == t)):
            at, _, action, order = heapq.heappop(timers)
            if state is None or state is not order:
                continue
            if action in ('entry', 'exit'):
                entry = action == 'entry'
                side = state.side if entry else -state.side
                px = state.entry if entry else state.limit
                if not fresh(at) or not (px < quote[2] if side == 1 else px > quote[0]):
                    r[action + '_rejects'] += 1
                    if entry:
                        state = None
                    continue
                state.stage, state.arrival = action, at
                r[action + '_posts'] += 1
                if entry:
                    schedule(at + LIFE, 'expire')
            elif action == 'expire' and state.stage == 'entry':
                r['expired'] += 1
                state = None
            elif action == 'cancel':
                state.stage = 'cancelled'
                schedule(at + LATENCY, 'cross')
            elif action == 'cross':
                state.stage = 'liquidate'
                unwind(at)

    for t, group in groupby(events, key=lambda x: x[0]):
        if t > end:
            break
        advance(t, False)
        batch = list(group)
        books = [e[3] for e in batch if e[2] == 'B']
        if books:
            stamp, quote = t, books[-1]
        advance(t, True)
        unwind(t)
        for _, _, kind, data in batch:
            if kind != 'T' or not state or state.stage not in ('entry', 'exit') or t <= state.arrival:
                continue
            aggr, px, qty = data
            entry = state.stage == 'entry'
            side = state.side if entry else -state.side
            limit = state.entry if entry else state.limit
            if qty < 1 or aggr != ('ask' if side == 1 else 'bid') or side * (limit - px) <= 0:
                continue
            if not entry:
                close(limit, 'passive_exits')
                continue
            r['entry_fills'] += 1
            state.stage = 'holding'
            schedule(t + LIFE, 'cancel')
            if fresh(t):
                state.limit = quote[2 if side == 1 else 0]
                schedule(t + LATENCY, 'exit')
            else:
                r['exit_rejects'] += 1
        if books and state is None and fresh(t):
            bid, bs, ask, az = quote
            side = 1 if bs >= 2 * az else -1 if az >= 2 * bs else 0
            if side:
                r['signals'] += 1
                state = Order(side, bid if side == 1 else ask)
                schedule(t + LATENCY, 'entry')
    advance(end, True)
    if state:
        r['pending_entries' if state.stage in ('placing', 'entry') else 'incomplete'] += 1
    return r


def replay(events, end):
    by_symbol = defaultdict(list)
    for e in events:
        by_symbol[e[1]].append(e)
    totals = Counter(dict.fromkeys(KEYS, 0))
    for tape in by_symbol.values():
        totals.update(_symbol(sorted(tape, key=lambda e: e[0]), end))
    return dict(totals)


def cents(value):
    x = float(value) * 100
    if abs(x - round(x)) > 1e-6 or x <= 0:
        raise ValueError(f'price not positive whole cents: {value}')
    return round(x)


def load(directory):
    events, metadata, sessions = [], {}, set()
    for stem in ('pricebooks', 'trades'):
        p = directory / (stem + '.csv.gz')
        with p.open('rb') as raw:
            digest = hashlib.file_digest(raw, 'sha256').hexdigest()
        n = 0
        with gzip.open(p, 'rt', newline='') as f:
            for row in csv.DictReader(f):
                n += 1
                sessions.add(row['exchange_started_at'])
                if stem == 'pricebooks':
                    parts = []
                    for name, choose in [('bids_json', max), ('asks_json', min)]:
                        levels = json.loads(row[name])
                        px, qty = choose(levels, key=lambda x: x[0]) if levels else (0, 0)
                        parts.extend([cents(px) if px else 0, int(qty)])
                    kind, data = 'B', tuple(parts)
                else:
                    side = row['aggressor_side']
                    if side not in ('bid', 'ask') or int(row['volume']) <= 0:
                        raise ValueError('invalid trade')
                    kind, data = 'T', (side, cents(row['price']), int(row['volume']))
                events.append((int(row['timestamp_ns']), row['instrument_id'], kind, data))
        if not n:
            raise ValueError(f'empty {p}')
        metadata[stem] = {'rows': n, 'sha256': digest}
    if len(sessions) != 1:
        raise ValueError('expected one shared exchange session')
    return events, metadata


def evaluate(directory):
    events, meta = load(directory)
    times = [e[0] for e in events]
    start, end = min(times), max(times)
    cut = start + (end - start) * 2 // 3
    result = {'inputs': meta, 'start_ns': start, 'holdout_start_ns': cut, 'end_ns': end}
    for label, lo, hi in [('development', start, cut - 1), ('holdout', cut, end)]:
        tape = [e for e in events if lo <= e[0] <= hi]
        result[label] = replay(tape, hi) | {'books': sum(e[2] == 'B' for e in tape), 'prints': sum(e[2] == 'T' for e in tape)}
    passes = all(r['completed'] >= 20 and r['net_cents'] > 0 and not r['incomplete'] for r in (result['development'], result['holdout']))
    result['verdict'] = 'PASS_RESEARCH_ONLY' if passes else 'NO_GO'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', nargs='?', type=Path, default=Path.home() / 'Downloads')
    try:
        print(json.dumps(evaluate(parser.parse_args().directory), indent=2))
    except (OSError, EOFError, ValueError) as exc:
        parser.exit(2, f'No verdict: {exc}\n')
