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
             mock.patch.object(runner.subprocess, 'check_output', return_value='  12 python\n  13 quartus_fit\n'):
            with self.assertRaisesRegex(ValueError, 'Legacy Quartus'):
                runner.executor_guard()

    def test_other_jobs_quartus_does_not_block(self):
        env = {'BUILD_URL': 'https://build.session-zero.dev/job/psx-fit/7/', 'JOB_NAME': 'psx-fit',
               'WORKSPACE': '/j/workspace/psx-fit'}
        for cwd, blocks in (('/j/workspace/nds-fit/build', False), ('/j/workspace/psx-fit@2/x', True),
                            ('/srv/checkout/PSX_Pocket', True), (None, True)):
            with self.subTest(cwd=cwd), mock.patch.dict(os.environ, env), \
                 mock.patch.object(runner.subprocess, 'check_output', return_value='  13 quartus_fit\n'), \
                 mock.patch.object(runner.os, 'readlink', side_effect=OSError if cwd is None else (lambda _: cwd)):
                if blocks:
                    with self.assertRaisesRegex(ValueError, 'Legacy Quartus'):
                        runner.executor_guard()
                else:
                    runner.executor_guard()

    def test_second_run_on_a_host_waits_for_the_lock(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(runner, 'HOST_LOCK', Path(directory) / 'host.lock'):
            first = runner.host_lock()
            with self.assertRaisesRegex(ValueError, 'too long'):
                runner.host_lock(wait_s=0.05, poll_s=0.01)
            first.close()
            runner.host_lock(wait_s=0.05, poll_s=0.01).close()

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

    def test_signoff_names_same_clock_and_crossing_setup_paths(self):
        c0, c1, c2 = (f'ic|cpu_pll|general[{i}].gpll~PLL_OUTPUT_COUNTER|divclk' for i in range(3))
        rows = [f'FAIL Slow_1100mV_85C_Model setup -2.305 {c1} -> {c1}: same-clock path',
                f'FAIL Slow_1100mV_0C_Model setup -2.159 {c1} -> {c1}: same-clock path',
                f'FAIL Slow_1100mV_85C_Model setup -0.226 {c0} -> {c2}: crossing without a waiver']
        script = runner.setup_path_script(rows, '/out')
        reports = [l.strip() for l in script.splitlines() if l.strip().startswith('report_timing')]
        self.assertEqual(len(reports), 3)
        self.assertIn(f'-from_clock [get_clocks {{{c0}}}] -to_clock [get_clocks {{{c2}}}]', script)
        self.assertEqual(sum(f'-from_clock [get_clocks {{{c1}}}] -to_clock [get_clocks {{{c1}}}]' in r
                             for r in reports), 2)
        self.assertEqual(len({r.split('-file ')[1] for r in reports}), 3)   # one report file per row
        self.assertTrue(script.rstrip().endswith('project_close -dont_export_assignments'))


if __name__ == '__main__':
    unittest.main()
