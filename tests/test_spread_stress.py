import unittest
from research import spread_stress as stress


class StressTest(unittest.TestCase):
    def test_model(self):
        # 0.30 full spread, 0.02 fee each side, +/-0.50 mid move
        cross, join = stress.required_success(.30, .50, .50, .02)
        self.assertAlmostEqual(cross, .84)
        self.assertAlmostEqual(join, .54)
        self.assertIsNone(stress.required_success(.60, .50, .50, 0)[0])

    def test_prior_book_only_and_no_fallback(self):
        books = [(1, .30), (3, None), (8, .50)]
        points = [1, 2, 3, 4, 11]
        self.assertEqual(stress.match(books, points, 2), ([.30, .30], {'no_prior': 1, 'invalid': 1, 'stale': 1}))
