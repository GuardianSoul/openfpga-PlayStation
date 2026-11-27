import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from check_m10k_sharing import main  # noqa: E402

HEADER = '; Name ; Type ; Mode ; Clock Mode ; Location ;'


def report(rows):
    lines = ['+----', '; Fitter RAM Summary ;', '+----', HEADER, '+----']
    lines += [f'; {n} ; M10K block ; ROM ; Single Clock ; {loc} ;' for n, loc in rows]
    lines += ['+----', '']
    return '\n'.join(lines)


class CheckM10kSharing(unittest.TestCase):
    def run_on(self, rows):
        with tempfile.NamedTemporaryFile('w', suffix='.rpt', delete=False) as f:
            f.write(report(rows))
        return main(['check_m10k_sharing.py', f.name])

    def test_shared_block_fails(self):
        # the 0.6.1 Rc3 case: the XA zigzag ROM and the GTE FastDiv ROM in one M10K
        self.assertEqual(self.run_on([('cd_xa_zigzag|Mux15_rtl_0', 'M10K_X30_Y14_N0'),
                                      ('GTEFastDiv_Inst|ram_rtl_0', 'M10K_X30_Y14_N0'),
                                      ('other', 'M10K_X3_Y1_N0, M10K_X3_Y2_N0')]), 1)

    def test_one_ram_per_block_passes(self):
        self.assertEqual(self.run_on([('cd_xa_zigzag|Mux15_rtl_0', 'M10K_X22_Y7_N0'),
                                      ('GTEFastDiv_Inst|ram_rtl_0', 'M10K_X30_Y14_N0'),
                                      ('other', 'M10K_X3_Y1_N0, M10K_X3_Y2_N0')]), 0)

    def test_unreadable_report(self):
        with tempfile.NamedTemporaryFile('w', suffix='.rpt', delete=False) as f:
            f.write('nothing here\n')
        self.assertEqual(main(['check_m10k_sharing.py', f.name]), 2)


if __name__ == '__main__':
    unittest.main()
