"""Team 011: fit on archived quotes, gate on untouched later quotes, one practice round.
No arguments: assess and print deployment status without taking the team connection.
--practice: after exclusive handoff, allow at most one entry IOC and one exit IOC.
"""
import argparse, bisect, csv, gzip, json, math, time
from collections import defaultdict
from pathlib import Path


SYMBOLS = set('AMD AVGO CRWV MU NBIS NVDA SNDK VRT'.split())
STEP = 500_000_000  # sample a decision at most twice per second
LAG = 250_000_000  # use a later quote for execution, not the signal quote
MAX_GAP = 1_000_000_000
COST = .02  # assumed per-lot round-trip cost beyond both executable spreads


def load(path):
    by = defaultdict(list)
    with gzip.open(path, 'rt', newline='') as f:
        for r in csv.DictReader(f):
            s = r['instrument_id']
            if s not in SYMBOLS: continue
            bids, asks = json.loads(r['bids_json']), json.loads(r['asks_json'])
            t = int(r['timestamp_ns']); q = None
            if bids and asks:
                b, v = map(float, bids[0]); a, w = map(float, asks[0])
                if all(map(math.isfinite, (b,a,v,w))) and 0 < b < a and min(v,w)>0:
                    q = (b,a,v,w)
            by[s].append((t,q))  # keep empty books: no fictional executable price
    return {s: sorted(rows,key=lambda row:row[0]) for s,rows in by.items()}


def direction(q, reverse):
    b,a,v,w = q
    if abs(v-w) < .6*(v+w): return 0
    return (1 if v>w else -1)*reverse


def replay(rows, reverse, hold):
    ts = [t for t,q in rows]; ready = 0; pnl = []; unresolved = False
    for i,(t,q) in enumerate(rows):
        # Scheduled end of the evaluation window: leave time to unwind.
        if t+hold+LAG+MAX_GAP>=ts[-1]: break
        if t < ready or not q: continue
        side = direction(q,reverse)
        ready = t+STEP
        if not side: continue
        j = bisect.bisect_left(ts,t+LAG,i+1)
        if j==len(ts) or ts[j]-t>LAG+MAX_GAP or not rows[j][1]: continue
        entry = rows[j][1]; limit = q[1] if side>0 else q[0]
        if (entry[1]>limit if side>0 else entry[0]<limit): continue
        k = bisect.bisect_left(ts,ts[j]+hold,j+1)
        # No exit quote: carry the lot until the next observable two-sided book.
        while k<len(ts) and not rows[k][1]: k+=1
        if k==len(ts):
            unresolved=True; break
        out = rows[k][1]; pnl.append((out[0]-entry[1] if side>0 else entry[0]-out[1])-COST)
        ready=ts[k]+STEP
    return len(pnl),round(sum(pnl),6),unresolved


def select(by):
    lo=min(rows[0][0] for rows in by.values()); hi=max(rows[-1][0] for rows in by.values()); cut=lo+(hi-lo)*2//3
    best=None
    for s,rows in sorted(by.items()):
        train=[r for r in rows if r[0]<cut]; test=[r for r in rows if r[0]>=cut]
        for hold in (1_000_000_000,3_000_000_000):
            for rev in (1,-1):
                n,p,_=replay(train,rev,hold)
                if n<10: continue
                score=p/n
                if best is None or score>best[0]: best=(score,s,rev,hold,n,p,test)
    if best is None: return {'enabled':False,'reason':'insufficient training trades'}
    _,s,rev,hold,n,p,test=best; m,g,unresolved=replay(test,rev,hold)
    return dict(symbol=s,reverse=rev,hold_ns=hold,train_trades=n,train_net=p,holdout_trades=m,holdout_net=g,holdout_unresolved=unresolved,enabled=p>0 and m>=8 and g>0 and not unresolved)


def practice(cfg):
    from optibook.synchronous_client import Exchange
    x=Exchange(); x.connect(); s=cfg['symbol']
    try:
        ids=list(x.get_instruments()); pos=lambda:{k:v for k,v in x.get_positions().items() if v}
        if pos() or any(x.get_outstanding_orders(i) for i in ids): raise RuntimeError('team is not flat/clear')
        def quote():
            book=x.get_last_price_book(s)
            if not book or not book.bids or not book.asks: return None
            b,a=book.bids[0],book.asks[0]; q=b.price,a.price,b.volume,a.volume
            if not (all(map(math.isfinite,q)) and 0<q[0]<q[1] and min(q[2:])>0): return None
            if book.timestamp is None or abs(time.time()-book.timestamp.timestamp())>1: return None
            return q
        def order(side,q):
            if not x.is_connected(): raise RuntimeError('disconnected')
            r=x.insert_order(s,price=q[1] if side==1 else q[0],side='bid' if side==1 else 'ask',volume=1,order_type='ioc'); print('IOC',s,side,r.success,r.order_id,flush=True)
            if not r.success: raise RuntimeError('rejected; no retry')
            time.sleep(.35); fills=x.poll_new_trades(s)
            if any(f.order_id!=r.order_id or f.volume!=1 or f.side!=('bid' if side==1 else 'ask') for f in fills) or len(fills)>1: raise RuntimeError('ambiguous fills')
            return fills
        for _ in range(20):
            q=quote(); d=direction(q,cfg['reverse']) if q else 0
            if d: break
            time.sleep(.5)
        else: print('NO SIGNAL'); return
        f=order(d,q); p=pos()
        if not f and not p: print('ENTRY NOT FILLED'); return
        if not f or p!={s:d}: raise RuntimeError('entry state not reconciled')
        time.sleep(cfg['hold_ns']/1e9); q=quote()
        if not q or pos()!={s:d}: raise RuntimeError('cannot verify exit book/inventory')
        out=order(-d,q)
        if len(out)!=1 or pos(): raise RuntimeError('residual inventory; inspect dashboard')
        print('ROUND TRIP',s,d,f[0].price,out[0].price,'FLAT',flush=True)
    finally:
        try: print('FINAL POSITIONS',x.get_positions(),flush=True)
        finally: x.disconnect()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--archive',type=Path,default=Path(__file__).with_name('pricebooks.csv.gz'));parser.add_argument('--practice',action='store_true');args=parser.parse_args()
    data=load(args.archive); cfg=select(data); print('SNAPSHOTS',sum(map(len,data.values())),'SYMBOLS',len(data),'CONFIG',json.dumps(cfg),flush=True)
    if args.practice and cfg['enabled']: practice(cfg)
    else: print('DEPLOYED: STAND BY; NO ORDERS' if cfg['enabled'] else 'DEPLOYED: NO TRADE; HISTORICAL GATE FAILED',flush=True)
