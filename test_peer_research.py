import unittest
import peer_research as p


class TestPeer(unittest.TestCase):
    def test_asof_keeps_invalidation_and_never_looks_forward(self):
        b = p.Book([(10, (9, 11)), (15, None), (19, (14, 16))])
        self.assertIsNone(b.at(9))
        self.assertEqual(b.at(14), (9, 11))
        self.assertIsNone(b.at(17))
        self.assertIsNone(b.at(19+p.FRESH+1))

    def test_round_trip_uses_arrival_and_both_spreads(self):
        s = 10**9
        leader = p.Book([(4*s, (99,101)), (5*s-1, (100,102)), (5*s, (90,92))])
        target = p.Book([(5*s, (20,22)), (5*s+p.LATENCY, (21,23)), (7*s+p.LATENCY, (22,24))])
        r = p.run(leader, target, 0, 8*s)
        self.assertEqual((r['long'], r['trades']), (1,1))
        self.assertEqual((r['mid_pnl'],r['crossed_pnl']), (1,-1))
        self.assertAlmostEqual(r['net_pnl'], -1-45/10000)

    def test_boundary_never_uses_pre_split_signal_or_post_split_exit(self):
        s=10**9
        leader=p.Book([(4*s,(9,11)), (5*s-1,(10,12))]); target=p.Book([(5*s,(1,2)),(7*s,(2,3))])
        self.assertEqual(p.run(leader,target,5*s,9*s)['trades'],0)
        self.assertEqual(p.run(leader,target,0,7*s)['trades'],0)

    def test_missing_exit_waits_for_next_executable_quote(self):
        s=10**9
        leader=p.Book([(4*s,(10,12)),(5*s-1,(9,11))]); target=p.Book([(5*s,(9,11)),(7*s,None),(8*s,(9,12))])
        r=p.run(leader,target,0,9*s)
        self.assertEqual((r['short'],r['delayed_exits'],r['crossed_pnl']),(1,1,-3))
