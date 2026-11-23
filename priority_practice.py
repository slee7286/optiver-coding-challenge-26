"""Run one supervised practice session. Reconnect with backoff only before first order."""
import time
from datetime import datetime, timezone
from continuous_practice import STOP, inventory, log, run, seconds_left


class Tracked:
    def __init__(self, exchange):
        self.exchange = exchange
        self.submitted = False

    def __getattr__(self, name):
        return getattr(self.exchange, name)

    def insert_order(self, *args, **kwargs):
        self.submitted = True  # even a dropped acknowledgement consumes the safety latch
        return self.exchange.insert_order(*args, **kwargs)


def supervise(factory, *, budget, clock=time.monotonic, sleep=time.sleep, runner=run):
    deadline = clock() + budget
    failures = 0
    while deadline-clock() > 10 and not STOP.exists():
        x = Tracked(factory()); connected = False
        try:
            x.connect(); connected = True
            runner(x, deadline-clock()); return
        except Exception as exc:
            reason = str(exc)
            lost = not x.is_connected()
            log('SESSION STOP',type(exc).__name__,reason[:180], 'connected',not lost,'order attempted',x.submitted)
            if x.submitted or (connected and not lost) or STOP.exists():
                log('NO RETRY: inspect team state'); return
        finally:
            if x.is_connected():
                try: log('POSITIONS BEFORE DISCONNECT',inventory(x))
                finally: x.disconnect()
        failures += 1
        delay=min(60, 5 * 2**min(failures-1,4))
        log('RETRY IN',delay,'seconds; next session requires flat account')
        sleep(min(delay,max(0, deadline-clock())))


if __name__=='__main__':
    from optibook.synchronous_client import Exchange
    supervise(Exchange,budget=seconds_left(datetime.now(timezone.utc)))
