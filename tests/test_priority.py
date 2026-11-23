import unittest
from unittest.mock import Mock
from priority_practice import supervise

class Retry(unittest.TestCase):
    def test_connect_failure_then_clean_handoff(self):
        a,b=Mock(),Mock(); a.connect.side_effect=ConnectionError(); a.is_connected.return_value=False
        b.is_connected.return_value=True; b.get_positions.return_value={}
        seq=iter((a,b)); runner=Mock(); pause=Mock()
        supervise(lambda:next(seq),budget=60, clock=lambda:0,sleep=pause,runner=runner)
        pause.assert_called_once_with(5); runner.assert_called_once(); b.disconnect.assert_called_once()
    def test_unknown_submission_is_not_retried(self):
        a=Mock(); a.is_connected.return_value=False; a.insert_order.side_effect=ConnectionError(); pause=Mock()
        supervise(lambda:a,budget=60,clock=lambda:0,sleep=pause,runner=lambda x,_:x.insert_order())
        pause.assert_not_called(); a.connect.assert_called_once()
