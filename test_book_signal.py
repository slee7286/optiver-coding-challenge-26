import unittest

from rapid_multi import book_signal


class Signals(unittest.TestCase):
    def test_continuation(self):
        rows = [(i, 100 + i * 0.2, 100.1 + i * 0.2, 80, 20) for i in range(4)]
        self.assertEqual(book_signal(rows, 0.02)[1], "pressure")
        self.assertIsNone(book_signal(rows, 0.99))

    def test_refill(self):
        rows = [
            (1, 101, 101.1, 50, 50),
            (2, 101, 101.1, 50, 50),
            (3, 100, 100.1, 10, 60),
            (4, 100, 100.1, 120, 40),
        ]
        self.assertEqual(book_signal(rows, 0.02)[1], "refill")
        rows[-1] = (3, 100, 100.1, 120, 40)
        self.assertIsNone(book_signal(rows, 0.02))
