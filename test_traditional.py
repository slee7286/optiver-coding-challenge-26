import unittest

from cross_ioc import traditional


class Signals(unittest.TestCase):
    def test_paths(self):
        for mids, expected in [
            ([10, 11, 12, 13, 14, 15, 16, 17], "momentum"),
            ([15, 15, 15, 15, 15, 15, 10, 11], "reversion"),
            ([10] * 8, None),
        ]:
            self.assertEqual(
                traditional(
                    [(2 * i, m - 0.05, m + 0.05) for i, m in enumerate(mids)], 0.02
                ),
                expected,
            )

    def test_gap(self):
        self.assertIsNone(
            traditional([(5 * i, i + 10, i + 10.1) for i in range(8)], 0.02)
        )
