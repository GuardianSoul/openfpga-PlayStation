"""Test report integrity with labelled fixtures, without running Quartus."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.resource_report import DEFAULT
from scripts.run_resource_fit import timing_metrics, validated_counts

ROOT = Path(__file__).resolve().parents[1]


class ResourceFitTests(unittest.TestCase):
    def test_negative_slack_and_missing_timing_are_not_timing_success(self):
        metrics=timing_metrics("Type  : Slow Model Setup 'clk'\nSlack : -3.136\n\n"
                               "Type  : Fast Model Hold 'clk'\nSlack : -0.027\n")
        self.assertTrue(metrics['timing_violations_reported'])
        self.assertEqual(metrics['worst_setup_slack_ns'],-3.136)
        self.assertFalse(metrics['board_timing_verified'])
        self.assertIsNone(timing_metrics('')['timing_violations_reported'])

    def test_summary_requires_matching_device_revision_and_success(self):
        original = DEFAULT.read_text().replace('Top-level Entity Name : apf_top',
                                               'Top-level Entity Name : ap_core')
        counts = validated_counts(original, 'ap_core', '5CEBA4F23C8')
        self.assertEqual(counts['alms']['used'], 17060)
        for old, new in [('Successful', 'Failed'), ('5CEBA4F23C8', '5CSEBA6U23I7'),
                         ('Revision Name : ap_core', 'Revision Name : previous'),
                         ('Top-level Entity Name : ap_core', 'Top-level Entity Name : previous')]:
            with self.subTest(old=old), self.assertRaises(ValueError):
                validated_counts(original.replace(old, new), 'ap_core', '5CEBA4F23C8')

    def test_new_run_cannot_reuse_previous_counts_or_claim_board_timing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / 'fake-bin'
            binary.mkdir()
            fixture = DEFAULT.read_text()
            tool = f'''#!{sys.executable}
from pathlib import Path
import sys
name = Path(sys.argv[0]).name
if name == 'quartus_sh':
    print('TEST FIXTURE: not Quartus')
elif name == 'quartus_map' and (Path(sys.argv[0]).parent / 'virtual-clock').exists():
    print('TEST FIXTURE: clock port is fed by virtual pin "clk1x"')
elif name == 'quartus_fit' and (Path(sys.argv[0]).parent / 'emit-summary').exists():
    top = sys.argv[1]
    text = {fixture!r}.replace('ap_core', top).replace('apf_top', top)
    Path('output_files').mkdir(exist_ok=True)
    Path('output_files', top + '.fit.summary').write_text(text)
    if (Path(sys.argv[0]).parent / 'fail-fit').exists():
        Path('output_files', top + '.fit.summary').write_text(text.replace('Successful','Failed'))
        print('Error: TEST FIXTURE capacity failure, not a native measurement')
        sys.exit(1)
elif name == 'quartus_sta':
    Path('output_files').mkdir(exist_ok=True)
    Path('output_files', sys.argv[1] + '.sta.summary').write_text('TEST FIXTURE: negative slack\\n')
    if (Path(sys.argv[0]).parent / 'change-source').exists():
        Path(sys.argv[1] + '.vhd').write_text('-- TEST FIXTURE changed during compile\\n')
'''
            for name in ('quartus_sh', 'quartus_map', 'quartus_fit', 'quartus_sta'):
                path = binary / name
                path.write_text(tool)
                path.chmod(0o700)
            marker = binary / 'emit-summary'
            marker.touch()
            output = root / 'resources'
            command = [sys.executable, str(ROOT / 'scripts/run_resource_fit.py'),
                       '--bin', str(binary), '--output', str(output)]
            first = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            report1 = json.loads((output / 'quartus-report.json').read_text())
            self.assertTrue(report1['component_resource_runs_completed'])
            self.assertFalse(report1['board_timing_verified'])
            self.assertFalse(report1['hardware_deployable'])
            for target in report1['targets'].values():
                self.assertTrue(target['fit_verified'])
                self.assertFalse(target['board_timing_verified'])
                self.assertIn('negative slack', target['timing_summary']['text'])
            marker.unlink()
            second = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(second.returncode, 0)
            report2 = json.loads((output / 'quartus-report.json').read_text())
            self.assertNotEqual(report1['run_directory'], report2['run_directory'])
            self.assertFalse(report2['component_resource_runs_completed'])
            for target in report2['targets'].values():
                self.assertEqual(target['counts'], {})
                self.assertFalse(target['fit_verified'])
                self.assertTrue(target['errors'])
            # A zero synthesis exit with a virtual clock is insufficient evidence.
            marker.touch()
            (binary / 'virtual-clock').touch()
            third = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(third.returncode, 0)
            report3 = json.loads((output / 'quartus-report.json').read_text())
            for target in report3['targets'].values():
                self.assertEqual([step['tool'] for step in target['steps']], ['quartus_map'])
                self.assertEqual(target['counts'], {})
                self.assertIn('Clock input was virtualized', target['errors'][0])
            (binary / 'virtual-clock').unlink()
            (binary / 'fail-fit').touch()
            failed = subprocess.run(command+['--targets','psx_top'],capture_output=True,text=True,timeout=30)
            self.assertNotEqual(failed.returncode,0)
            report4=json.loads((output/'quartus-report.json').read_text())
            target=report4['targets']['psx_top']
            self.assertEqual(target['counts'],{})
            self.assertFalse(target['fit_verified'])
            self.assertIn('Failed',target['failed_fit_summary']['text'])
            self.assertIn('TEST FIXTURE capacity failure',target['tool_error_messages'][0])
            (binary/'fail-fit').unlink()
            (binary/'change-source').touch()
            changed=subprocess.run(command+['--targets','gte'],capture_output=True,text=True,timeout=30)
            self.assertNotEqual(changed.returncode,0)
            target=json.loads((output/'quartus-report.json').read_text())['targets']['gte']
            self.assertFalse(target['source_identity_verified'])
            self.assertFalse(target['fit_verified'])
            self.assertEqual(target['counts'],{})
            self.assertTrue(any('Sources changed' in error for error in target['errors']))


if __name__ == '__main__':
    unittest.main()
