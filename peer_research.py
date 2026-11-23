"""Offline frozen hypothesis: NBIS trailing 1s mid move leads CRWV in the same direction.
One share on a UTC 5s grid, 100ms latency each way, exit order sent 2s after entry
order sent. No fitting; first 70% / final 30% of elapsed tape time, independently flat.
Exact decision-time updates are unavailable to the signal. Arrival-time snapshot is
assumed executable, not an observed fill. Fees assumed 1bp of notional on each leg.
"""
import argparse
from bisect import bisect_right, bisect_left
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path

S=10**9
GRID, LOOKBACK, FRESH, LATENCY, HOLD = 5*S, S, 2*S, S//10, 2*S
LEADER, TARGET = 'NBIS', 'CRWV'
FEE=0.0001


class Book:
    def __init__(self, rows: list[tuple[int, tuple[float, float] | None]]):
        rows=sorted(rows,key=lambda x:x[0])  # stable: last file row at same ns wins
        latest=dict(rows)
        self.times=list(latest)
        self.quotes=list(latest.values())
        self.valid=[t for t,q in latest.items() if q is not None]

    def at(self, t, start=None):
        i=bisect_right(self.times,t)-1
        if i<0 or t-self.times[i]>FRESH or (start is not None and self.times[i]<start):
            return None
        return self.quotes[i]


def run(leader,target,start,end):
    out: dict[str, int | float]={k:0 for k in ('decisions','missing_signal','flat_signal','signals','missed_entry','busy','trades','long','short','delayed_exits','unclosed','wins','mid_pnl','crossed_pnl','fees','net_pnl')}
    busy_until=start
    for t in range(((start+LOOKBACK+GRID-1)//GRID)*GRID,end-LATENCY-HOLD,GRID):
        out['decisions']+=1
        a,b=leader.at(t-LOOKBACK,start), leader.at(t-1,start)
        if not a or not b:
            out['missing_signal']+=1; continue
        move=round(sum(b)-sum(a),9)
        if not move:
            out['flat_signal']+=1; continue
        out['signals']+=1
        if t<=busy_until:
            out['busy']+=1; continue
        sign=1 if move>0 else -1
        entry=t+LATENCY; exit_time=entry+HOLD
        q=target.at(entry,start)
        if not q:
            out['missed_entry']+=1; continue
        z=target.at(exit_time,start)
        if not z:
            i=bisect_left(target.valid,exit_time)
            if i==len(target.valid) or target.valid[i]>=end:
                out['unclosed']+=1; break
            exit_time=target.valid[i]; z=target.at(exit_time,start); out['delayed_exits']+=1
        busy_until=exit_time
        px=q[1] if sign>0 else q[0]; close=z[0] if sign>0 else z[1]
        crossed=sign*(close-px); fee=FEE*(px+close)
        out['long' if sign>0 else 'short']+=1; out['trades']+=1
        out['mid_pnl']+=sign*(sum(z)-sum(q))/2; out['crossed_pnl']+=crossed
        out['fees']+=fee; out['net_pnl']+=crossed-fee; out['wins']+=int(crossed>fee)
    return {k:round(v,8) for k,v in out.items()}


def research(path):
    counts=Counter(); two_sided=Counter(); series=defaultdict(list); sessions=set(); first=last=None
    with gzip.open(path,'rt',newline='') as f:
        for row in csv.DictReader(f):
            t=int(row['timestamp_ns']); symbol=row['instrument_id']; counts[symbol]+=1
            first=t if first is None else min(first,t); last=t if last is None else max(last,t)
            sessions.add(row['exchange_started_at'])
            bids,asks=([p for p,v in json.loads(row[key]) if v>=1 and math.isfinite(p) and p>0] for key in ('bids_json','asks_json'))
            q=(max(bids),min(asks)) if bids and asks and max(bids)<min(asks) else None
            if q: two_sided[symbol]+=1
            if symbol in (LEADER,TARGET): series[symbol].append((t,q))
    if first is None or last is None or len(sessions)!=1 or not all(series[s] for s in (LEADER,TARGET)):
        raise ValueError('Expected one exchange session and both instruments')
    end=last+1; cut=first+7*(end-first)//10
    leader, target = Book(series[LEADER]), Book(series[TARGET])
    return dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),rows=sum(counts.values()),counts=counts,two_sided=two_sided,first_ns=first,cut_ns=cut,end_exclusive_ns=end,development=run(leader,target,first,cut),holdout=run(leader,target,cut,end))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('archive',type=Path)
    print(json.dumps(research(parser.parse_args().archive),indent=2))
