"""The narrowed GTE microcode table rebuilds every 87-bit entry and fits two M10K."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_gte_microcode_narrow as narrow


class GteMicrocodeNarrow(unittest.TestCase):
    def setUp(self):
        self.words = narrow.read_table()
        self.columns, self.sources = narrow.plan(self.words)

    def test_every_entry_rebuilds_from_the_narrow_word(self):
        for word, small in zip(self.words, narrow.narrow_words(self.words, self.columns)):
            self.assertEqual(narrow.expand(small, self.sources), word)

    def test_two_m10k_in_256_x_40_mode(self):
        self.assertLessEqual(len(self.columns), 80)
        self.assertEqual(len(self.sources), 87)

    def test_a_wrong_wire_is_seen(self):
        bad = list(self.sources)
        b = next(b for b, (k, v) in enumerate(bad) if k == 'rom' and self.columns[v] != b)
        bad[b] = ('rom', (bad[b][1] + 1) % len(self.columns))
        small = narrow.narrow_words(self.words, self.columns)
        self.assertNotEqual([narrow.expand(s, bad) for s in small], self.words)


if __name__ == '__main__':
    unittest.main()
