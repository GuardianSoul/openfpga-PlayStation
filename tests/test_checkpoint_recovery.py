"""A missing Python report cannot relax checkpoint or input identity checks."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.prepare_resource_targets import digest

ROOT = Path(__file__).resolve().parents[1]


class CheckpointRecoveryTests(unittest.TestCase):
    def fixture(self, root):
        run, resumed = root / 'run', root / 'resumed'
        run.mkdir(); resumed.mkdir()
        state = run / 'final-state.bin'; state.write_bytes(b'serialized model')
        boot = run / 'boot.hex8'; boot.write_text('00\n')
        expected = run / 'expected.hex'; expected.write_text('600df00d\n')
        fixed = {'boot_sha256': digest(boot), 'expected_sha256': digest(expected)}
        segment = {'end_cycle': 100, 'checks': 0, 'sectors': 7}
        (run / 'segment.json').write_text(json.dumps(segment))
        (run / 'final-state.json').write_text(json.dumps({
            'fixed_inputs': fixed, 'checkpoint_sha256': digest(state), 'segment': segment}))
        (run / 'run.log').write_text('STOP cycles=100 checks=0 sectors=7\n')
        protected = root / 'model.cpp'; protected.write_text('immutable source')
        report = {'source_and_fixed_input_identity_verified': True,
            'exit_code': 0, 'wall_timeout': False, 'bounded_execution_completed': True,
            'diagnostic_passed': False, 'restored_from': str(state),
            'fixed_inputs': fixed, 'segment': {'start_cycle': 100, 'end_cycle': 110},
            'protected_sha256': {str(protected): digest(protected)}}
        (resumed / 'report.json').write_text(json.dumps(report))
        return run, resumed, protected

    def invoke(self, run, resumed):
        return subprocess.run([sys.executable, str(ROOT / 'scripts/recover_checkpoint_report.py'),
            '--run', str(run), '--continuation', str(resumed)], text=True, capture_output=True)

    def test_validated_continuation_preserves_original_sidecar_and_unknown_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            run, resumed, _ = self.fixture(Path(temporary))
            sidecar = (run / 'final-state.json').read_bytes()
            result = self.invoke(run, resumed)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((run / 'recovered-report.json').read_text())
            self.assertIsNone(report['recovery']['original_subprocess_exit_code'])
            self.assertFalse(report['game_verified'])
            self.assertEqual(sidecar, (run / 'final-state.json').read_bytes())
            self.assertFalse((run / 'report.json').exists())
            self.assertNotEqual(self.invoke(run, resumed).returncode, 0)

    def test_source_and_checkpoint_mutations_are_rejected(self):
        for mutation in ('source', 'checkpoint', 'completion', 'boot', 'segment'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                run, resumed, protected = self.fixture(Path(temporary))
                path = {'source': protected, 'checkpoint': run / 'final-state.bin',
                    'completion': run / 'run.log', 'boot': run / 'boot.hex8',
                    'segment': run / 'segment.json'}[mutation]
                path.write_text('{"end_cycle":99}' if mutation == 'segment' else 'changed')
                self.assertNotEqual(self.invoke(run, resumed).returncode, 0)
                self.assertFalse((run / 'recovered-report.json').exists())


if __name__ == '__main__':
    unittest.main()
