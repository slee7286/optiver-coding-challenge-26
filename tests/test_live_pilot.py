"""Offline only. Run smoke: python -B -m unittest discover -s tests -p test_live_pilot.py -k smoke"""
import contextlib
import io
import subprocess
import sys
import unittest
from types import SimpleNamespace as Row
from unittest.mock import Mock, patch

import live_pilot as pilot


class Fake:
    def __init__(self, fills=()):
        self.fills = fills
        self.sent, self.cancels, self.trades = [], [], []
        self.pos, self.open = {}, {'S': {}, 'HALTED': {}}
        self.stock = Row(instrument_type=Row(name='STOCK'), tick_size=.1, price_limit=None)
        self.book = Row(instrument_id='S', bids=[Row(price=10., volume=4)], asks=[Row(price=10.3, volume=3)])

    def is_connected(self): return True
    def get_instruments(self): return {'S': self.stock, 'HALTED': None}
    def get_tradable_instruments(self): return {'S': self.stock}
    def get_positions(self): return self.pos.copy()
    def get_outstanding_orders(self, symbol): return self.open[symbol].copy()
    def get_last_price_book(self, symbol): return self.book
    def poll_new_trades(self, symbol):
        result, self.trades = self.trades, []
        return result

    def insert_order(self, symbol, *, price, volume, side, order_type):
        self.sent.append((symbol, price, volume, side, order_type))
        oid = len(self.sent)
        if oid in self.fills:
            self.trades.append(Row(order_id=oid, instrument_id=symbol, price=price, volume=volume, side=side))
            self.pos[symbol] = self.pos.get(symbol, 0) + (volume if side == 'bid' else -volume)
        elif order_type == 'limit':
            self.open[symbol][oid] = Row(order_id=oid)
        return Row(success=True, order_id=oid)

    def delete_order(self, symbol, *, order_id):
        self.cancels.append((symbol, order_id))
        del self.open[symbol][order_id]
        return Row(success=True)


class PilotTests(unittest.TestCase):
    def test_hosted_noarg_entrypoint_only_fake_client(self):
        client = Fake((1, 2))
        client.connect, client.disconnect = Mock(), Mock()
        factory = Mock(return_value=client)
        modules = {'optibook': Row(), 'optibook.synchronous_client': Row(Exchange=factory)}
        with patch.dict(sys.modules, modules), contextlib.redirect_stdout(io.StringIO()) as log:
            self.assertEqual(pilot.main(), 0)
        factory.assert_called_once_with()
        client.connect.assert_called_once_with()
        client.disconnect.assert_called_once_with()
        self.assertIn('positions={} outstanding={}', log.getvalue())

    def test_watchdog_interrupts_stalled_fake_connect(self):
        code = '''import live_pilot, sys, time, threading
from types import SimpleNamespace as R
timer = threading.Timer
threading.Timer = lambda n, cb: timer(.1, cb) if n == 30 else None
sys.modules['optibook'] = R()
sys.modules['optibook.synchronous_client'] = R(Exchange=lambda: R(connect=lambda: time.sleep(60)))
sys.exit(live_pilot.main())'''
        result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn('TIMEOUT: positions/outstanding UNKNOWN', result.stdout)

    def run_fake(self, x, emit=None):
        return pilot.run(x, sleep=lambda _: None, emit=emit or (lambda _: None))

    def test_smoke_passive_round_trip(self):
        x = Fake((1, 2))
        print('FAKE EXCHANGE SMOKE')
        self.assertTrue(self.run_fake(x, print))
        self.assertEqual(x.sent, [('S', 10., 1, 'bid', 'limit'), ('S', 10.3, 1, 'ask', 'limit')])
        self.assertEqual(x.pos['S'], 0)

    def test_cancelled_exit_late_fill_must_not_sell_again(self):
        x = Fake((1,))
        original = x.get_last_price_book
        def book(symbol):
            if x.cancels and not x.trades and x.pos['S'] == 1:
                x.pos['S'] = 0
                x.trades.append(Row(order_id=2, instrument_id='S', price=10.3, volume=1, side='ask'))
            return original(symbol)
        x.get_last_price_book = book
        self.run_fake(x)
        self.assertEqual(len(x.sent), 2)

    def test_preexisting_team_state_no_writes(self):
        for which in ('position', 'order'):
            with self.subTest(which=which):
                x = Fake()
                if which == 'order': x.open['HALTED'][42] = Row()
                else: x.pos['HALTED'] = -2
                self.assertFalse(self.run_fake(x))
                self.assertEqual((x.sent, x.cancels), ([], []))

    def test_no_fill_only_cancel_acknowledged_entry(self):
        x = Fake()
        self.assertTrue(self.run_fake(x))
        self.assertEqual(x.cancels, [('S', 1)])
        self.assertEqual(len(x.sent), 1)

    def test_single_best_bid_ioc_and_report_residual_if_unfilled(self):
        for filled in (False, True):
            with self.subTest(filled=filled):
                x = Fake((1, 3) if filled else (1,))
                orig = x.delete_order
                def cancel(*args, **kwargs):
                    x.book.bids[0].price = 9.8
                    return orig(*args, **kwargs)
                x.delete_order = cancel
                messages = []
                self.assertEqual(self.run_fake(x, messages.append), filled)
                self.assertEqual(x.sent[-1], ('S', 9.8, 1, 'ask', 'ioc'))
                self.assertEqual(len(x.sent), 3)
                self.assertEqual(x.cancels, [('S', 2)])
                if not filled: self.assertIn("positions={'S': 1}", messages[-1])

    def test_ambiguous_insert_cancel_or_connection_no_retry(self):
        for failure in ('insert', 'cancel', 'disconnect', 'mismatch', 'foreign', 'missing_id'):
            with self.subTest(failure=failure):
                x = Fake()
                def insert(*args, **kwargs):
                    r = Fake.insert_order(x, *args, **kwargs)
                    if failure == 'insert': raise TimeoutError()
                    if failure == 'disconnect': x.is_connected = lambda: False
                    if failure == 'mismatch': x.pos['S'] = 1
                    if failure == 'foreign': x.open['S'][99] = Row()
                    if failure == 'missing_id': r.order_id = None
                    return r
                x.insert_order = insert
                if failure == 'cancel': x.delete_order = Mock(side_effect=TimeoutError())
                self.assertFalse(self.run_fake(x))
                self.assertEqual(len(x.sent), 1)
                self.assertEqual(x.cancels, [])
                if failure == 'cancel': x.delete_order.assert_called_once_with('S', order_id=1)

    def test_ineligible_books_or_derivatives_no_entry(self):
        for case in ('future', 'empty', 'crossed', 'offgrid', 'outside'):
            with self.subTest(case=case):
                x = Fake()
                if case == 'future': x.stock.instrument_type.name = 'STOCK_FUTURE'
                elif case == 'empty': x.book.asks = []
                elif case == 'crossed': x.book.bids[0].price = 10.4
                elif case == 'offgrid': x.book.bids[0].price = 10.03
                else: x.stock.price_limit = Row(min=11., max=12.)
                self.assertFalse(self.run_fake(x))
                self.assertEqual(x.sent, [])
