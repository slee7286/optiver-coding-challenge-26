"""Multi-cycle Optibook competition runner; sequential IOCs, NOT atomic."""

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


def book_signal(history, cost):
    """Rank directional setups by observed move / crossing hurdle, not expected PnL."""
    h = history[-4:]
    if len(h) < 4 or any(not 0 < v[0] - u[0] <= 4 for u, v in zip(h, h[1:])):
        return None
    mid = [(b + a) / 2 for _, b, a, _, _ in h]
    imb = [(bv - av) / (bv + av) for _, _, _, bv, av in h]
    _, b, a, bv, av = h[-1]
    width = a - b + float(cost)
    move = mid[-1] - mid[0]
    if width <= 0:
        return None
    if min(imb[-2:]) >= 0.25 and mid[-1] >= mid[-2] and move > 1.5 * width:
        return move / width * imb[-1], "pressure"
    drop = max(mid[:2]) - mid[-1]
    prior = h[-2]
    if (
        drop > 2 * width
        and b == prior[1]
        and bv >= 2 * prior[3]
        and imb[-2] < 0 < 0.25 <= imb[-1]
    ):
        return drop / width * imb[-1], "refill"
    return None


def run(x, root, scans, cost, fallback=False, deadline=None, histories=None):
    cost = D(cost)
    if not cost.is_finite() or cost < 0 or not 1 <= scans <= 1000:
        raise ValueError("invalid cost or scan count")
    latch = root / ".cross-ioc-attempt"
    stop = root / "STOP"
    end = time.monotonic() + 300 if deadline is None else deadline

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
        if not b or not a:
            return None
        bid, ask = max(b), min(a)
        return (
            bid,
            ask,
            ts.timestamp(),
            sum(l.volume for l in book.bids if l.price == bid),
            sum(l.volume for l in book.asks if l.price == ask),
        )

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

    histories = {s: [] for s in symbols} if histories is None else histories
    for s in symbols:
        histories.setdefault(s, [])
    for _ in range(scans):
        if _ % 10 == 0:
            print("Scanning pressure, refill and crosses; sweep", _ + 1, flush=True)
        candidate = None
        for s in symbols:
            if stopped():
                return "stopped"
            q = quote(s)
            if fallback:
                h = histories[s]
                if q and q[0] < q[1]:
                    if h and q[2] < h[-1][0]:
                        h.clear()
                    if not h or q[2] > h[-1][0]:
                        h.append((q[2], q[0], q[1], q[3], q[4]))
                        del h[:-4]
                    signal = book_signal(h, cost)
                    if signal and (candidate is None or signal[0] > candidate[0]):
                        candidate = (signal[0], s, q, signal[1])
                else:
                    h.clear()
            if q and D(str(q[0])) - D(str(q[1])) > cost:
                x.poll_new_trades(s)
                verify()
                q = quote(s)
                if q and D(str(q[0])) - D(str(q[1])) > cost:
                    print("cross", s, flush=True)
                    with latch.open("x") as f:
                        f.write(f"{s} buy cap {q[1]} sell floor {q[0]}")
                        f.flush()
                        os.fsync(f.fileno())
                    if not order(s, "bid", q[1], 0):
                        return "no buy fill"
                    return "flat" if order(s, "ask", q[0], 1) else "unmatched long"
        # Sample the entire basket per sweep, not one symbol per sleep.
        time.sleep(0.2)
        if candidate and not stopped():
            _, s, old, mode = candidate
            x.poll_new_trades(s)
            verify()
            q = quote(s)
            if (
                not q
                or q[:2] != old[:2]
                or q[3] * old[4] < old[3] * q[4]
                or stopped()
                or end - time.monotonic() < 10
            ):
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


def session(x, root, attempts, cost, seconds):
    if not 1 <= attempts <= 30 or not 1 <= seconds <= 300:
        raise ValueError("attempts 1..30, seconds 1..300")
    lock = root / ".rapid-session"
    latch = root / ".cross-ioc-attempt"
    deadline = time.monotonic() + seconds
    if latch.exists():
        raise FileExistsError("prior attempt: reconcile before restart")
    lock.mkdir()
    histories = {}
    try:
        for i in range(attempts):
            result = run(x, root, 1000, cost, True, deadline, histories)
            print("cycle", i + 1, result, flush=True)
            if result not in ("flat", "no buy fill"):
                return result
            if any(x.get_positions().values()) or any(
                x.get_outstanding_orders(s) for s in x.get_instruments()
            ):
                raise RuntimeError("not confirmed flat; no rearm")
            if (
                i + 1 < attempts
                and time.monotonic() < deadline
                and not (root / "STOP").exists()
            ):
                latch.unlink()
                time.sleep(0.25)
            else:
                return "attempt budget reached" if i + 1 == attempts else "stopped"
    finally:
        lock.rmdir()


def clip_to_target(current, target, depth):
    delta = target - current
    return (1 if delta > 0 else -1) * min(abs(delta), int(depth))


