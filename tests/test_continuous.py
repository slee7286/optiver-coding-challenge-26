import unittest
from datetime import datetime, timezone
from continuous_practice import eligible, seconds_left, candidate


class Gates(unittest.TestCase):
    def test_promotion_requires_two_consecutive_qualified_windows(self):
        c = dict(symbol='AMD', reverse=1, hold_ns=10**9, enabled=True, train_trades=18, train_net=2, holdout_trades=12, holdout_net=1, holdout_unresolved=False)
        self.assertFalse(eligible(c,None)); self.assertTrue(eligible(c,candidate(c)))
        for key,value in [('holdout_net',0),('holdout_trades',7),('enabled',False),('holdout_unresolved',True)]:
            self.assertFalse(eligible(dict(c,**{key:value}),candidate(c)))

    def test_cutoff(self):
        self.assertEqual(seconds_left(datetime(2026,11,23,20,1,tzinfo=timezone.utc)),0)
        self.assertEqual(seconds_left(datetime(2026,11,23,18,30,tzinfo=timezone.utc)),3600)
        self.assertEqual(seconds_left(datetime(2026,11,23,19,59,tzinfo=timezone.utc)),60)
