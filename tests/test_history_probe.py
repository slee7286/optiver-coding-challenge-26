import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock


class Frame:
    """Offline stand-in for the documented DataFrame interface."""
    def __init__(self, columns, *rows):
        self.columns, self.rows = columns.split(), rows

    def __len__(self):
        return len(self.rows)

    def itertuples(self, *, index, name):
        assert index is False and name is None
        return iter(self.rows)


class HistoryProbeTests(unittest.TestCase):
    def test_three_paced_reads_of_same_completed_utc_window(self):
        from history_probe import probe
        getters = [Mock(return_value=Frame('instrument_id')) for _ in range(3)]
        loader = SimpleNamespace(**dict(zip(('get_trades_df', 'get_orders_df', 'get_pricebooks_df'), getters)))
        now = datetime(2026, 4, 1, 2, 3, tzinfo=timezone(timedelta(hours=2)))
        sleep = Mock()
        report = probe(loader, now=now, sleep=sleep)
        start, end = getters[0].call_args.args
        self.assertEqual(end, datetime(2026, 4, 1, 0, 0))  # naive UTC per hosted examples
        self.assertLess(start, end)
        self.assertLessEqual(end - start, timedelta(hours=1))
        for get in getters:
            get.assert_called_once_with(start, end)
        self.assertEqual(sleep.call_count, 2)
        self.assertTrue(all(c.args[0] >= 2 for c in sleep.call_args_list))
        self.assertEqual(report['feeds']['trades'], {'rows': 0, 'columns': ['instrument_id']})
        self.assertEqual(report['candidates'], [])

    def test_rank_by_traded_volume_and_summarize_valid_two_sided_books(self):
        from history_probe import probe
        loader = SimpleNamespace(
            get_trades_df=lambda *_: Frame('instrument_id volume buyer', ('A', 2, 'private'), ('B', 7, 'private')),
            get_orders_df=lambda *_: Frame('instrument_id', ('A',), ('C',)),
            get_pricebooks_df=lambda *_: Frame('instrument_id bids_json asks_json',
                ('B', '[[10,8]]', '[[12,4]]'), ('B', '[]', '[[11,3]]'),
                ('B', 'bad', '[]'), ('B', '[[13,1]]', '[[12,1]]')))
        report = probe(loader, sleep=lambda _: None)
        self.assertNotIn('private', repr(report))
        self.assertEqual([c['instrument_id'] for c in report['candidates']], ['B', 'A', 'C'])
        b, a, c = report['candidates']
        self.assertEqual((b['trades'], b['traded_volume'], b['pricebooks'], b['valid_two_sided'], b['mean_spread']), (1, 7, 4, 1, 2))
        self.assertEqual((a['orders'], c['trades'], c['mean_spread']), (1, 0, None))

    def test_rate_limit_stops_without_retry(self):
        from history_probe import probe
        loader = Mock()
        loader.get_trades_df.side_effect = ValueError('429: wait 15s')
        with self.assertRaisesRegex(ValueError, '429'):
            probe(loader, sleep=self.fail)
        self.assertEqual(len(loader.mock_calls), 1)