def portfolio(x, root, attempts, cost, seconds, limit):
    """Directional inventory persists between signals and after shutdown."""
    cost = D(cost)
    if not (
        limit >= 1
        and 1 <= attempts <= 1000
        and 1 <= seconds <= 300
        and cost.is_finite()
        and cost >= 0
    ):
        raise ValueError("invalid portfolio controls")
    deadline = time.monotonic() + seconds
    lock = root / ".rapid-session"
    latch = root / ".cross-ioc-attempt"
    stop = root / "STOP"

    def ended():
        return stop.exists() or time.monotonic() >= deadline

    def positions():
        return {s: v for s, v in x.get_positions().items() if v}

    symbols = sorted(x.get_tradable_instruments())
    ids = list(x.get_instruments())
    p = positions()
    last = {}
    used = 0
    previous = {}
    if not symbols or any(
        s not in symbols or type(v) != int or abs(v) > limit for s, v in p.items()
    ):
        raise RuntimeError("inventory outside configuration")

    def verify():
        if p != positions() or any(x.get_outstanding_orders(s) for s in ids):
            raise RuntimeError("account mismatch; reconcile manually")

    def touch(s):
        b = x.get_last_price_book(s)
        if not b or not b.timestamp or not b.asks or not b.bids:
            return None
        t = b.timestamp
        t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        t = t.timestamp()
        a = b.asks[0]
        v = b.bids[0]
        if not (
            0 <= time.time() - t <= 1
            and all(isfinite(k) and k > 0 for k in (a.price, v.price))
            and min(a.volume, v.volume) >= 1
        ):
            return None
        return t, v.price, a.price, v.volume, a.volume

    def send(s, n, price):
        verify()
        side = "bid" if n > 0 else "ask"
        sign = 1 if n > 0 else -1
        if ended():
            raise RuntimeError("stop/deadline; inspect inventory")
        r = x.insert_order(s, side=side, order_type="ioc", volume=abs(n), price=price)
        if r.success is not True or type(r.order_id) != int:
            raise RuntimeError("IOC not confirmed; no retry")
        time.sleep(0.4)
        fs = x.poll_new_trades(s)
        total = sum(f.volume for f in fs)
        if total > abs(n) or any(
            f.order_id != r.order_id
            or f.instrument_id != s
            or f.side != side
            or type(f.volume) != int
            or f.volume <= 0
            or not isfinite(f.price)
            or f.price <= 0
            or sign * (f.price - price) > 0
            for f in fs
        ):
            raise RuntimeError("unexpected fill")
        val = p.get(s, 0) + sign * total
        if val:
            p[s] = val
        else:
            p.pop(s, None)
        verify()
        print(s, side, "requested", abs(n), "filled", total, "positions", p, flush=True)
        return total

    if latch.exists():
        raise FileExistsError("prior attempt; reconcile")
    lock.mkdir()
    try:
        verify()
        for s in symbols:
            x.poll_new_trades(s)
        while not ended() and used < attempts:
            candidates = []
            for s in symbols:
                q = touch(s)
                if not q:
                    continue
                t, b, a, bv, av = q
                mid = (a + b) / 2
                old = previous.get(s)
                previous[s] = (t, mid)
                if t <= last.get(s, 0):
                    continue
                cross = D(str(b)) - D(str(a)) > cost
                imb = (bv - av) / (bv + av)
                target = limit if cross or imb > 0 else -limit
                if not cross and not (
                    b < a
                    and abs(imb) >= 0.15
                    and old
                    and 0 < t - old[0] <= 2
                    and (mid - old[1]) * imb >= 0
                ):
                    continue
                n = clip_to_target(
                    p.get(s, 0), target, av if target > p.get(s, 0) else bv
                )
                if cross:
                    n = min(n, bv, 1)
                if n:
                    candidates.append(
                        (
                            (cross, s not in p, -abs(p.get(s, 0)), abs(imb)),
                            s,
                            q,
                            n,
                            cross,
                        )
                    )
            if candidates:
                _, s, q, n, cross = max(candidates)
                fresh = touch(s)
                if fresh == q and not ended():
                    verify()
                    if x.poll_new_trades(s):
                        raise RuntimeError("unexpected account activity")
                    with latch.open("x") as f:
                        f.write(f"{s} signed IOC {n} before {p}")
                        f.flush()
                        os.fsync(f.fileno())
                    used += 1
                    last[s] = q[0]
                    filled = send(s, n, q[2] if n > 0 else q[1])
                    if cross and filled and send(s, -filled, q[1]) != filled:
                        return "unmatched cross; reconcile"
                    latch.unlink()
            time.sleep(0.2)
        return f"attempts={used} ending positions={p}; not flattened"
    finally:
        lock.rmdir()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--live", action="store_true", required=True)
    p.add_argument("--roundtrip-cost", required=True, type=D)
    p.add_argument("--seconds", type=int, default=180)
    p.add_argument("--attempts", type=int, default=5)
    p.add_argument(
        "--portfolio-limit",
        type=int,
        help="enable multi-symbol inventory; supply verified round limit in lots per symbol",
    )
    a = p.parse_args()
    root = Path(__file__).resolve().parent
    if any(
        (root / n).exists() for n in ("STOP", ".rapid-session", ".cross-ioc-attempt")
    ):
        raise SystemExit(
            "STOP or previous session/attempt marker; reconcile before connecting"
        )
    from optibook.synchronous_client import Exchange

    x = Exchange()
    x.connect()
    try:
        if a.portfolio_limit is None:
            print(session(x, root, a.attempts, a.roundtrip_cost, a.seconds), flush=True)
        else:
            print("CONNECTED: multi-symbol inventory", flush=True)
            print(
                portfolio(
                    x, root, a.attempts, a.roundtrip_cost, a.seconds, a.portfolio_limit
                ),
                flush=True,
            )
    finally:
        x.disconnect()
