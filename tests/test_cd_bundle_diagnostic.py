"""The diagnostic must not turn an unknown source or scope into a waiver."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from diagnose_pocket_cd_cdc import MAX, MINS, REVIEWED_CD, profile, replace_bounds


class CDDiagnosticGuards(unittest.TestCase):
    def test_replaces_only_the_two_known_lines(self):
        for minimum in MINS:
            original = '# untouched\n' + MAX + minimum + 'set_false_path -to existing\n'
            result = replace_bounds(original, Path('/build/diagnostic-CD.tcl'))
            self.assertEqual(result, '# untouched\nset_false_path -to existing\n\nsource "/build/diagnostic-CD.tcl"\n')
        for bad in (MAX, MINS[0], MAX + MINS[0] * 2, MAX * 2 + MINS[0],
                    MAX + ''.join(MINS), MAX + MINS[0].replace(' 0 ', ' -20 '),
                    MAX + MINS[0] + 'set_min_delay 0 -from other_disc_stream|x\n'):
            with self.assertRaises(ValueError):
                replace_bounds(bad, Path('/build/scope.tcl'))
        for path in ('/build/$name.tcl', '/build/[name].tcl', '/build/"name.tcl'):
            with self.assertRaises(ValueError):
                replace_bounds(MAX + MINS[0], Path(path))

    def test_requires_reviewed_source_and_fixed_mapped_profile(self):
        top = ("psx_cd_stream #(.DEFAULT_SLOT(16'd0), .NBUF(2), .MAX_TRACKS(19)) disc_stream ("
               ".host_clk(clk_74a),.clk(clk1x),.disc_select_strobe(1'b0));")
        for sha in REVIEWED_CD:
            profile(top, sha)
        for bad in (top + top, top.replace('MAX_TRACKS(19)', 'MAX_TRACKS(4)'),
                    top.replace('NBUF(2)', 'NBUF(4)'), top.replace('clk1x', 'clk2x'),
                    top.replace('clk_74a', 'clk_74b'), top.replace("1'b0", 'swap_strobe'),
                    top.replace('.NBUF(2)', ".NBUF(2),.LIBCRYPT_SLOT(16'd99)")):
            with self.assertRaises(ValueError):
                profile(bad, next(iter(REVIEWED_CD)))
        with self.assertRaisesRegex(ValueError, 'not been reviewed'):
            profile(top, '0' * 64)


if __name__ == '__main__':
    unittest.main()
