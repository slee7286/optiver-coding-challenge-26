"""Frozen aggressive flow-follow test; no fitting or instrument selection.
Every UTC 10s, follow the dominant aggressor if >=2/3 of executed volume over
[t-10s,t), at least 40 total. One share per symbol; fill strictly prior BBO
at arrival (100ms latency, 1s freshness). Send exit 5s after entry; retry each
100ms if no usable book. Fee stress $0.01/share/side (not an exchange tariff).
First 70% / last 30% by elapsed common feed time, fresh state in each partition.
"""
import argparse
from bisect import bisect_left
import csv
import gzip
import json
from collections import defaultdict, Counter
from pathlib import Path

S = 10**9
GRID, LAT, HOLD = 10*S, S//10, 5*S
FEE = 0.01


def replay(trades, books, start, end):
    times = [row[0] for row in books]
    ticks = [row[0] for row in trades]
    def quote(t):
        i = bisect_left(times, t)-1
        if i >= 0 and max(start, t-S) <= times[i]:
            _, bid, ask = books[i]
            if 0 < bid < ask:
                return bid, ask
        return None

    r = dict(signals=0, missed=0, closed=0, unresolved=0, mid=0., crossed=0., fees=0., net=0.)
    busy_until = start
    first = ((start+GRID+GRID-1)//GRID)*GRID
    for t in range(first, end-HOLD-2*LAT, GRID):
        if t <= busy_until:
            continue
        flow = [v for _, v in trades[bisect_left(ticks,t-GRID):bisect_left(ticks,t)]]
        total, signed = sum(map(abs,flow)), sum(flow)
        if total < 40 or 3*abs(signed) < total:
            continue
        side = 1 if signed > 0 else -1
        r['signals'] += 1
        entry = quote(t+LAT)
        if not entry:
            r['missed'] += 1
            continue
        for arrival in range(t+2*LAT+HOLD,end,LAT):
            exit_book = quote(arrival)
            if exit_book:
                break
        else:
            r['unresolved'] += 1
            r['fees'] += FEE
            r['net'] -= FEE
            break
        busy_until = arrival
        crossed = exit_book[0]-entry[1] if side==1 else entry[0]-exit_book[1]
        r['closed'] += 1
        r['mid'] += side*(sum(exit_book)-sum(entry))/2
        r['crossed'] += crossed
        r['fees'] += 2*FEE
        r['net'] += crossed-2*FEE
    return r


def load(path, book):
    groups = defaultdict(list)
    sessions = set()
    with gzip.open(path,'rt',newline='') as fh:
        for row in csv.DictReader(fh):
            sessions.add(row['exchange_started_at'])
            t = int(row['timestamp_ns'])
            if book:
                bids, asks = (json.loads(row[k]) for k in ('bids_json','asks_json'))
                obs = (t,max((p for p,v in bids if v>=1),default=0),min((p for p,v in asks if v>=1),default=0))
            else:
                obs = (t,{'bid':1,'ask':-1}[row['aggressor_side']]*int(row['volume']))
            groups[row['instrument_id']].append(obs)
    for group in groups.values():
        group.sort(key=lambda row:row[0])
    return groups,sessions


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ('trades','pricebooks'):
        cli.add_argument('--'+name,type=Path,default=Path.home()/'Downloads'/(name+'.csv.gz'))
    args = cli.parse_args()
    trades, ts = load(args.trades,False)
    books, bs = load(args.pricebooks,True)
    if len(ts)!=1 or ts!=bs:
        raise ValueError('Single common exchange session required')
    start = max(min(g[0][0] for g in feed.values()) for feed in (trades,books))
    end = min(max(g[-1][0] for g in feed.values()) for feed in (trades,books))+1
    split = start+(end-start)*7//10
    for phase,lo,hi in [('development',start,split),('holdout',split,end)]:
        totals = Counter()
        for symbol in sorted(trades):
            totals.update(replay(trades[symbol],books.get(symbol,[]),lo,hi))
        print(json.dumps(dict(phase=phase,start_ns=lo,end_ns_exclusive=hi,**{k:round(v,6) for k,v in totals.items()})))


if __name__=='__main__':
    main()
