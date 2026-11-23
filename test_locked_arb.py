import unittest

from locked_arb import evaluate


class Test(unittest.TestCase):
    def test_positive_control(self):
        self.assertEqual(evaluate([[102, 3]], [[100, 2]], True, 1), (1, 1))

    def test_nonatomic(self):
        self.assertEqual(evaluate([[102, 3]], [[100, 2]], False, 1), (0, 0))

    def test_too_costly(self):
        self.assertEqual(evaluate([[102, 3]], [[100, 2]], True, 2), (0, 0))

    def test_no_depth(self):
        self.assertEqual(evaluate([[102, 0]], [[100, 2]], True, 1), (0, 0))
