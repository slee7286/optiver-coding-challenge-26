import unittest
from decimal import Decimal as D

from prep import Book, evaluate, room, slide_example, walk


def book(t, bid, ask, bv=10, av=10):
    return Book(t, 'NVDA', D(bid), D(ask), bv, av)


class PrepTests(unittest.TestCase):
    def test_risk_counts_same_side_resting_orders(self):
        self.assertEqual(room(75, 'bid', 8), 17)
        self.assertEqual(room(-75, 'ask', 8), 17)
        self.assertEqual(room(100, 'bid'), 0)
        self.assertEqual(room(-100, 'ask'), 0)
        with self.assertRaises(ValueError):
            room(0, 'buy')

    def test_walk_is_price_and_size_bounded(self):
        self.assertEqual(walk([(D('100.30'), 15), (D('100.40'), 50),
                               (D('100.50'), 110)], 100, 'bid', D('100.40')),
                         [(D('100.30'), 15), (D('100.40'), 50)])
        self.assertEqual(walk([(D('100.70'), 50), (D('100.60'), 76)],
                              100, 'ask', D('100.60')),
                         [(D('100.70'), 50), (D('100.60'), 50)])

    def test_slide_uses_cash_flows_and_executable_depth(self):
        self.assertEqual(slide_example(), (D('18.00'), D('23.00')))

    def test_research_charges_both_sides_and_does_not_overlap(self):
        rows = [book(0, '100', '101', 99, 1), book(1, '100', '101', 99, 1),
                book(2, '103', '104'), book(3, '103', '104')]
        r = evaluate(rows, signal='microprice', horizon_ms=2, tolerance_ms=0,
                     trigger_bps=D('10'), cost_bps=D('10'))
        self.assertEqual(r['trades'], 1)
        self.assertEqual(r['net_pnl'], D('1.796'))

    def test_research_skips_stale_future_books(self):
        rows = [book(0, '100', '101', 99, 1), book(50, '103', '104')]
        r = evaluate(rows, signal='microprice', horizon_ms=2, tolerance_ms=5,
                     trigger_bps=D('10'), cost_bps=D('0'))
        self.assertEqual(r['trades'], 0)
        self.assertEqual(r['missing_exit'], 1)

    def test_missing_exit_does_not_free_inventory_for_next_signal(self):
        rows = [book(0, '100', '101', 99, 1), book(1, '100', '101', 99, 1),
                book(11, '103', '104')]
        r = evaluate(rows, signal='microprice', horizon_ms=10, tolerance_ms=0,
                     trigger_bps=D('10'), cost_bps=D('0'))
        self.assertEqual((r['missing_exit'], r['trades']), (1, 0))

    def test_research_no_cross_instrument_exit(self):
        rows = [book(0, '100', '101', 99, 1), Book(2, 'AMD', D('103'), D('104'), 10, 10)]
        with self.assertRaises(ValueError):
            evaluate(rows, signal='microprice', horizon_ms=2, tolerance_ms=0,
                     trigger_bps=D('10'), cost_bps=D('0'))

    def test_research_short_covers_at_ask(self):
        rows = [book(0, '103', '104', 1, 99), book(2, '100', '101')]
        r = evaluate(rows, signal='microprice', horizon_ms=2, tolerance_ms=0,
                     trigger_bps=D('10'), cost_bps=D('0'))
        self.assertEqual(r['net_pnl'], D('2'))
