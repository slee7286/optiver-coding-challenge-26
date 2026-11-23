"""Team 011 Optibook only. Continuous passive one-lot rounds until stop, loss or cutoff."""
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from continuous_practice import STOP, book, log, seconds_left
from priority_practice import supervise
from live_pilot import run


class Venue:
    def __init__(self, x, s, stock, deadline):
        self.x, self.s, self.stock, self.deadline = x, s, stock, deadline
    def __getattr__(self, n): return getattr(self.x,n)
    def get_tradable_instruments(self): return {self.s:self.stock}
    def insert_order(self,s,*,price,volume,side,order_type):
        if s!=self.s or volume!=1: raise ValueError('invalid order scope')
        q=book(self.x,s)
        if q is None: raise ValueError('stale/empty book; no submission')
        if side=='bid':
            if STOP.exists() or time.monotonic()+10>=self.deadline: raise ValueError('stop/deadline')
            if q[0]!=price: raise ValueError('entry quote moved; no submission')
        elif order_type=='limit' and price<=q[0]: raise ValueError('exit quote moved')
        return self.x.insert_order(s,price=price,volume=1,side=side,order_type=order_type)


def resident(x,budget):
    end=time.monotonic()+budget; baseline=x.get_pnl(); peak=baseline
    if baseline is None or not math.isfinite(baseline): raise ValueError('invalid starting PnL')
    ids=x.get_instruments(); stocks=x.get_tradable_instruments(); n=0
    if any(x.get_positions().values()) or any(x.get_outstanding_orders(s) for s in ids):
        raise RuntimeError('nonflat or open orders at handoff; inspect account')
    log('LIVE READY', 'baseline',baseline,'budget',int(budget),'old orders reconciled')
    while time.monotonic()+12<end and not STOP.exists():
        options=[]
        for s in ('VRT','NVDA','AMD','MU'):
            if s not in stocks: continue
            q=book(x,s)
            if q and q[1]-q[0]>=1.99*stocks[s].tick_size: options.append((min(q[2:]),s))
        if not options: time.sleep(.5); continue
        _,s=max(options); n+=1; log('LIVE ROUND',n,s)
        if not run(Venue(x,s,stocks[s],end),emit=log):
            log('HALTED after unverified round; no resubmission');return
        pnl=x.get_pnl()
        if pnl is None or not math.isfinite(pnl): return
        peak=max(peak,pnl);log('FLAT',n,'account delta',round(pnl-baseline,4))
        if min(pnl-peak,pnl-baseline)<=-.5: log('LOSS STOP');return
        time.sleep(.5)
    log('CUTOFF OR STOP')

if __name__=='__main__':
    from optibook.synchronous_client import Exchange
    supervise(Exchange,budget=seconds_left(datetime.now(timezone.utc)),runner=resident)
