import unittest

from rapid_multi import clip_to_target


class Test(unittest.TestCase):
    def test_capacity(self):
        self.assertEqual(clip_to_target(8, 10, 50), 2)
        self.assertEqual(clip_to_target(-8, -10, 50), -2)
        self.assertEqual(clip_to_target(10, 10, 5), 0)
        self.assertEqual(clip_to_target(-10, 10, 3), 3)
