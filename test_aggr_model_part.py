import unittest

import aggr_model_part as m


class AggressiveTests(unittest.TestCase):
    def trend(self, sign=1, spread=0.1):
        return [
            (
                t,
                100 + sign * t * 0.1,
                100 + sign * t * 0.1 + spread,
                100,
                100,
                10 if sign > 0 else 0,
                10 if sign < 0 else 0,
            )
            for t in range(40)
        ]

    def test_confirmed_trends_and_cost(self):
        self.assertEqual(m.target(self.trend(), 0.1), 20)
        self.assertEqual(m.target(self.trend(-1), 0.1), -20)
        self.assertIsNone(m.target(self.trend(spread=9), 0.1))
        self.assertIsNone(m.target([], 0.1))

    def test_latency_terminal_capacity(self):
        frames = [(s, {"A": (s, 10 + s, 11 + s, 20, 20, 0, 0)}) for s in range(30)]
        r = m._ag_replay(frames, lambda h, t: 20, 0.01)
        self.assertEqual(r["first_fill_sec"], 1)
        self.assertEqual(r["residual"], {})
        self.assertEqual(r["units"], 40)
        self.assertEqual(r["peak_gross"], 20)
        self.assertEqual(r["fees"], 0.40)

    def test_frame_boundary_and_invalidation(self):
        n = 10**9
        q = (10, 11, 2, 3)
        books = [(0, "A", q), (n + 1, "A", None), (2 * n + 1, "A", q)]
        trades = [(n, "A", 0, 4), (n + 1, "A", 1, 7)]
        f = dict(m._ag_frames(books, trades, 0, 7 * n))
        self.assertEqual(f[1]["A"][5:], (4, 0))
        self.assertEqual(f[2], {})
        self.assertEqual(f[3]["A"][5:], (0, 0))
        self.assertEqual(f[5], {})

    def test_portfolio_cap_and_shared_order_budget(self):
        frames = [
            (s, {k: (s, 10, 11, 100, 100, 0, 0) for k in "ABCDEFGHI"})
            for s in range(70)
        ]
        r = m._ag_replay(frames, lambda h, t: 20)
        self.assertEqual((r["peak_gross"], r["fills"], r["units"]), (80, 32, 160))
        self.assertEqual(r["net_executable"], -81.6)
        self.assertEqual(r["residual"], {})

    def test_stale_book_not_liquidated(self):
        frames = [
            (s, {"A": (s, 10, 11, 10, 10, 0, 0)} if s < 4 else {}) for s in range(30)
        ]
        r = m._ag_replay(frames, lambda h, t: 20, 0.01)
        self.assertEqual(r["residual"], {"A": 15})
        self.assertIsNone(r["net_executable"])
