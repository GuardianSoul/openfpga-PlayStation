"""A frozen full-top result must include constraints loaded through Tcl source."""
import sys
from pathlib import Path
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from measure_pocket_seeds import inputs


class SDCCapture(unittest.TestCase):
    def project(self, root):
        (root/'apf_top.qsf').write_text('')
        (root/'apf_top.qpf').write_text('')
        (root/'pocket.sdc').write_text('')

    def test_absolute_relative_recursive_and_changed_constraint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.project(root)
            external=root/'external files';external.mkdir()
            child=external/'apf constraints.sdc';leaf=root/'pll.sdc'
            (root/'pocket.sdc').write_text(f'source "{child}"\n')
            child.write_text('source -encoding utf-8 {pll.sdc}\n')
            leaf.write_text('derive_pll_clocks\n')
            before={s['path']:s for s in inputs(root)}
            self.assertEqual(before[str(child)]['assignment'],'SDC_SOURCE')
            self.assertIn(str(leaf),before)
            leaf.write_text('derive_pll_clocks\nderive_clock_uncertainty\n')
            after={s['path']:s for s in inputs(root)}
            self.assertNotEqual(before[str(leaf)]['sha256'],after[str(leaf)]['sha256'])

    def test_missing_dynamic_conditional_and_cycle_reject(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.project(root)
            for text in ['source absent.sdc\n','source "$env(HOME)/clocks.sdc"\n',
                         'source [file join clocks.sdc]\n','if {1} {source clocks.sdc}\n',
                         'source pocket.sdc\n']:
                with self.subTest(text=text):
                    (root/'pocket.sdc').write_text(text)
                    with self.assertRaises(ValueError):inputs(root)

    def test_repeated_includes_and_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.project(root)
            child=root/'clocks.sdc';child.write_text('derive_pll_clocks\nif {0} {error "Missing source clock"}\n')
            (root/'pocket.sdc').write_text('# source omitted.sdc\nsource clocks.sdc # PLL\nsource "clocks.sdc"\n')
            self.assertEqual(sum(s['path']==str(child) for s in inputs(root)),1)


if __name__=='__main__':unittest.main()
