"""Practice only: compete one tick inside a wide spread, never cross to enter."""
import math, time
from datetime import datetime, timezone
from continuous_practice import STOP, seconds_left, log, book
from priority_practice import supervise
from live_pilot import run


def inside(bid, ask, tick):
    if not all(map(math.isfinite,(bid,ask,tick))) or tick<=0 or bid<=0: return None
    b,a=round(bid/tick),round(ask/tick)
    if abs(b*tick-bid)>1e-7 or abs(a*tick-ask)>1e-7 or a-b<3: return None
    return round((b+1)*tick,8)


class Selected:
    def __init__(self,x,s,stock): self.x,self.s,self.stock=x,s,stock
    def __getattr__(self,name): return getattr(self.x,name)
    def get_tradable_instruments(self): return {self.s:self.stock}
    def insert_order(self,s,*,price,side,volume,order_type):
        if s!=self.s or volume!=1: raise ValueError('out of scope')
        if side=='bid':
            q=book(self.x,s)
            p=inside(q[0],q[1],self.stock.tick_size) if q else None
            if p is None or q[0]!=price: raise ValueError('entry quote changed; stand down')
            price=p
        log('WIRE',s,side,volume,price,order_type)
        return self.x.insert_order(s,price=price,side=side,volume=volume,order_type=order_type)


def rounds(x,budget):
    ids=x.get_tradable_instruments(); baseline=x.get_pnl(); end=time.monotonic()+budget
    if baseline is None or not math.isfinite(baseline): return
    completed=0
    while time.monotonic()+12<end and completed<24 and not STOP.exists():
        candidates=[]
        for s,stock in ids.items():
            if getattr(stock.instrument_type,'name',None)!='STOCK': continue
            q=book(x,s)
            if q and inside(q[0],q[1],stock.tick_size) is not None:
                # Prefer a thick two-sided book, not a large but empty spread.
                candidates.append((min(q[2:]),s,stock))
        if not candidates: time.sleep(.5); continue
        _,s,stock=max(candidates,key=lambda r:(r[0],r[1])); log('ROUND',completed+1,s)
        if not run(Selected(x,s,stock),emit=log): return
        completed+=1; change=x.get_pnl()-baseline; log('RECONCILED',completed,'PnL change',round(change,4))
        if not math.isfinite(change) or change<=-.5: return
        time.sleep(.5)
    log('FAST PASSIVE FINISHED',completed)

if __name__=='__main__':
    from optibook.synchronous_client import Exchange
    supervise(Exchange,budget=min(180,seconds_left(datetime.now(timezone.utc))),runner=rounds)
