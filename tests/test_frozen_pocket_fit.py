from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_frozen_pocket_fit as runner


class FrozenFitAdmission(unittest.TestCase):
    def test_physical_tools_require_jenkins_without_starting_anything(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(runner.subprocess, 'check_output') as commands:
            with self.assertRaisesRegex(ValueError, 'Jenkins queue'):
                runner.executor_guard()
            commands.assert_not_called()

    def test_legacy_job_blocks_executor(self):
        with mock.patch.dict(os.environ, {'BUILD_URL': 'https://build.session-zero.dev/job/psx-fit/7/'}), \
             mock.patch.object(runner.subprocess, 'check_output', return_value='python\nquartus_fit\n'):
            with self.assertRaisesRegex(ValueError, 'Legacy Quartus'):
                runner.executor_guard()

    def test_nested_snapshot_refused_before_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            marker = source / 'keep'
            marker.write_text('original')
            with self.assertRaisesRegex(ValueError, 'outside its source'):
                runner.snapshot(source, source / 'unused-manifest.json', source / 'nested', 4)
            self.assertEqual(sorted(p.name for p in source.iterdir()), ['keep'])
            self.assertEqual(marker.read_text(), 'original')

    def test_analysis_copy_is_outside_the_original_fit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            (project / 'db').mkdir(parents=True)
            (project / 'db/netlist').write_text('original compiled database')
            for suffix in ('.qsf', '.qpf'):
                (project / ('apf_top' + suffix)).write_text('project')
            target = dict(quartus_sources=[], project_sha256={
                s: runner.digest(project / ('apf_top' + s)) for s in ('.qsf', '.qpf')})
            tools = SimpleNamespace(ROOT=root, find_tool=lambda name: str(root / name))
            with mock.patch.object(runner, 'executor_guard'), \
                 mock.patch.dict(sys.modules, {'tool_paths': tools}):
                self.assertEqual(runner.private_sta(project, target, project / 'audit', []), {})
            self.assertEqual((root / 'project-analysis/db/netlist').read_text(),
                             'original compiled database')
            self.assertFalse((project / 'audit/analysis-project').exists())

    def test_weakened_hold_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            hold = project / 'cpu-hold-margin/pocket_cpu_hold.sdc'
            hold.parent.mkdir()
            hold.write_text('set pocket_cpu_hold_ns 0.10\n')
            hold.with_name('scope.tcl').write_text('# wrong scope\n')
            rows = [dict(assignment='SDC_FILE', path=str(hold))]
            with self.assertRaisesRegex(ValueError, '150 ps'):
                runner.hold_guard(project, rows)


if __name__ == '__main__':
    unittest.main()
