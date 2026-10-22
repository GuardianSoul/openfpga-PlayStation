"""prepare_cd_pause_arith.py: same Pause workDelay as upstream for every input."""
from pathlib import Path
import random
import unittest

from scripts.prepare_cd_pause_arith import patch

PINNED = Path(__file__).resolve().parents[1] / 'references/PSX_MiSTer/rtl/cd_top.vhd'
MAX27 = (1 << 27) - 1


def upstream(t, nxt, delay, double):
    elapsed = nxt - delay if nxt > delay else 0
    target = t - elapsed if t > elapsed else 0
    minimum = 1066874 if double else 2157295
    return minimum if target < minimum else target


def rewritten(t, nxt, delay, double):
    dn = delay - nxt
    x = t + dn if dn < 0 else t
    minimum = 1066874 if double else 2157295
    return minimum if x < minimum else x


class CDPauseArithTests(unittest.TestCase):
    def test_same_value_for_every_input(self):
        edges = [0, 1, 1066873, 1066874, 1066875, 2157294, 2157295, 2157296, 451584, 903168, 9934848, 1 << 26, MAX27]
        for t in edges:
            for nxt in edges:
                for delay in edges:
                    for double in (False, True):
                        self.assertEqual(upstream(t, nxt, delay, double), rewritten(t, nxt, delay, double))
        r = random.Random(7)
        for _ in range(200000):
            t, nxt, double = r.randint(0, MAX27), r.randint(0, MAX27), r.random() < 0.5
            delay = max(0, min(MAX27, nxt + r.randint(-4000000, 4000000)))
            self.assertEqual(upstream(t, nxt, delay, double), rewritten(t, nxt, delay, double))

    @unittest.skipUnless(PINNED.is_file(), 'references/PSX_MiSTer not checked out')
    def test_patch_replaces_only_the_pause_arithmetic(self):
        text = PINNED.read_text()
        out = patch(text)
        self.assertNotIn('pause_elapsed_v', out)
        self.assertEqual(out.count('pause_dn_v := to_signed(driveDelay, 29) - to_signed(driveDelayNext, 29);'), 1)
        # T keeps its original statement (and wrap)
        self.assertEqual(out.count('pause_target_v := pause_rotation_ticks - (2 * driveREADSPEED);'), 1)
        self.assertEqual(text.split('-- Pause waits one disc rotation')[0].replace(
            '      variable pause_elapsed_v  : integer range 0 to 134217727;\n', ''),
            out.split('-- Pause waits one disc rotation')[0].replace(
            '      variable pause_dn_v       : signed(28 downto 0);\n'
            '      variable pause_x_v        : signed(28 downto 0);\n'
            '      variable pause_min_v      : integer range 0 to 2157295;\n', ''))
        self.assertEqual(text.split('drive_stop <= \'1\';', 1)[1], out.split('drive_stop <= \'1\';', 1)[1])
        with self.assertRaises(SystemExit):
            patch(out)


if __name__ == '__main__':
    unittest.main()
