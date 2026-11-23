"""Team 011 simulator: slowly build a diversified long basket, hold and monitor."""
import math,time
from datetime import datetime,timezone
from continuous_practice import STOP,book,log,seconds_left
from priority_practice import supervise

NAMES=('VRT','NVDA','MU','AMD','AVGO','SNDK','NBIS','CRWV')
TARGET=10  # lots per stock, including inherited inventory
CLIP=2

def remainder(pos): return max(0,min(CLIP,TARGET-pos))

def basket(x,budget):
    end=time.monotonic()+budget; ids=tuple(x.get_instruments()); stocks=x.get_tradable_instruments()
    pos=lambda:{s:v for s,v in x.get_positions().items() if v}
    def clear(): return not any(x.get_outstanding_orders(s) for s in ids)
    p=pos(); baseline=x.get_pnl()
    if not math.isfinite(baseline) or not clear() or any(s not in NAMES or not 0<v<=TARGET for s,v in p.items()): raise RuntimeError('unexpected starting inventory/orders')
    log('BASKET LIVE | inherited',p,'PnL',baseline,'10 per symbol, 80 gross maximum'); last=0; idx=0; paused=False
    while time.monotonic()+3<end and not STOP.exists():
        if not x.is_connected(): raise RuntimeError('disconnected')
        actual=pos()
        if actual!=p or not clear(): raise RuntimeError('external account change; no new orders')
        pnl=x.get_pnl()
        if not math.isfinite(pnl): raise RuntimeError('invalid PnL')
        if pnl<baseline-5: paused=True  # no more entries; never liquidate just to show flat
        now=time.monotonic()
        if now-last>=15: log('HOLD positions',p,'gross',sum(p.values()),'PnL',pnl,'paused',paused); last=now
        if paused or all(p.get(s,0)>=TARGET for s in NAMES if s in stocks): time.sleep(3);continue
        # Breadth first; rotate through equally held names. Reject wide/stale books.
        available=[]
        for j in range(len(NAMES)):
            s=NAMES[(idx+j)%len(NAMES)]
            if s not in stocks or not remainder(p.get(s,0)): continue
            q=book(x,s); tick=stocks[s].tick_size
            if q and 0<tick and q[1]-q[0]<=3.001*tick and q[3]>=1: available.append((p.get(s,0),j,s))
        if not available: time.sleep(2); continue
        _,_,s=min(available); idx=(NAMES.index(s)+1)%len(NAMES); q=book(x,s)
        if not q or q[1]-q[0]>3.001*stocks[s].tick_size: time.sleep(1);continue
        qty=min(remainder(p.get(s,0)),int(q[3]),80-sum(p.values()))
        if qty<1 or STOP.exists() or time.monotonic()+3>=end: break
        r=x.insert_order(s,price=q[1],volume=qty,side='bid',order_type='ioc')
        if not r.success: raise RuntimeError('entry rejected')
        time.sleep(.5); fills=x.poll_new_trades(s); got=sum(f.volume for f in fills)
        if not 0<=got<=qty or any(f.order_id!=r.order_id or f.side!='bid' for f in fills): raise RuntimeError('unrecognized fill')
        expected=dict(p)
        if got: expected[s]=expected.get(s,0)+got
        actual=pos()
        if actual!=expected or not clear(): raise RuntimeError('position mismatch; no retry')
        p=actual; log('ENTRY',s,'id',r.order_id,'requested',qty,'filled',got,'position',p.get(s,0),'gross',sum(p.values()))
        time.sleep(8)
    log('END: holding positions',pos(),'PnL',x.get_pnl())

if __name__=='__main__':
    from optibook.synchronous_client import Exchange
    supervise(Exchange,budget=seconds_left(datetime.now(timezone.utc)),runner=basket)
