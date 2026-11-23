import unittest

import volume_challenger as v


class VolumeReplayTests(unittest.TestCase):
    def test_passive_round_trip_requires_strict_through_after_arrival(self):
        # Millisecond fixture; money is in integer cents, one lot.
        events = [v.book(0, 'X', 1000, 3, 1010, 1),
                  v.trade(100, 'X', 'ask', 999),  # exact arrival
                  v.trade(101, 'X', 'ask', 1000),  # mere touch
                  v.trade(102, 'X', 'bid', 999),   # wrong side
                  v.trade(103, 'X', 'ask', 999),
                  v.trade(200, 'X', 'bid', 1011),  # exit still in flight
                  v.trade(204, 'X', 'bid', 1010),  # mere touch
                  v.trade(205, 'X', 'bid', 1011)]
        partial = v.replay(events[:4], 102*v.MS)
        self.assertEqual(partial['entry_fills'], 0)
        r = v.replay(events, 205*v.MS)
        self.assertEqual((r['entry_fills'], r['passive_exits'], r['net_cents']), (1, 1, 8))

    def test_stale_unwind_waits_for_executable_size_and_costs_spread(self):
        e = [v.book(0, 'X', 1000, 1, 1010, 3), v.trade(101, 'X', 'bid', 1011)]
        self.assertEqual(v.replay(e, 6000*v.MS)['incomplete'], 1)
        e += [v.book(6100, 'X', 1010, 3, 1020, 0), v.book(6200, 'X', 1010, 3, 1020, 1)]
        r = v.replay(e, 6200*v.MS)
        self.assertEqual((r['unwinds'], r['net_cents'], r['incomplete']), (1, -12, 0))

    def test_marketable_at_arrival_is_rejected(self):
        e = [v.book(0, 'X', 1000, 3, 1010, 1), v.book(90, 'X', 980, 1, 990, 1),
             v.trade(102, 'X', 'ask', 980)]
        r = v.replay(e, 102*v.MS)
        self.assertEqual((r['entry_rejects'], r['entry_fills']), (1, 0))

    def test_split_does_not_carry_orders_or_inventory(self):
        from pathlib import Path
        from unittest.mock import patch
        events = [v.book(0, 'X', 1000, 3, 1010, 1), v.trade(101, 'X', 'ask', 999),
                  v.trade(201, 'X', 'bid', 1011), v.trade(300, 'X', 'bid', 1011)]
        with patch.object(v, 'load', return_value=(events, {})):
            r = v.evaluate(Path('.'))
        self.assertEqual(r['holdout_start_ns'], 200*v.MS)
        self.assertEqual(r['development']['incomplete'], 1)
        self.assertEqual((r['holdout']['prints'], r['holdout']['completed']), (2, 0))
        self.assertEqual(r['verdict'], 'NO_GO')

    def test_print_at_expiry_cannot_fill(self):
        e = [v.book(0, 'X', 1000, 3, 1010, 1), v.trade(5100, 'X', 'ask', 999)]
        r = v.replay(e, 5100*v.MS)
        self.assertEqual((r['expired'], r['entry_fills']), (1, 0))

    def test_balanced_book_never_quotes(self):
        self.assertEqual(v.replay([v.book(0, 'X', 1000, 2, 1010, 2)], v.MS)['signals'], 0)
