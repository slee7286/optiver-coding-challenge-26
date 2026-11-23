import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as N

from cross_ioc import run


class X:
    def __init__(self, fills):
        self.seq = iter(fills)
        self.net = 0
        self.pending = []
        self.sent = []

    def get_positions(self):
        return {"A": self.net}

    def get_outstanding_orders(self, s):
        return {}

    def get_tradable_instruments(self):
        return {"A": None}

    def get_instruments(self):
        return {"A": None}

    def get_last_price_book(self, s):
        return N(
            timestamp=datetime.now(timezone.utc),
            bids=[N(price=101, volume=1)],
            asks=[N(price=100, volume=1)],
        )

    def insert_order(self, s, **kw):
        self.sent.append(kw)
        i = len(self.sent)
        side = kw["side"]
        fill = next(self.seq)
        if fill:
            self.net += 1 if side == "bid" else -1
            self.pending = [
                N(instrument_id=s, side=side, order_id=i, price=kw["price"], volume=1)
            ]
        return N(success=True, order_id=i)

    def poll_new_trades(self, s):
        v = self.pending
        self.pending = []
        return v


class Case(unittest.TestCase):
    def test_paths(self):
        for seq, expected, pos in [
            ([1, 1], "flat", 0),
            ([0], "no buy fill", 0),
            ([1, 0], "unmatched long", 1),
        ]:
            with self.subTest(seq=seq), tempfile.TemporaryDirectory() as t:
                x = X(seq)
                self.assertEqual(run(x, Path(t), 1, ".02"), expected)
                self.assertEqual(x.net, pos)
                self.assertEqual(len(x.sent), len(seq))
                self.assertTrue(
                    all(o["order_type"] == "ioc" and o["volume"] == 1 for o in x.sent)
                )
                self.assertRaises(FileExistsError, run, x, Path(t), 1, ".02")
