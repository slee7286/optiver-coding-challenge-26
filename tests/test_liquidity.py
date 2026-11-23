import csv
import gzip
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TapeTest(unittest.TestCase):
    def test_common_clock_time_weighting_and_stale_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            books, trades = [Path(tmp) / (n + '.gz') for n in ('books', 'trades')]
            with gzip.open(books, 'wt', newline='') as f:
                w = csv.writer(f)
                w.writerow(['timestamp_ns', 'instrument_id', 'bids_json', 'asks_json'])
                for sec, sym, ask in [(0, 'A', '[[1.1,1]]'), (0, 'B', '[]'), (3, 'A', '[[1.3,2]]')]:
                    w.writerow([sec * 10**9, sym, '[[1,2]]', ask])
            with gzip.open(trades, 'wt', newline='') as f:
                w = csv.writer(f); w.writerow(['timestamp_ns', 'instrument_id', 'volume'])
                w.writerow([4 * 10**9, 'A', 7])
            cmd = [sys.executable, str(Path(__file__).resolve().parents[1] / 'research/liquidity.py'), '--books', str(books), '--trades', str(trades)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            a, b = csv.DictReader(result.stdout.splitlines())
            self.assertEqual((a['symbol'], b['symbol']), ('A', 'B'))
            self.assertEqual(float(a['spread_ticks']), 1.5)
            self.assertEqual(float(a['trades_min']), 15)
            self.assertEqual(int(a['volume']), 7)
            self.assertEqual(float(a['stale_pct']), 50)
            self.assertEqual(int(a['stale_intervals']), 1)
            self.assertEqual(b['spread_ticks'], '')
            self.assertEqual(int(b['volume']), 0)
