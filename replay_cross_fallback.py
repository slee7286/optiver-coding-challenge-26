"""Replay the actual one-attempt runner with 100ms IOC latency and recorded depth."""

import bisect
import csv
import gzip
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as N
from unittest.mock import patch

import cross_ioc


class Clock:
    def __init__(self, start):
        self.now = start

    def time(self):
        return self.now

    monotonic = time

    def sleep(self, dt):
        self.now += dt


class Tape:
    def __init__(self, data, clock, start, end):
        self.d = data
        self.clock = clock
        self.start = start
        self.end = end
        self.pos = {}
        self.pending = []
        self.orders = 0
        self.fills = 0
        self.cash = 0

    def get_positions(self):
        return self.pos

    def get_instruments(self):
        return self.d

    get_tradable_instruments = get_instruments

    def get_outstanding_orders(self, s):
        return {}

    def get_last_price_book(self, s):
        ts, books = self.d[s]
        i = bisect.bisect_right(ts, self.clock.now) - 1
        if i < 0 or ts[i] < self.start or self.clock.now >= self.end:
            return None
        return books[i]

    def poll_new_trades(self, s):
        v = self.pending
        self.pending = []
        return v

    def insert_order(self, s, *, side, price, volume, order_type):
        self.clock.sleep(0.1)
        self.orders += 1
        b = self.get_last_price_book(s)
        buy = side == "bid"
        sign = 1 if buy else -1
        levels = (
            (b.asks if buy else b.bids)
            if b and self.clock.now - b.timestamp.timestamp() <= 1
            else []
        )
        executable = [
            l.price for l in levels if l.volume >= 1 and sign * (l.price - price) <= 0
        ]
        self.pending = []
        if executable:
            p = (min if buy else max)(executable)
            self.pos[s] = self.pos.get(s, 0) + sign
            self.cash -= sign * p + 0.01
            self.fills += 1
            self.pending = [
                N(instrument_id=s, side=side, price=p, volume=1, order_id=self.orders)
            ]
        return N(success=True, order_id=self.orders)


def main(path):
    d = {}
    with gzip.open(path, "rt") as f:
        for r in csv.DictReader(f):
            t = int(r["timestamp_ns"]) / 1e9
            s = r["instrument_id"]
            a, b = d.setdefault(s, ([], []))
            a.append(t)
            b.append(
                N(
                    timestamp=datetime.fromtimestamp(t, timezone.utc),
                    **{
                        k: [N(price=p, volume=v) for p, v in json.loads(r[k + "_json"])]
                        for k in ("asks", "bids")
                    },
                )
            )
    assert all(ts == sorted(ts) for ts, _ in d.values())
    lo = min(a[0] for a, _ in d.values())
    hi = max(a[-1] for a, _ in d.values())
    cut = lo + (hi - lo) * 2 / 3
    for label, start, end in [("early", lo, cut), ("later", cut, hi)]:
        clock = Clock(start)
        v = Tape(d, clock, start, end)
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(cross_ioc, "time", clock),
        ):
            result = cross_ioc.run(
                v, Path(tmp), int((end - start) / (len(d) * 0.2)), ".02", True
            )
        print(
            label,
            result,
            "orders",
            v.orders,
            "fills",
            v.fills,
            "positions",
            v.pos,
            "net if flat",
            None if any(v.pos.values()) else round(v.cash, 4),
        )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
