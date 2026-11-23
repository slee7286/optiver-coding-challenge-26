"""Team 011 practice-only resident strategy. Ctrl+C or create STOP_PRACTICE to stop.
One connection; periodically refit quick_strategy in memory, never reconnect on loss.
"""
import json
import math
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from quick_strategy import SYMBOLS, select, direction, COST

STOP = Path(__file__).with_name('STOP_PRACTICE')
END = datetime(2026, 11, 23, 20, 0, tzinfo=timezone.utc)

def seconds_left(now): return max(0, min(3600, int((END-now).total_seconds())))
def candidate(c): return (c.get('symbol'), c.get('reverse'), c.get('hold_ns'))
def eligible(c, previous):
    return bool(c.get('enabled') and not c['holdout_unresolved'] and c['train_net']>0
                and c['holdout_net']>0 and c['holdout_trades']>=8 and candidate(c)==previous)

def log(*args): print(datetime.now(timezone.utc).strftime('%H:%M:%SZ'), *args, flush=True)

def book(x,s):
    b=x.get_last_price_book(s)
    if not b or not b.timestamp or not b.bids or not b.asks: return None
    ts=b.timestamp
    if not ts.tzinfo: ts=ts.replace(tzinfo=timezone.utc)
    if not -.1 <= time.time()-ts.timestamp() <= 1: return None
    bid,ask=b.bids[0],b.asks[0]; q=(bid.price,ask.price,bid.volume,ask.volume)
    if all(map(math.isfinite,q)) and 0<q[0]<q[1] and min(q[2:])>0: return q
    return None

def inventory(x): return {k:v for k,v in x.get_positions().items() if v}

def clear(x,ids): return not any(x.get_outstanding_orders(s) for s in ids)

def ioc(x,s,d,q):
    if not x.is_connected(): raise RuntimeError('connection lost')
    side='bid' if d==1 else 'ask'; price=q[1] if d==1 else q[0]
    r=x.insert_order(s,price=price,side=side,volume=1,order_type='ioc')
    if not r.success: raise RuntimeError('IOC rejected')
    time.sleep(.30); fills=x.poll_new_trades(s)
    if len(fills)>1 or any(f.order_id!=r.order_id or f.volume!=1 or f.side!=side for f in fills) or x.get_outstanding_orders(s):
        raise RuntimeError('ambiguous execution; no retry')
    log('IOC',s,side,r.order_id,'fill',fills[0].price if fills else None)
    return fills[0].price if fills else None

def run(x,budget):
    ids=tuple(x.get_instruments()); symbols=sorted(SYMBOLS.intersection(x.get_tradable_instruments()))
    if not symbols or inventory(x) or not clear(x,ids): raise RuntimeError('requires exclusive flat account without orders')
    start=time.monotonic(); end=start+budget; report=start; refit=start+90; last_trade=start-10
    rows={s:deque() for s in symbols}; cfg=None; previous=None; tries=0; net=0.; fills=0
    log('CONNECTED PRACTICE',symbols,'flat; shadow first; budget seconds',budget)
    while time.monotonic()<end and not STOP.exists():
        now=time.monotonic(); stamp=time.time_ns(); current={}
        if not x.is_connected(): raise RuntimeError('lost team connection; not reconnecting')
        for s in symbols:
            q=book(x,s); current[s]=q; r=rows[s]; r.append((stamp,q))
            while r and r[0][0]<stamp-240_000_000_000: r.popleft()
        if now>=refit:
            c=select({s:list(r) for s,r in rows.items()}); cfg=c if eligible(c,previous) else None
            previous=candidate(c) if c.get('enabled') else None
            log('REFIT',json.dumps(c),'ACTIVE',bool(cfg)); refit=now+90
        if now>=report:
            log('HEARTBEAT','LIVE' if cfg else 'SHADOW','fresh books',sum(q is not None for q in current.values()),'attempts',tries,'closed',fills,'net estimate',round(net,3)); report=now+15
        if cfg and end-now>8 and now-last_trade>=10 and tries<12 and net>-.50:
            s=cfg['symbol']; q=current[s]; d=direction(q,cfg['reverse']) if q else 0
            if d and not STOP.exists():
                if inventory(x) or not clear(x,ids): raise RuntimeError('unexpected team exposure/orders')
                q=book(x,s)
                if not q or direction(q,cfg['reverse'])!=d: continue
                tries+=1; last_trade=now; entry=ioc(x,s,d,q); pos=inventory(x)
                if entry is None:
                    if pos: raise RuntimeError('unexplained inventory')
                else:
                    if pos!={s:d}: raise RuntimeError('entry mismatch')
                    time.sleep(cfg['hold_ns']/1e9); quote=book(x,s)
                    if not quote or inventory(x)!={s:d}: raise RuntimeError('cannot verify exit; inspect inventory')
                    exit_price=ioc(x,s,-d,quote)
                    if exit_price is None or inventory(x): raise RuntimeError('unwound quantity unverified; inspect inventory')
                    fills+=1; net+=d*(exit_price-entry)-COST; log('FLAT net estimate',round(net,3))
        time.sleep(.5)
    log('STOP','deadline or stop file','positions',inventory(x),'outstanding clear',clear(x,ids))

def main():
    budget=seconds_left(datetime.now(timezone.utc))
    if budget<10 or STOP.exists(): log('not starting: cutoff or STOP file'); return
    from optibook.synchronous_client import Exchange
    x=Exchange(); x.connect()
    try: run(x,budget)
    finally:
        try: log('FINAL inventory',inventory(x))
        finally: x.disconnect()

if __name__=='__main__': main()
