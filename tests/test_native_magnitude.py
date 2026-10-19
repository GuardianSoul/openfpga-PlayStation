from pathlib import Path
import hashlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_native_divider_dsp as fingerprint
import prepare_native_magnitude as candidate


class NativeMagnitudeAdmission(unittest.TestCase):
    def fixture(self, root):
        parent = root / 'build/source'
        parent.mkdir(parents=True)
        (parent / 'feature.sv').write_text('module feature(output y);assign y=1;endmodule\n')
        (parent / 'apf_top.qsf').write_text(
            'set_global_assignment -name TOP_LEVEL_ENTITY apf_top\n'
            'set_global_assignment -name VERILOG_MACRO "SELECTED_FEATURE=1"\n')
        return parent

    def rows(self, parent):
        source = parent / 'feature.sv'
        return [dict(path=str(source), assignment='SYSTEMVERILOG_FILE', library='work',
                     sha256=hashlib.sha256(source.read_bytes()).hexdigest())]

    def check_rejection(self, case):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = self.fixture(root)
            output = root / 'build/output'
            with mock.patch.object(fingerprint, 'inputs', side_effect=self.rows):
                original = candidate.profile(parent)
                if case == 'hardware':
                    source = parent / 'feature.sv'
                    source.write_text(source.read_text().replace('assign y=1', 'assign y=0'))
                elif case == 'setting':
                    qsf = parent / 'apf_top.qsf'
                    qsf.write_text(qsf.read_text() +
                                   'set_global_assignment -name OPTIMIZE_HOLD_TIMING OFF\n')
                else:
                    assets = root / 'assets'
                    assets.mkdir()
                    (assets / 'manifest.json').write_text('{"changed":true}\n')
                # A freshly hashed manifest must not bypass the source profile.
                refreshed = dict(quartus_sources=self.rows(parent))
                with mock.patch.object(candidate, 'ROOT', root), \
                     mock.patch.object(candidate, 'BASE', original), \
                     mock.patch.object(candidate, 'load_source', return_value=refreshed), \
                     mock.patch.object(candidate.subprocess, 'run', return_value=mock.Mock(returncode=0)), \
                     mock.patch.object(candidate, 'ASSETS', root / 'assets'), \
                     mock.patch.object(candidate, 'snapshot') as capture:
                    message = 'Unreviewed producer' if case == 'assets' else 'Exact selected Fill/B6'
                    with self.assertRaisesRegex(ValueError, message):
                        candidate.prepare(parent, output)
                    capture.assert_not_called()
                self.assertFalse(output.exists())

    def test_changed_hardware_with_refreshed_manifest_fails_before_snapshot(self):
        self.check_rejection('hardware')

    def test_weakened_fitter_setting_fails_before_snapshot(self):
        self.check_rejection('setting')

    def test_changed_support_manifest_fails_before_snapshot(self):
        self.check_rejection('assets')


if __name__ == '__main__':
    unittest.main()
