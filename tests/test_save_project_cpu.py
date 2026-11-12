"""Reject stale or ambiguous fit inputs before running a save simulation."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SaveProjectCpuTest(unittest.TestCase):
    def test_restore_copy_stores_do_not_count_as_guest_replay(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        from run_save_project_cpu import replayed_guest
        reference = dict(log_stores='32', spu_writes='32')
        # Actual early-load witness: 1,024 restore writes, zero guest replay.
        early = dict(loads='1', loads_failed='0', log_stores='1056', spu_writes='32')
        self.assertFalse(replayed_guest(reference, early))
        self.assertTrue(replayed_guest(reference, dict(early, spu_writes='44')))
        self.assertFalse(replayed_guest(reference, dict(early, loads_failed='1', spu_writes='44')))

    def project(self, root):
        project = root / 'project'
        project.mkdir()
        rows = []
        for name in ('cpu.vhd', 'memorymux.vhd', 'datacache.vhd', 'divider.vhd', 'memctrl.vhd'):
            path = project / name
            path.write_text('-- fixture source\n')
            rows.append(dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        (project / 'apf_top.qsf').write_text(''.join(
            f'set_global_assignment -name VHDL_FILE "{row["path"]}" -library work\n' for row in rows))
        (project / 'fit-inputs.json').write_text(json.dumps(dict(quartus_sources=rows)))
        return project

    def run_check(self, root, project, extra=()):
        # This deliberately has no fixture checkout or simulator. Input
        # provenance must fail first, before importing or running either.
        return subprocess.run([sys.executable, str(ROOT / 'scripts/run_save_project_cpu.py'),
                               str(project), '--fixture-root', str(root / 'absent'),
                               '--output', str(root / 'output'), *extra], capture_output=True, text=True)

    def test_staged_full_context_rejects_entry_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.run_check(root, self.project(root), ['--staged-full-context', '--entry-probe'])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('already paired full firmware/CPU', result.stderr)
            self.assertFalse((root / 'output').exists())

    def test_context_fault_requires_staged_firmware(self):
        for fault in ('debug-restore', 'spu-restore', 'sample-restore'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = self.run_check(root, self.project(root), ['--staged-context-fault', fault])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('requires --staged-full-context', result.stderr)
                self.assertFalse((root / 'output').exists())

    def test_old_format_probe_requires_staged_round_trip(self):
        for extra in ([], ['--staged-full-context'], ['--warm-round-trip']):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = self.run_check(root, self.project(root), ['--reject-old-staged-format', *extra])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('old-format rejection requires', result.stderr)
                self.assertFalse((root / 'output').exists())

    def test_contention_requires_error_only_entry_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.run_check(root, self.project(root), ['--entry-contention-matrix'])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('entry contention requires --entry-probe', result.stderr)
            self.assertFalse((root / 'output').exists())

    def test_kernel_check_requires_entry_and_its_own_matrix(self):
        for options in (['--entry-kernel-gprs'],
                        ['--entry-probe', '--entry-kernel-gprs', '--entry-contention-matrix']):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = self.run_check(root, self.project(root), options)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('kernel-GPR check requires --entry-probe', result.stderr)
                self.assertFalse((root / 'output').exists())

    def test_startup_and_contention_are_separate_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.run_check(root, self.project(root),
                ['--entry-probe', '--entry-contention-matrix', '--exp1-startup-matrix'])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('separate startup-matrix run', result.stderr)
            self.assertFalse((root / 'output').exists())

    def test_kernel_options_require_the_matching_run(self):
        for options, message in (
                (['--preserve-kernel-gprs'], 'preservation requires'),
                (['--entry-kernel-guard'], 'context guard requires'),
                (['--entry-probe', '--kernel-guard-kind', 'unusable'], 'guard kind requires'),
                (['--entry-probe', '--kernel-gpr-fault', 'restore-k1'], 'fault requires'),
                (['--entry-probe', '--preserve-kernel-gprs', '--entry-kernel-guard',
                  '--kernel-gpr-fault', 'restore-k1'], 'fault requires')):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = self.run_check(root, self.project(root), options)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse((root / 'output').exists())

    def test_changed_cpu_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self.project(root)
            (project / 'cpu.vhd').write_text('-- modified after the fit\n')
            result = self.run_check(root, project)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('fit-inputs.json does not bind the current', result.stderr)
            self.assertFalse((root / 'output').exists())

    def test_two_selected_cpus_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self.project(root)
            other = project / 'other'
            other.mkdir()
            path = other / 'cpu.vhd'
            path.write_text('-- another CPU\n')
            with (project / 'apf_top.qsf').open('a') as handle:
                handle.write(f'set_global_assignment -name VHDL_FILE "{path}" -library work\n')
            result = self.run_check(root, project)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('expected one selected', result.stderr)
            self.assertFalse((root / 'output').exists())


if __name__ == '__main__':
    unittest.main()
