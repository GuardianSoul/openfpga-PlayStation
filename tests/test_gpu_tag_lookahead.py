# SPDX-License-Identifier: MIT
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_gpu_tag_lookahead import lookahead_text, prepare_project


class TagLookaheadAdmission(unittest.TestCase):
    def test_unknown_shader_is_not_transformed(self):
        with self.assertRaisesRegex(ValueError, 'Unreviewed GPU tag-lookahead'):
            lookahead_text('unreviewed shader\n')

    def test_fitted_project_is_rejected_before_reading_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / 'db').mkdir()
            with self.assertRaisesRegex(ValueError, 'Fresh pre-fit'):
                prepare_project(p)

    def test_missing_source_selection_leaves_qsf_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            q = p / 'apf_top.qsf'
            q.write_text('set_global_assignment -name FAMILY "Cyclone V"\n')
            before = q.read_bytes()
            with self.assertRaisesRegex(ValueError, 'One selected tag/RAM source'):
                prepare_project(p)
            self.assertEqual(q.read_bytes(), before)
            self.assertFalse((p / 'gpu-tag-lookahead').exists())

    def test_unreviewed_primitive_does_not_write_partial_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            paths = [p / name for name in ('gpu_pixelpipeline.vhd', 'pocket_dpram_sdp.vhd')]
            for source in paths:
                source.write_text('unreviewed\n')
            q = p / 'apf_top.qsf'
            q.write_text(''.join(f'set_global_assignment -name VHDL_FILE "{x}" -library work\n' for x in paths) +
                         f'set_global_assignment -name VHDL_FILE "{paths[1]}" -library mem\n')
            before = {x: x.read_bytes() for x in paths + [q]}
            with self.assertRaisesRegex(ValueError, 'Reviewed one-clock M10K'):
                prepare_project(p)
            self.assertTrue(all(x.read_bytes() == value for x, value in before.items()))
            self.assertFalse((p / 'gpu-tag-lookahead').exists())

    def test_duplicate_shader_selection_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            names = ('gpu_pixelpipeline.vhd', 'gpu_pixelpipeline.vhd', 'pocket_dpram_sdp.vhd')
            (p / 'apf_top.qsf').write_text(''.join(
                f'set_global_assignment -name VHDL_FILE "{p}/{i}/{name}" -library work\n'
                for i, name in enumerate(names)))
            with self.assertRaisesRegex(ValueError, 'One selected tag/RAM source.*gpu_pixelpipeline'):
                prepare_project(p)
            self.assertFalse((p / 'gpu-tag-lookahead').exists())


if __name__ == '__main__':
    unittest.main()
