import unittest
from decimal import Decimal

from arb_observe import observe
from test_arb_observe import Venue


class DispatchTests(unittest.TestCase):
    def test_one_package_only(self):
        calls = []
        observe(
            Venue(),
            3,
            atomic_submit=lambda **kw: calls.append(kw),
            max_cost=Decimal(".02"),
        )
        self.assertEqual(
            calls, [dict(symbol="A", quantity=1, max_buy=100, min_sell=101)]
        )

    def test_costs_block(self):
        observe(
            Venue(),
            1,
            atomic_submit=lambda **kw: self.fail("sent"),
            max_cost=Decimal("1"),
        )

    def test_uncertain_submission_is_not_retried(self):
        calls = []

        def submit(**kw):
            calls.append(kw)
            raise TimeoutError

        v = Venue()
        with self.assertRaises(TimeoutError):
            observe(v, 3, atomic_submit=submit, max_cost=Decimal("0"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(v.events[-1], "disconnect")
