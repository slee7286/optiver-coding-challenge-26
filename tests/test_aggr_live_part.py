import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS


class Clock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


class Fake:
    def __init__(self, c):
        self.c = c
        self.pos = {"AMD": 17}
        self.orders = []
        self.fills = []
        self.pnl = 50
        self.connections = 0
        self.tick_calls = 0
        self.failure = False
        self.price_move = False
        self.books = 0

    def connect(self):
        self.connections += 1

    def disconnect(self):
        pass

    def is_connected(self):
        return True

    def get_instruments(self):
        return {"AMD": NS(tick_size=0.1), "NVDA": NS(tick_size=0.1)}

    get_tradable_instruments = get_instruments

    def get_outstanding_orders(self, s):
        return {}

    def get_positions(self):
        return dict(self.pos)

    def get_pnl(self):
        return self.pnl

    def poll_new_trade_ticks(self, s):
        return [NS(aggressor_side="bid", volume=3)]

    def get_last_price_book(self, s):
        self.books += 1
        return NS(
            timestamp=datetime.fromtimestamp(self.c.t, timezone.utc),
            bids=[NS(price=10.0, volume=9)],
            asks=[
                NS(price=10.1 + (self.books % 2 if self.price_move else 0), volume=9)
            ],
        )

    def insert_order(self, s, **kw):
        self.orders.append((self.c.t, s, kw))
        oid = len(self.orders)
        if self.failure:
            raise OSError("unknown")
        n = min(2, kw["volume"]) if oid % 2 else 0
        self.fills = (
            [
                NS(
                    instrument_id=s,
                    side=kw["side"],
                    order_id=oid,
                    trade_id=oid,
                    volume=n,
                    price=kw["price"],
                )
            ]
            if n
            else []
        )
        self.pos[s] = self.pos.get(s, 0) + n * (1 if kw["side"] == "bid" else -1)
        return NS(success=True, order_id=oid)

    def poll_new_trades(self, s):
        result = self.fills
        self.fills = []
        return result


class AdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.c = Clock()
        self.x = Fake(self.c)

    def go(self, signal, minutes=0.12):
        import aggr_live_part as m

        return m._aggr_run(
            minutes,
            signal,
            lambda: self.x,
            self.root,
            self.c.now,
            self.c.now,
            self.c.sleep,
        )

    def test_signed_clips_and_interval_data(self):
        def sig(h, tick):
            self.assertEqual(len(h[-1]), 7)
            self.assertEqual(h[-1][-2:], (3, 0))
            self.assertEqual(tick, 0.1)
            return -20

        self.go(sig)
        self.assertGreater(len(self.x.orders), 2)
        for t, s, k in self.x.orders:
            self.assertEqual(
                (k["side"], k["order_type"], k["price"]), ("ask", "ioc", 10.0)
            )
            self.assertTrue(0 < k["volume"] <= 5)
        self.assertTrue(
            all(b[0] - a[0] >= 1 for a, b in zip(self.x.orders, self.x.orders[1:]))
        )

    def test_none_hold_and_persistent_brake(self):
        self.go(lambda h, t: None)
        self.assertEqual(self.x.orders, [])
        self.x.pnl -= 5
        self.go(lambda h, t: 20)
        self.x.pnl += 20
        self.go(lambda h, t: 20)
        self.assertEqual(self.x.orders, [])
        self.assertEqual(self.x.pos, {"AMD": 17})

    def test_ambiguous_never_retried(self):
        self.x.failure = True
        with self.assertRaises(OSError):
            self.go(lambda h, t: 20)
        with self.assertRaises(RuntimeError):
            self.go(lambda h, t: 20)
        self.assertEqual(len(self.x.orders), 1)
        self.assertEqual(self.x.connections, 1)

    def test_gross_inherited_and_room(self):
        self.x.pos = {"AMD": 20, "MU": 20, "AVGO": 20, "VRT": 18}
        self.go(lambda h, t: 20)
        self.assertEqual(len(self.x.orders), 1)
        self.assertEqual(self.x.orders[0][2]["volume"], 2)
        self.assertEqual(sum(map(abs, self.x.pos.values())), 80)

    def test_invalid_start(self):
        self.x.pos = {"AMD": 21}
        with self.assertRaises(RuntimeError):
            self.go(lambda h, t: -20)
        self.assertFalse(self.x.orders)

    def test_resting_orders(self):
        self.x.get_outstanding_orders = lambda s: {1: object()}
        with self.assertRaises(RuntimeError):
            self.go(lambda h, t: 20)
        self.assertFalse(self.x.orders)

    def test_stale_and_worsened_quote(self):
        self.x.get_tradable_instruments = lambda: {"AMD": NS(tick_size=0.1)}
        original = self.x.get_last_price_book
        queried = [0]
        self.x.pos = {}

        def worsens(s):
            b = original(s)
            queried[0] += 1
            # first sample has no submission; subsequent decision/recheck alternate.
            if queried[0] >= 3 and queried[0] % 2:
                b.asks[0].price += 1
            return b

        self.x.get_last_price_book = worsens
        self.go(lambda h, t: 20)
        self.assertFalse(self.x.orders)

        def stale(s):
            b = original(s)
            b.timestamp = datetime.fromtimestamp(0, timezone.utc)
            return b

        self.x.get_last_price_book = stale
        self.go(lambda h, t: 20)
        self.assertFalse(self.x.orders)

    def test_stop_before_connection(self):
        (self.root / "STOP").touch()
        self.go(lambda h, t: 20)
        self.assertEqual(self.x.connections, 0)
