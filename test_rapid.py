import tempfile
import unittest
from pathlib import Path

from rapid_multi import session
from test_cross_ioc import X


class Test(unittest.TestCase):
    def test_two_cycles(self):
        with tempfile.TemporaryDirectory() as d:
            x = X([1, 1, 1, 1])
            self.assertEqual(
                session(x, Path(d), 2, ".02", 30), "attempt budget reached"
            )
            self.assertEqual(len(x.sent), 4)
            self.assertEqual(x.net, 0)

    def test_no_rearm_after_unmatched(self):
        with tempfile.TemporaryDirectory() as d:
            x = X([1, 0])
            self.assertEqual(session(x, Path(d), 3, ".02", 30), "unmatched long")
            self.assertRaises(FileExistsError, session, x, Path(d), 3, ".02", 30)
