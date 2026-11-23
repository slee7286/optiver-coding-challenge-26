import unittest

from basket_hold_v2 import entry_book


class EntryFilter(unittest.TestCase):
    def test_does_not_pay_three_ticks(self):
        q = (100, 100.3, 20, 20)
        self.assertIsNone(entry_book(q, 0.1))

    def test_two_ticks_without_changing_size(self):
        q = (100, 100.2, 20, 20)
        self.assertEqual(entry_book(q, 0.1), q)

    def test_empty_or_crossed_not_used(self):
        self.assertIsNone(entry_book(None, 0.1))
        self.assertIsNone(entry_book((101, 100, 1, 1), 0.1))
