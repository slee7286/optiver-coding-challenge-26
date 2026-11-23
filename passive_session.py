"""Exclusive Team 011 simulator practice: bounded passive spread-capture rounds."""
import time
from datetime import datetime, timezone
from continuous_practice import STOP, log, seconds_left
from live_pilot import run
from priority_practice import supervise


def rounds(x, budget):
    end = time.monotonic() + budget
    initial = x.get_pnl()
    if initial is None: raise RuntimeError('missing baseline PnL')
    for n in range(12):
        if STOP.exists() or time.monotonic()+12 >= end: break
        log('PASSIVE ROUND', n+1, 'baseline PnL', initial)
        if not run(x, emit=log): return
        pnl=x.get_pnl()
        if pnl is None: return
        log('ROUND COMPLETE',n+1,'account PnL change',round(pnl-initial,4))
        if pnl-initial <= -.5: break
        time.sleep(2)
    log('PASSIVE SESSION FINISHED')


if __name__=='__main__':
    from optibook.synchronous_client import Exchange
    supervise(Exchange,budget=min(120,seconds_left(datetime.now(timezone.utc))),runner=rounds)
