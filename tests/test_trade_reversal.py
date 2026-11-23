import unittest
from research.trade_reversal import replay


class TestReplay(unittest.TestCase):
    def test_sell_print_buys_ask_then_sells_bid_with_latency(self):
        # Quotes exactly at order arrival are not known to precede it.
        key = ('session', 'A')
        books = {key: [(0, 99, 100), (100_000_000, 88, 89),
                       (1_190_000_000, 102, 103)]}
        out = replay([(0, key, 50, 'ask'), (1, key, 50, 'bid')], books, 50, 2_000_000_000)
        self.assertEqual(out['closed'], 1)
        self.assertAlmostEqual(out['net'], 1.9798)
