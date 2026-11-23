"""One-attempt Optibook competition runner; sequential IOCs, NOT atomic."""

import argparse
import os
import time
from datetime import timezone
from decimal import Decimal as D
from math import isfinite
from pathlib import Path


def traditional(history, cost):
    """Long-only trend continuation or rebound after a dip; neither is arbitrage."""
    h = history[-8:]
    if len(h) != 8 or any(not 0 < b < a for _, b, a in h):
        return None
    if h[-1][0] - h[0][0] < 6 or any(
        not 0 < y[0] - z[0] <= 4 for z, y in zip(h, h[1:])
    ):
        return None
    m = [(a + b) / 2 for _, b, a in h]
    width = h[-1][2] - h[-1][1] + float(cost)
    if (
        m[-1] - m[0] > 3 * width
        and sum(y > z for z, y in zip(m, m[1:])) >= 5
        and m[-1] > m[-2]
    ):
        return "momentum"
    base = sum(m[:5]) / 5
    if (
        base - m[-1] > 3 * width
        and m[-1] - m[-2] > width
        and max(m[:5]) - min(m[:5]) <= 2 * width
    ):
        return "reversion"
    return None


def run(x, root, scans, cost, fallback=False):
    cost = D(cost)
    if not cost.is_finite() or cost < 0 or not 1 <= scans <= 1000:
        raise ValueError("invalid cost or scan count")
    latch = root / ".cross-ioc-attempt"
    stop = root / "STOP"
    end = time.monotonic() + 300

    def stopped():
        return stop.exists() or time.monotonic() >= end

    if latch.exists():
        raise FileExistsError("prior attempt: reconcile account before rearming")
    ids = list(x.get_instruments())
    symbols = sorted(x.get_tradable_instruments())

    def verify(s=None, n=0):
        if any(x.get_outstanding_orders(i) for i in ids) or {
            k: v for k, v in x.get_positions().items() if v
        } != ({s: n} if n else {}):
            raise RuntimeError("inventory or resting orders differ; reconcile manually")

    verify()

    def quote(s):
        book = x.get_last_price_book(s)
        if not book or not book.timestamp:
            return None
        ts = book.timestamp
        ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        if not 0 <= time.time() - ts.timestamp() <= 1:
            return None
        b = [
            l.price
            for l in book.bids
            if l.volume >= 1 and isfinite(l.price) and l.price > 0
        ]
        a = [
            l.price
            for l in book.asks
            if l.volume >= 1 and isfinite(l.price) and l.price > 0
        ]
        return (max(b), min(a)) if b and a else None

    def order(s, side, p, before):
        if stopped():
            raise RuntimeError("stopped before " + side + "; check inventory")
        r = x.insert_order(s, side=side, price=p, volume=1, order_type="ioc")
        if r.success is not True or type(r.order_id) is not int:
            raise RuntimeError("unconfirmed IOC; inspect account")
        time.sleep(0.4)
        f = x.poll_new_trades(s)
        sign = 1 if side == "bid" else -1
        if len(f) > 1 or any(
            t.order_id != r.order_id
            or t.instrument_id != s
            or t.side != side
            or t.volume != 1
            or not isfinite(t.price)
            or t.price <= 0
            or sign * (t.price - p) > 0
            for t in f
        ):
            raise RuntimeError("unexpected fill; inspect account")
        verify(s, before + sign * len(f))
        print(s, side, "order", r.order_id, "observed fills", len(f), flush=True)
        return bool(f)

    histories = {s: [] for s in symbols}
    for _ in range(scans):
        candidate = None
        for s in symbols:
            if stopped():
                return "stopped"
            q = quote(s)
            if fallback:
                h = histories[s]
                if q and q[0] < q[1]:
                    h.append((time.monotonic(), *q))
                    del h[:-8]
                    mode = traditional(h, cost)
                    if mode and candidate is None:
                        candidate = (s, q, mode)
                else:
                    h.clear()
            if q and D(str(q[0])) - D(str(q[1])) > cost:
                x.poll_new_trades(s)
                verify()
                q = quote(s)
                if q and D(str(q[0])) - D(str(q[1])) > cost:
                    with latch.open("x") as f:
                        f.write(f"{s} buy cap {q[1]} sell floor {q[0]}")
                        f.flush()
                        os.fsync(f.fileno())
                    if not order(s, "bid", q[1], 0):
                        return "no buy fill"
                    return "flat" if order(s, "ask", q[0], 1) else "unmatched long"
            time.sleep(0.2)
        if candidate and not stopped():
            s, old, mode = candidate
            x.poll_new_trades(s)
            verify()
            q = quote(s)
            if not q or q != old or stopped() or end - time.monotonic() < 10:
                continue
            with latch.open("x") as f:
                f.write(f"{s} {mode} buy cap {q[1]}")
                f.flush()
                os.fsync(f.fileno())
            print("fallback", mode, s, flush=True)
            if not order(s, "bid", q[1], 0):
                return "no buy fill"
            # One exit attempt on target, adverse move, or holding-time expiry.
            width = q[1] - q[0] + float(cost)
            until = time.monotonic() + 6
            while not stopped():
                fresh = quote(s)
                if fresh and (
                    fresh[0] >= q[1] + 2 * width
                    or fresh[0] <= q[1] - 2 * width
                    or time.monotonic() >= until
                ):
                    return "flat" if order(s, "ask", fresh[0], 1) else "unmatched long"
                if time.monotonic() >= until + 1:
                    break
                time.sleep(0.2)
            return "unmatched long"
    return "no eligible signal"


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--live", action="store_true", required=True)
    p.add_argument("--roundtrip-cost", required=True, type=D)
    p.add_argument("--scans", type=int, default=100)
    p.add_argument(
        "--fallback",
        action="store_true",
        help="enable experimental long-only momentum and rebound orders",
    )
    a = p.parse_args()
    root = Path(__file__).resolve().parent
    if (root / "STOP").exists() or (root / ".cross-ioc-attempt").exists():
        raise SystemExit("STOP or prior attempt present; not connecting")
    from optibook.synchronous_client import Exchange

    x = Exchange()
    x.connect()
    try:
        print("Connected; one-lot live sender enabled", flush=True)
        print(run(x, root, a.scans, a.roundtrip_cost, a.fallback), flush=True)
    finally:
        x.disconnect()
