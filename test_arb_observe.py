import unittest
from types import SimpleNamespace as N

from arb_observe import observe


class Venue:
    def __init__(self):
        self.events = []

    def connect(self):
        self.events.append("connect")

    def disconnect(self):
        self.events.append("disconnect")

    def get_tradable_instruments(self):
        return {"A": None}

    def get_last_price_book(self, s):
        return N(bids=[N(price=101, volume=1)], asks=[N(price=100, volume=1)])


class Tests(unittest.TestCase):
    def test_cross_reported_without_legging(self):
        v = Venue()
        self.assertEqual(observe(v, 1), 1)
        self.assertEqual(v.events, ["connect", "disconnect"])

    def test_read_failure_disconnects(self):
        v = Venue()
        v.get_last_price_book = lambda s: 1 / 0
        with self.assertRaises(ZeroDivisionError):
            observe(v, 1)
        self.assertEqual(v.events, ["connect", "disconnect"])
