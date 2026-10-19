from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_native_divider_dsp as candidate


class NativeDividerProfile(unittest.TestCase):
    def setUp(self):
        # The real scanner is exercised by the complete 179-role qualification.
        # These profile-contract tests need no downloaded upstream HDL.
        def fixture_inputs(root):
            return [dict(path=str(root / name), assignment=kind, library='work')
                    for name, kind in [('feature.sv', 'SYSTEMVERILOG_FILE'),
                                       ('pocket.sdc', 'PROJECT')]]
        patch = mock.patch.object(candidate, 'inputs', side_effect=fixture_inputs)
        patch.start()
        self.addCleanup(patch.stop)

    def project(self, root, seed):
        root.mkdir()
        (root / 'feature.sv').write_text('module feature(output y);assign y=1;endmodule\n')
        (root / 'pocket.sdc').write_text('# source root ' + str(root) + '\n')
        (root / 'apf_top.qpf').write_text('PROJECT_REVISION = apf_top\n')
        (root / 'apf_top.qsf').write_text(
            'set_global_assignment -name TOP_LEVEL_ENTITY apf_top\n'
            'set_global_assignment -name SEED ' + str(seed) + '\n'
            'set_global_assignment -name VERILOG_MACRO "SELECTED_FEATURE=1"\n'
            'set_global_assignment -name SYSTEMVERILOG_FILE "' + str(root / 'feature.sv') + '"\n'
            'set_global_assignment -name SDC_FILE "' + str(root / 'pocket.sdc') + '"\n')
        return root

    def test_relocation_and_confirmation_seed_preserve_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.project(Path(directory) / 'first', 4)
            second = self.project(Path(directory) / 'second', 12)
            self.assertEqual(candidate.profile(first), candidate.profile(second))

    def test_selected_feature_change_invalidates_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.project(Path(directory) / 'project', 4)
            original = candidate.profile(project)
            qsf = project / 'apf_top.qsf'
            qsf.write_text(qsf.read_text().replace('SELECTED_FEATURE=1', 'SELECTED_FEATURE=0'))
            self.assertNotEqual(original, candidate.profile(project))

    def test_hardware_change_invalidates_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.project(Path(directory) / 'project', 4)
            original = candidate.profile(project)
            source = project / 'feature.sv'
            source.write_text(source.read_text().replace('assign y=1', 'assign y=0'))
            self.assertNotEqual(original, candidate.profile(project))


if __name__ == '__main__':
    unittest.main()
