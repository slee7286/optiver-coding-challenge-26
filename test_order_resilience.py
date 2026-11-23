import unittest


class ReplayTests(unittest.TestCase):
    def test_limit_lifecycle_not_ioc_or_zero_cancel_field(self):
        from order_resilience import lifecycle

        live = {}

        def event(a, n, typ="limit"):
            return lifecycle(live, ("1", "bid", a, typ, n, 100.0))

        self.assertEqual(event("new", 100), (0, 0, 100))
        self.assertEqual(event("partialFill", 60), (0, 40, 0))
        self.assertEqual(event("amended", 70), (0, 0, 10))
        self.assertEqual(event("cancelled", 0), (70, 0, 0))
        self.assertEqual(event("new", 100, "ioc"), (0, 0, 0))
        self.assertEqual(event("fill", 0, "ioc"), (0, 0, 0))
        self.assertFalse(live)

    def test_only_prior_trade_and_order_flow_can_affect_shock(self):
        from collections import Counter

        from order_resilience import MS, Instrument, Policy

        def quotes(b):
            return (
                "pricebooks",
                {"bids_json": f"[[{b},10]]", "asks_json": f"[[{b + 1},10]]"},
            )

        def order(a, v):
            return (
                "orders",
                dict(
                    order_id="x",
                    side="ask",
                    action=a,
                    order_type="limit",
                    volume=v,
                    price=101,
                ),
            )

        trade = ("trades", dict(aggressor_side="bid", volume=90))
        for tape_time, cancel_time, expected in [
            (2, 1, False),
            (3, 1, True),
            (4, 1, True),
            (4, 3, False),
        ]:
            with self.subTest(tape=tape_time, cancel=cancel_time):
                s = Instrument(Policy(), Counter())
                s.frame(0, [quotes(100), order("new", 100)])
                for t in (1, 2, 3, 4):
                    frame = []
                    if t == tape_time:
                        frame.append(trade)
                    if t == cancel_time:
                        frame.append(order("cancelled", 0))
                    if t == 3:
                        frame.append(quotes(101))
                    s.frame(t * MS, frame)
                self.assertEqual(s.shock is not None, expected)

    def test_late_exit_never_fills_or_reopens(self):
        from collections import Counter

        from order_resilience import MS, Instrument, Policy

        s = Instrument(Policy(), Counter())
        s.position = (-1, 100, 101, 100 * MS)
        s.book(1101 * MS, (95, 96, 20, 20))
        s.book(1102 * MS, (95, 96, 20, 20))
        s.finish()
        self.assertTrue(s.halted)
        self.assertEqual(s.stats["unresolved_exits"], 1)
        self.assertEqual(s.stats["completed"], 0)

    def test_gzip_replay_splits_and_executable_pnl(self):
        import csv
        import gzip
        import json
        import tempfile
        from contextlib import ExitStack
        from pathlib import Path

        from order_resilience import run

        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            d = Path(tmp)
            heads = {
                "orders": "order_id side action order_type volume price",
                "trades": "trade_id aggressor_side volume price",
                "pricebooks": "bids_json asks_json",
            }
            writers = {}
            for kind, fields in heads.items():
                w = csv.writer(
                    stack.enter_context(
                        gzip.open(d / (kind + ".csv.gz"), "wt", newline="")
                    )
                )
                h = [
                    "timestamp_ns",
                    "exchange_started_at",
                    "instrument_id",
                ] + fields.split()
                w.writerow(h)
                w.writerow(h)
                writers[kind] = w
            for day in (1, 0):  # intentionally reverse chronological; order IDs recur

                def row(kind, t, *x):
                    writers[kind].writerow(
                        [day * 86400 * 10**9 + t * 10**6, "session", "X", *x]
                    )

                def book(t, b, a, q=20):
                    row("pricebooks", t, json.dumps([[b, q]]), json.dumps([[a, 20]]))

                book(0, 100, 101)
                row("orders", 5, 1, "bid", "new", "limit", 500, 100)
                row("orders", 10, 1, "bid", "cancelled", "limit", 0, 100)
                book(20, 99, 100, 1)
                row("orders", 25, 2, "bid", "new", "limit", 500, 99)
                row("orders", 25, 2, "bid", "new", "limit", 500, 99)
                book(30, 99, 100)
                book(90, 98, 99)
                book(150, 99, 100)
                if day == 0:
                    book(2300, 102, 103)
                else:
                    row("trades", 2300, 1, "ask", 50, 99)
            stack.close()
            report = run(d, train_fraction=0.5)
            self.assertEqual(report["ingest"]["duplicates"], 2)
            self.assertEqual(report["ingest"]["repeated_headers"], 3)
            train, hold = report["train"], report["holdout"]
            self.assertEqual(train["completed"], 1)
            self.assertAlmostEqual(train["closed_net"], 1.98)
            self.assertEqual(hold["unresolved_exits"], 1)
            self.assertEqual(hold["flat_net"], 0)
