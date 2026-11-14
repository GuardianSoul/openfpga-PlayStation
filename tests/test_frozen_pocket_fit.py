from pathlib import Path
import os
import json
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
        for cwd, blocks in (('/j/workspace/nds-fit/build', False), ('/j/workspace/psx-fit@2/x', False), ('/j/workspace/psx-fit/src', True),
                            ('/srv/checkout/PSX_Pocket', True), ('/srv/u/psx-fit/F-nopool', True),
                            ('/srv/u/Git/NDS_Pocket/build/prequeue/x', False), (None, True)):
            with self.subTest(cwd=cwd), mock.patch.dict(os.environ, env), \
                 mock.patch.object(runner.subprocess, 'check_output', return_value='  13 quartus_fit\n'), \
                 mock.patch.object(runner.os, 'readlink', side_effect=OSError if cwd is None else (lambda _: cwd)):
                if blocks:
                    with self.assertRaisesRegex(ValueError, 'Legacy Quartus'):
                        runner.executor_guard()
                else:
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

    def test_failed_cue_cdb_never_assembles_a_stale_rbf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / 'source', root / 'output'
            (source / 'output_files').mkdir(parents=True)
            (source / 'output_files/apf_top.rbf').write_bytes(b'old bitstream')
            (source / 'physical-result.json').write_text(json.dumps(
                dict(fit_verified=True, measurement_valid=True)))
            (source / 'release-timing.json').write_text(json.dumps(dict(passed=True)))
            (source / 'base').write_text('protected base')
            (source / 'signoff-request.json').write_text(json.dumps(dict(
                cue_firmware={}, base_sha256={'base': runner.digest(source / 'base')})))
            tools = SimpleNamespace(ROOT=root, find_tool=lambda _: '/tools/quartus_sh')
            closure = SimpleNamespace(inputs=lambda _: [])
            with mock.patch.object(runner, 'executor_guard'), \
                 mock.patch.dict(sys.modules, {'tool_paths': tools, 'measure_pocket_seeds': closure}), \
                 mock.patch('update_cue_fit.stage', return_value=[]), \
                 mock.patch.object(runner.subprocess, 'run', return_value=SimpleNamespace(returncode=1)) as call:
                with self.assertRaisesRegex(ValueError, 'no assembly permitted'):
                    runner.signoff(SimpleNamespace(source=source, output=output))
            self.assertEqual(call.call_count, 1)
            self.assertEqual(call.call_args.args[0], ['/tools/quartus_cdb', 'apf_top', '--update_mif'])
            self.assertFalse((output / 'output_files/apf_top.rbf').exists())
            self.assertEqual((source / 'output_files/apf_top.rbf').read_bytes(), b'old bitstream')

    def save_update(self, root, inputs_after):
        source, output = root / 'source', root / 'output'
        (source / 'output_files').mkdir(parents=True)
        (source / 'output_files/apf_top.rbf').write_bytes(b'old bitstream')
        (source / 'physical-result.json').write_text(json.dumps(dict(fit_verified=True, measurement_valid=True)))
        (source / 'release-timing.json').write_text(json.dumps(dict(passed=True)))
        (source / 'base').write_text('protected base')
        (source / 'signoff-request.json').write_text(json.dumps(dict(
            save_firmware={}, base_sha256={'base': runner.digest(source / 'base')})))
        rom = [dict(path='/x/save-states/psx_save_rom40.hex', sha256='a'), dict(path='/x/cpu.vhd', sha256='c')]
        tools = SimpleNamespace(ROOT=root, find_tool=lambda _: '/tools/quartus_sh')
        closure = SimpleNamespace(inputs=mock.Mock(side_effect=[rom, inputs_after]))
        lane = [dict(lane='save-states/psx_save_rom40.hex', after_sha256='n', mif='m.hdl.mif', mif_after_sha256='n')]
        return source, output, tools, closure, lane

    def test_failed_save_cdb_never_assembles_a_stale_rbf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            after = [dict(path='/x/save-states/psx_save_rom40.hex', sha256='b'), dict(path='/x/cpu.vhd', sha256='c')]
            source, output, tools, closure, lane = self.save_update(root, after)
            with mock.patch.object(runner, 'executor_guard'), \
                 mock.patch.dict(sys.modules, {'tool_paths': tools, 'measure_pocket_seeds': closure}), \
                 mock.patch('update_save_fit.stage', return_value=lane), \
                 mock.patch('build_save_fw.rom40_from_byte_hex', return_value='rom\n'), \
                 mock.patch.object(runner.subprocess, 'run', return_value=SimpleNamespace(returncode=1)) as call:
                with self.assertRaisesRegex(ValueError, 'no assembly permitted'):
                    runner.signoff(SimpleNamespace(source=source, output=output))
            self.assertEqual(call.call_count, 1)
            self.assertEqual(call.call_args.args[0], ['/tools/quartus_cdb', 'apf_top', '--update_mif'])
            self.assertFalse((output / 'output_files/apf_top.rbf').exists())

    def test_staged_rom40_image_is_staged_as_given(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            after = [dict(path='/x/save-states/psx_save_rom40.hex', sha256='b'), dict(path='/x/cpu.vhd', sha256='c')]
            source, output, tools, closure, lane = self.save_update(root, after)
            image = root / 'new-rom40.hex'
            image.write_text('0123456789\n' * 1024)
            request = json.loads((source / 'signoff-request.json').read_text())
            request['save_firmware'] = dict(rom40=str(image))
            (source / 'signoff-request.json').write_text(json.dumps(request))
            seen = {}

            def fake_stage(project, new_rom, want):
                seen['rom'] = Path(new_rom).read_text(); seen['want'] = dict(want)
                return lane
            with mock.patch.object(runner, 'executor_guard'), \
                 mock.patch.dict(sys.modules, {'tool_paths': tools, 'measure_pocket_seeds': closure}), \
                 mock.patch('update_save_fit.stage', side_effect=fake_stage), \
                 mock.patch('build_save_fw.rom40_from_byte_hex', side_effect=AssertionError('legacy ROM built')), \
                 mock.patch.object(runner.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
                with self.assertRaisesRegex(ValueError, 'no assembly permitted'):
                    runner.signoff(SimpleNamespace(source=source, output=output))
            self.assertEqual(seen['rom'], image.read_text())
            self.assertEqual(seen['want'], {})

    def test_extension_rom_is_staged_with_the_routine_rom(self):
        # a fit with the extension ROM (prepare_save_rom_ext.py): both images reach the stage, both names may change
        ROM, EXT = '/x/save-states/psx_save_rom40.hex', '/x/save-states/psx_save_rom_ext.hex'
        for changed_ext, refused in ((True, None), (False, None), (None, 'neither ROM')):
            with self.subTest(changed_ext=changed_ext), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                before = [dict(path=ROM, sha256='a'), dict(path=EXT, sha256='e'), dict(path='/x/cpu.vhd', sha256='c')]
                after = [dict(path=ROM, sha256='a' if changed_ext is None else 'b'),
                         dict(path=EXT, sha256='f' if changed_ext else 'e'), dict(path='/x/cpu.vhd', sha256='c')]
                source, output, tools, closure, _ = self.save_update(root, after)
                closure.inputs.side_effect = [before, after]
                image, ext = root / 'new-rom40.hex', root / 'new-ext.hex'
                image.write_text('0123456789\n' * 1024); ext.write_text('5a\n' * 1024)
                request = json.loads((source / 'signoff-request.json').read_text())
                request['save_firmware'] = dict(rom40=str(image), before_sha256='a', after_sha256='b',
                                                ext=dict(hex=str(ext), before_sha256='e', after_sha256='f'))
                (source / 'signoff-request.json').write_text(json.dumps(request))
                seen = {}

                def fake_stage(project, new_rom, want, new_ext):
                    seen.update(rom=Path(new_rom).read_text(), ext=Path(new_ext).read_text(), want=want)
                    return [dict(lane='save-states/psx_save_rom40.hex', before_sha256='a', after_sha256=after[0]['sha256'],
                                 mif='m.hdl.mif', mif_after_sha256='n'),
                            dict(lane='save-states/psx_save_rom_ext.hex', before_sha256='e', after_sha256=after[1]['sha256'],
                                 mif='x.hdl.mif', mif_after_sha256='n')]
                with mock.patch.object(runner, 'executor_guard'), \
                     mock.patch.dict(sys.modules, {'tool_paths': tools, 'measure_pocket_seeds': closure}), \
                     mock.patch('update_save_fit.stage', side_effect=fake_stage), \
                     mock.patch.object(runner.subprocess, 'run', return_value=SimpleNamespace(returncode=1)) as call:
                    with self.assertRaisesRegex(ValueError, refused or 'no assembly permitted'):
                        runner.signoff(SimpleNamespace(source=source, output=output))
                self.assertEqual(call.call_count, 0 if refused else 1)
                self.assertEqual((seen['rom'], seen['ext']), (image.read_text(), ext.read_text()))
                self.assertEqual(seen['want'], dict(before_sha256='a', after_sha256='b', ext=dict(before_sha256='e', after_sha256='f')))

    def test_extension_rom_needs_its_image_and_the_staged_rom40(self):
        for firmware in (dict(rom40='IMAGE', ext=dict(hex='relative.hex')), dict(rom40='IMAGE', ext={}),
                         dict(ext=dict(hex='IMAGE'))):
            with self.subTest(firmware=firmware), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, output, tools, closure, lane = self.save_update(root, [])
                image = root / 'image.hex'; image.write_text('00\n')
                fix = lambda v: {k: fix(x) if isinstance(x, dict) else str(image) if x == 'IMAGE' else x for k, x in v.items()}
                request = json.loads((source / 'signoff-request.json').read_text())
                request['save_firmware'] = fix(firmware)
                (source / 'signoff-request.json').write_text(json.dumps(request))
                with mock.patch.object(runner, 'executor_guard'), \
                     mock.patch.dict(sys.modules, {'tool_paths': tools, 'measure_pocket_seeds': closure}), \
                     mock.patch('update_save_fit.stage', return_value=lane) as stage, \
                     mock.patch.object(runner.subprocess, 'run') as call:
                    with self.assertRaisesRegex(ValueError, 'ext needs rom40 and hex'):
                        runner.signoff(SimpleNamespace(source=source, output=output))
                self.assertEqual((call.call_count, stage.call_count), (0, 0))

    def test_staged_rom40_needs_an_absolute_existing_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, tools, closure, lane = self.save_update(root, [])
            request = json.loads((source / 'signoff-request.json').read_text())
            request['save_firmware'] = dict(rom40='missing.hex')
            (source / 'signoff-request.json').write_text(json.dumps(request))
            with mock.patch.object(runner, 'executor_guard'), \
                 mock.patch.dict(sys.modules, {'tool_paths': tools, 'measure_pocket_seeds': closure}), \
                 mock.patch('update_save_fit.stage', return_value=lane), \
                 mock.patch.object(runner.subprocess, 'run') as call:
                with self.assertRaisesRegex(ValueError, 'rom40 must be an absolute path'):
                    runner.signoff(SimpleNamespace(source=source, output=output))
            self.assertEqual(call.call_count, 0)

    def test_save_update_that_changes_other_inputs_is_refused_before_cdb(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            after = [dict(path='/x/save-states/psx_save_rom40.hex', sha256='b'), dict(path='/x/cpu.vhd', sha256='d')]
            source, output, tools, closure, lane = self.save_update(root, after)
            with mock.patch.object(runner, 'executor_guard'), \
                 mock.patch.dict(sys.modules, {'tool_paths': tools, 'measure_pocket_seeds': closure}), \
                 mock.patch('update_save_fit.stage', return_value=lane), \
                 mock.patch('build_save_fw.rom40_from_byte_hex', return_value='rom\n'), \
                 mock.patch.object(runner.subprocess, 'run') as call:
                with self.assertRaisesRegex(ValueError, 'non-ROM inputs'):
                    runner.signoff(SimpleNamespace(source=source, output=output))
            self.assertEqual(call.call_count, 0)

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

    def test_named_paths_default_row_keeps_the_summary_report(self):
        script = runner.named_path_script([{'name': 'a', 'from': '*|x[*]', 'to': '*|y'}], '/out')
        reports = [l for l in script.splitlines() if 'report_timing' in l]
        self.assertEqual(len(reports), 2)                                   # setup and hold
        for kind, line in zip(('setup', 'hold'), reports):
            self.assertIn(f'report_timing -{kind} -from [get_registers {{*|x[*]}}] -to [get_registers {{*|y}}] '
                          '-npaths 2 -detail summary', line)
            # one file per corner: $n must sit outside braces or every corner overwrites one file
            self.assertIn(f'-file [file join {{/out}} named-0-{kind}-corner$n.txt]', line)
            self.assertEqual(line.count('{'), line.count('}'))
        self.assertIn('NAMED_CORNER $n [get_operating_conditions_info -display_name $op]', script)
        self.assertTrue(script.rstrip().endswith('project_close -dont_export_assignments'))

    def test_named_paths_row_may_ask_for_full_paths(self):
        script = runner.named_path_script([{'from': 'a', 'to': 'b'},
                                           {'from': 'c', 'to': 'd', 'detail': 'full_path', 'npaths': 4}], '/out')
        self.assertEqual(script.count('-npaths 2 -detail summary'), 2)
        self.assertEqual(script.count('-npaths 4 -detail full_path'), 2)
        for bad in ({'detail': 'everything'}, {'npaths': 0}, {'npaths': 500}):
            with self.assertRaises(ValueError):
                runner.named_path_script([{'from': 'a', 'to': 'b', **bad}], '/out')

    def test_named_reports_are_read_per_corner_with_full_path_facts(self):
        summary = ('; Slack ; From Node ; To Node ; Launch Clock ; Latch Clock ; Relationship ; Clock Skew ; Data Delay ;\n'
                   '; -0.897 ; r|xCnt[1] ; r|state.state_bit_2 ; c ; c ; 14.762 ; -0.116 ; 15.423 ;\n')
        full = summary + '\n'.join((
            'Path #1: Setup slack is -0.897 (VIOLATED)', '; Path Summary ;',
            '; From Node ; r|xCnt[1] ;', '; To Node ; r|state.state_bit_2 ;', '; Statistics ;',
            '; Property ; Value ; Count ; Total Delay ; % of Total ; Min ; Max ;',
            '; Setup Relationship ; 14.762 ; ; ; ; ; ;', '; Clock Skew ; -0.116 ; ; ; ; ; ;',
            '; Data Delay ; 15.423 ; ; ; ; ; ;', '; Number of Logic Levels ; ; 11 ; ; ; ; ;',
            '; Arrival Path ; ; ; ; ; ; ;', '; Clock ; ; ; ; ; ; ;', '; IC ; ; 2 ; 4.944 ; 83 ; 1.984 ; 2.960 ;',
            '; Cell ; ; 2 ; 1.003 ; 17 ; 0.348 ; 0.655 ;', '; Data ; ; ; ; ; ; ;',
            '; IC ; ; 19 ; 7.800 ; 51 ; 0.000 ; 0.947 ;', '; Cell ; ; 20 ; 7.623 ; 49 ; 0.000 ; 1.256 ;',
            '; Required Path ; ; ; ; ; ; ;', '; Clock ; ; ; ; ; ; ;', '; IC ; ; 2 ; 2.459 ; 73 ; 0.564 ; 1.895 ;',
            '; Data Arrival Path ;', '; Total ; Incr ; RF ; Type ; Fanout ; Location ; Element ;',
            '; 3.308 ; 0.348 ; RR ; CELL ; 5438 ; CLKCTRL_G13 ; clk~CLKENA0|outclk ;',
            '; 19.750 ; 13.803 ; ; ; ; ; data path ;',
            '; 6.733 ; 0.786 ; RR ; IC ; 2 ; LABCELL_X24_Y29_N0 ; r|Add15~13|datac ;',
            '; 7.762 ; 1.029 ; RR ; CELL ; 1 ; LABCELL_X24_Y29_N0 ; r|Add15~13|cout ;',
            '; Data Required Path ;', '; 15.645 ; 0.318 ; RR ; CELL ; 5438 ; CLKCTRL_G13 ; clk~CLKENA0|outclk ;')) + '\n'
        rows = [{'name': 'tag', 'from': 'a', 'to': 'b'}, {'name': 'rect', 'from': 'c', 'to': 'd', 'detail': 'full_path'}]
        with tempfile.TemporaryDirectory() as root:
            out = Path(root)
            (out / 'named-0-setup-corner1.txt').write_text(summary.replace('-0.897', '0.500'))
            (out / 'named-0-setup-corner2.txt').write_text(summary.replace('-0.897', '0.700'))
            (out / 'named-1-setup-corner1.txt').write_text(full)
            (out / 'named-1-hold-corner1.txt').write_text('No paths to report.\n')
            log = ('NAMED_CORNER 1 Slow 1100mV 85C Model\nInfo: NAMED_CORNER 2 Slow 1100mV 0C Model\n'
                   'NAMED_ERROR 0 hold 2 no registers matched\n')
            named, details = runner.parse_named_reports(rows, out, log)
        self.assertIn('tag setup Slow 1100mV 85C Model: 0.500   r|xCnt[1]   r|state.state_bit_2', named)
        self.assertIn('tag setup Slow 1100mV 0C Model: 0.700   r|xCnt[1]   r|state.state_bit_2', named)   # both corners kept
        self.assertIn('rect hold Slow 1100mV 85C Model: no path found', named)
        self.assertIn('tag hold Slow 1100mV 0C Model: report failed: no registers matched', named)
        # the full-path file contributes one summary row, not its arrival-table rows
        self.assertEqual([n for n in named if n.startswith('rect setup')],
                         ['rect setup Slow 1100mV 85C Model: -0.897   r|xCnt[1]   r|state.state_bit_2'])
        self.assertEqual(len(details), 1)
        fact = details[0]
        self.assertEqual((fact['name'], fact['check'], fact['corner']), ('rect', 'setup', 'Slow 1100mV 85C Model'))
        self.assertEqual((fact['slack_ns'], fact['clock_skew_ns'], fact['data_delay_ns'], fact['logic_levels']),
                         (-0.897, -0.116, 15.423, 11))
        self.assertEqual((fact['data_ic_ns'], fact['data_cell_ns']), (7.8, 7.623))   # data rows, not the clock rows
        self.assertEqual(fact['elements'], ['0.786 IC 2 LABCELL_X24_Y29_N0 r|Add15~13|datac',
                                            '1.029 CELL 1 LABCELL_X24_Y29_N0 r|Add15~13|cout'])

    # --- the tight tail (request 'tail') ---
    C0, C1 = (f'ic|cpu_pll|general[{i}].gpll~PLL_OUTPUT_COUNTER|divclk' for i in range(2))
    VID = 'ic|video_pll|general[0].gpll~PLL_OUTPUT_COUNTER|divclk'
    CORE = 'core_top:ic|psx_mister_psx_top_resource:psx|psx_top:component_core|'
    PERIODS = {C0: 29.524, C1: 14.762, VID: 18.624}
    WAIVERS = [dict({'from': 'clk2x', 'to': 'clk1x'}, check='hold', floor_ns=-0.05, reason='r'),
               dict({'from': '*video*', 'to': 'clk2x'}, check='setup', floor_ns=-6.0, reason='r')]

    @classmethod
    def tail_table(cls, rows):
        """A report_timing -detail summary file as Quartus 25.1 writes it (layout of the
        captured paths-Slow_1100mV_85C_Model-11.txt), rows = (slack, from, to, launch, latch)."""
        bar = '+--------+------+------+------+------+--------------+------------+------------+\n'
        return (bar + '; Report Timing ;\n' + bar +
                '; Slack  ; From Node ; To Node ; Launch Clock ; Latch Clock ; Relationship ; Clock Skew ; Data Delay ;\n' + bar +
                ''.join(f'; {s} ; {a:60} ; {b} ; {l} ; {t} ; 14.762       ; -0.176     ; 14.869     ;\n'
                        for s, a, b, l, t in rows) + bar)

    def test_no_tail_request_leaves_the_generated_tcl_byte_identical(self):
        import hashlib
        c0, c1, c2 = (f'ic|cpu_pll|general[{i}].gpll~PLL_OUTPUT_COUNTER|divclk' for i in range(3))
        rows = [f'FAIL Slow_1100mV_85C_Model setup -2.305 {c1} -> {c1}: same-clock path',
                f'FAIL Fast_1100mV_0C_Model hold -0.001 {c1} -> {c1}: same-clock path',
                f'FAIL Slow_1100mV_85C_Model setup -0.226 {c0} -> {c2}: crossing without a waiver']
        named = [{'name': 'a', 'from': '*|x[*]', 'to': '*|y'}, {'from': 'c', 'to': 'd', 'detail': 'full_path', 'npaths': 4}]
        sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
        # hashes of the scripts the runner of b2b9596f (before the tail report) generates for these rows
        self.assertEqual(sha(runner.setup_path_script(rows, '/out')),
                         '8c67eac02cd742b381fcf2080135f7557d1fdbe084ce3ce85063d67d7fff5beb')
        self.assertEqual(sha(runner.named_path_script(named, '/out')),
                         'ab9d15f1baa499ccddfe84d84651e01fb3388cffb86b5645ae0736fa612d506b')

    def test_tail_request_is_checked(self):
        self.assertEqual(runner.tail_request({}), dict(setup_below_ns=0.3, hold_below_ns=0.1, max_paths=5000))
        self.assertEqual(runner.tail_request({'setup_below_ns': 1, 'hold_below_ns': 0.05, 'max_paths': 20}),
                         dict(setup_below_ns=1.0, hold_below_ns=0.05, max_paths=20))
        for bad in ([], 'yes', {'setup': 0.3}, {'setup_below_ns': '0.3'}, {'hold_below_ns': 9}, {'max_paths': 0},
                    {'max_paths': 2.5}, {'max_paths': 10**7}, {'setup_below_ns': True}):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'Unsupported tail request'):
                runner.tail_request(bad)

    def test_tail_script_writes_one_file_per_corner_and_check(self):
        script = runner.tail_script({'setup_below_ns': 0.3, 'hold_below_ns': 0.1, 'max_paths': 5000}, '/out')
        calls = [l for l in script.splitlines() if l.startswith('  tail_report ')]
        self.assertEqual(calls, ['  tail_report setup 0.300 5000 $n [file join {/out} tail-setup-corner$n.txt]',
                                 '  tail_report hold 0.100 5000 $n [file join {/out} tail-hold-corner$n.txt]'])
        for line in calls:      # $n must sit outside braces or every corner overwrites one file
            self.assertNotRegex(line, r'\{[^{}]*\$n[^{}]*\}')
        self.assertEqual(script.count('{'), script.count('}'))
        self.assertIn('report_timing -$kind -less_than_slack $limit -npaths $npaths {*}$extra -detail summary -file $file', script)
        self.assertNotIn('-from_clock', script)            # every clock pair, as release_timing.tcl visits them
        self.assertIn('TAIL_CORNER $n [get_operating_conditions_info -display_name $op]', script)
        self.assertLess(script.index('read_sdc'), script.index('set_operating_conditions'))
        self.assertTrue(script.rstrip().endswith('project_close -dont_export_assignments'))

    def test_register_bank_strips_bits_copies_and_state_values(self):
        r = self.CORE + 'gpu:igpu|gpu_rect:igpu_rect|'
        tag = self.CORE + 'cpu:icpu|pocket_dpram_sdp:itagram|altsyncram:altsyncram_component|altsyncram_hh24:auto_generated|'
        for name, bank in ((r + 'xCnt[1]', r + 'xCnt'), (r + 'xCnt[10]~DUPLICATE', r + 'xCnt'),
                           (r + 'state.state_bit_2', r + 'state'), (r + 'state.IDLE~DUPLICATE', r + 'state'),
                           (self.CORE + 'vram_pause~DUPLICATE', self.CORE + 'vram_pause'),
                           (self.CORE + 'cpu:icpu|regs[3][17]', self.CORE + 'cpu:icpu|regs'),
                           (self.CORE + 'cpu:icpu|writebackTarget[2]', self.CORE + 'cpu:icpu|writebackTarget'),
                           (tag + 'ram_block1a0~portb_address_reg2', tag + 'ram_block1a*~portb_address_reg'),
                           (tag + 'ram_block1a17~porta_we_reg', tag + 'ram_block1a*~porta_we_reg'),
                           (tag + 'ram_block1a17', tag + 'ram_block1a*'),
                           ('core_top:ic|gen[3].u|q[0]', 'core_top:ic|gen[3].u|q'),      # only the last part is cut
                           ('cart_tran_bank0[4]', 'cart_tran_bank0'), ('plain', 'plain')):
            with self.subTest(name=name):
                self.assertEqual(runner.register_bank(name), bank)

    def tail_capture(self, out):
        r, c = self.CORE + 'gpu:igpu|gpu_rect:igpu_rect|', self.CORE + 'cpu:icpu|'
        tag = c + 'pocket_dpram_sdp:itagram|altsyncram:altsyncram_component|altsyncram_hh24:auto_generated|'
        slow85 = [('-0.897', r + 'xCnt[1]', r + 'state.state_bit_2', self.C1, self.C1),
                  ('-0.481', r + 'xCnt[3]~DUPLICATE', r + 'state.state_bit_0', self.C1, self.C1),
                  ('-0.482', c + 'writebackTarget[2]', tag + 'ram_block1a0~portb_address_reg2', self.C0, self.C0),
                  ('-5.681', 'core_top:ic|videoout_request', 'core_top:ic|request_s1', self.VID, self.C1),   # waived
                  ('-6.200', 'core_top:ic|videoout_reports[1]', 'core_top:ic|reports_s1[1]', self.VID, self.C1),  # below floor
                  ('0.250', 'cart_tran_bank0[4]', 'core_top:ic|pad_in[4]', self.C0, self.C0)]
        slow0 = [('-0.300', r + 'xCnt[1]', r + 'state.state_bit_2', self.C1, self.C1),
                 ('0.120', r + 'yCnt[0]', r + 'state.state_bit_2', self.C1, self.C1)]
        fast0 = [('-0.001', c + 'a[0]', c + 'b[0]', self.C1, self.C1),
                 ('-0.035', c + 'gte_q[1]', c + 'resultData[1]', self.C1, self.C0),          # waived (floor -0.05)
                 ('0.040', c + 'gte_q[2]', c + 'resultData[2]', self.C1, self.C0)]            # not negative: listed
        (out / 'tail-setup-corner1.txt').write_text(self.tail_table(slow85))
        (out / 'tail-setup-corner2.txt').write_text(self.tail_table(slow0))
        (out / 'tail-hold-corner4.txt').write_text(self.tail_table(fast0))
        for name in ('tail-setup-corner3.txt', 'tail-setup-corner4.txt', 'tail-hold-corner1.txt',
                     'tail-hold-corner2.txt', 'tail-hold-corner3.txt'):
            (out / name).write_text('No paths to report.\n')
        return ('TAIL_PORT cart_tran_bank0[4]\nTAIL_CORNER 1 Slow 1100mV 85C Model\nTAIL_COUNT setup 1 6 -6.200 pairs_only\n'
                'Info: TAIL_CORNER 2 Slow 1100mV 0C Model\nTAIL_CORNER 3 Fast 1100mV 85C Model\n'
                'TAIL_CORNER 4 Fast 1100mV 0C Model\nTAIL_COUNT hold 4 3 -0.035 pairs_only\n')

    def test_tail_reports_are_grouped_by_clock_pair_check_and_register_bank(self):
        tail = {'setup_below_ns': 0.3, 'hold_below_ns': 0.1, 'max_paths': 5000}
        with tempfile.TemporaryDirectory() as root:
            log = self.tail_capture(Path(root))
            groups, info = runner.parse_tail_reports(root, log, tail, self.PERIODS, self.WAIVERS)
            text = runner.tail_summary(groups, info)
        r = self.CORE + 'gpu:igpu|gpu_rect:igpu_rect|'
        self.assertEqual([g['worst_slack_ns'] for g in groups], [-6.2, -0.897, -0.482, -0.001, 0.04, 0.12, 0.25])
        rect = groups[1]
        self.assertEqual((rect['from_bank'], rect['to_bank'], rect['check'], rect['launch'], rect['latch']),
                         (r + 'xCnt', r + 'state', 'setup', 'clk2x', 'clk2x'))
        self.assertEqual((rect['launch_clock'], rect['latch_clock']), (self.C1, self.C1))
        self.assertEqual(rect['paths'], 2)                       # xCnt[1] and xCnt[3]; xCnt[1] at two corners is one path
        self.assertEqual((rect['worst_corner'], rect['worst_from'], rect['worst_to']),
                         ('Slow 1100mV 85C Model', r + 'xCnt[1]', r + 'state.state_bit_2'))
        self.assertEqual(rect['corners'], {'Slow 1100mV 85C Model': dict(worst_slack_ns=-0.897, paths=2),
                                           'Slow 1100mV 0C Model': dict(worst_slack_ns=-0.3, paths=1)})
        self.assertTrue(groups[2]['to_bank'].endswith('ram_block1a*~portb_address_reg'))
        self.assertEqual((groups[3]['check'], groups[3]['worst_corner']), ('hold', 'Fast 1100mV 0C Model'))
        # waived rows (release_timing: different clocks, negative, covered, not below the floor) are left out and counted
        self.assertEqual([(w['check'], w['paths'], w['worst_slack_ns'], w['floor_ns']) for w in info['waived_excluded']],
                         [('hold', 1, -0.035, -0.05), ('setup', 1, -5.681, -6.0)])
        self.assertEqual((groups[0]['from_bank'], groups[0]['launch'], groups[0]['latch']),
                         ('core_top:ic|videoout_reports', self.VID, 'clk2x'))             # below the floor: kept
        self.assertEqual((groups[4]['check'], groups[4]['launch'], groups[4]['latch']), ('hold', 'clk2x', 'clk1x'))
        self.assertEqual([g['io'] for g in groups], [False] * 6 + [True])
        self.assertTrue(info['complete'])
        self.assertEqual(len(info['reports']), 8)
        self.assertEqual(info['corners'][3], 'Fast 1100mV 0C Model')
        self.assertIn('setup -0.897 slow85C      2   -0.897   -0.300        -        - clk2x->clk2x', text)
        self.assertIn('waived, not listed: setup', text)
        json.dumps(groups), json.dumps(info)                     # plain JSON values only

    def test_tail_reports_say_when_they_are_cut_or_failed(self):
        with tempfile.TemporaryDirectory() as root:
            log = self.tail_capture(Path(root))
            groups, info = runner.parse_tail_reports(root, log, {'max_paths': 6}, self.PERIODS, self.WAIVERS)
            self.assertFalse(info['complete'])
            cut = [r for r in info['reports'] if r['truncated']]
            self.assertEqual([(r['check'], r['corner'], r['complete_below_ns']) for r in cut],
                             [('setup', 'Slow 1100mV 85C Model', 0.25)])
            self.assertIn('TRUNCATED setup Slow 1100mV 85C Model', runner.tail_summary(groups, info))
            _, info = runner.parse_tail_reports(root, log + 'TAIL_ERROR hold 3 no such option\n', {}, self.PERIODS, self.WAIVERS)
            self.assertEqual(info['errors'], ['hold Fast 1100mV 85C Model: no such option'])
            self.assertFalse(info['complete'])
            # without clock periods no alias matches: nothing is waived, and the result says so
            groups, info = runner.parse_tail_reports(root, log, {}, {}, self.WAIVERS)
            self.assertEqual((info['waived_excluded'], info['waiver_clock_periods_known']), ([], False))
            self.assertEqual(len(groups), 8)       # the two waived rows are listed

    def signoff_run(self, root, request):
        """signoff() with every tool mocked: release_timing passes; the tail script 'writes' the capture."""
        source, output = root / 'source', root / 'output'
        source.mkdir()
        (source / 'physical-result.json').write_text(json.dumps(dict(fit_verified=True, measurement_valid=True)))
        (source / 'apf_top.qsf').write_text('set_global_assignment -name SEED 6\n')
        (source / 'signoff-request.json').write_text(json.dumps(request))
        calls = []

        def tool(command, **kwargs):
            calls.append([str(c) for c in command])
            if calls[-1][-1].endswith('signoff-tail.tcl'):
                kwargs['stdout'].write(self.tail_capture(output)); kwargs['stdout'].flush()
            if 'release_timing.py' in calls[-1][1]:
                (output / 'release-timing.json').write_text(json.dumps(dict(passed=True, clocks=self.PERIODS)))
            return SimpleNamespace(returncode=0)
        tools = SimpleNamespace(ROOT=Path(runner.__file__).resolve().parents[1], find_tool=lambda _: '/tools/quartus_sh')
        with mock.patch.object(runner, 'executor_guard'), mock.patch.dict(sys.modules, {'tool_paths': tools}), \
             mock.patch.object(runner, 'job_context', return_value={}), \
             mock.patch.object(runner.subprocess, 'run', side_effect=tool), \
             mock.patch('builtins.print'):
            runner.signoff(SimpleNamespace(source=source, output=output, result_file=None))
        return output, calls, json.loads((output / 'frozen-fit-result.json').read_text())

    def test_signoff_without_a_tail_request_runs_and_reports_as_before(self):
        with tempfile.TemporaryDirectory() as directory:
            output, calls, result = self.signoff_run(Path(directory), dict(requested_by='test'))
            self.assertEqual([Path(c[0]).name if 'quartus' in c[0] else Path(c[1]).name for c in calls],
                             ['release_timing.py', 'quartus_asm'])
            self.assertFalse(list(output.glob('*tail*')))
            self.assertNotIn('tail', result)
            self.assertNotIn('tail_info', result)
            self.assertEqual(list(result)[-2:], ['save_abort_update', 'save_trace_bundle'])   # the key set of before

    def test_signoff_with_a_tail_request_adds_the_groups_and_the_text_table(self):
        with tempfile.TemporaryDirectory() as directory:
            output, calls, result = self.signoff_run(
                Path(directory), dict(tail={'setup_below_ns': 0.3, 'hold_below_ns': 0.1, 'max_paths': 5000}))
            self.assertEqual([Path(c[0]).name if 'quartus' in c[0] else Path(c[1]).name for c in calls],
                             ['release_timing.py', 'quartus_sta', 'quartus_asm'])
            self.assertEqual(calls[1][1:], ['-t', str(output / 'signoff-tail.tcl')])
            self.assertIn(f'[file join {{{output}}} tail-setup-corner$n.txt]', (output / 'signoff-tail.tcl').read_text())
            # the real waiver file: "*video*" -> clk1x/clk2x setup is retired (hold-constraints-060),
            # so the -5.681 video row is listed now instead of waived
            self.assertEqual(result['tail'][1]['worst_slack_ns'], -5.681)
            self.assertEqual(result['tail'][2]['worst_slack_ns'], -0.897)
            self.assertEqual(result['tail'][2]['launch'], 'clk2x')          # periods from release-timing.json
            self.assertIs(result['tail_info']['cpu_hold_sdc_removed'], False)
            self.assertTrue((output / 'tail-summary.txt').read_text().startswith('tail: setup < 0.3 ns, hold < 0.1 ns'))

    def test_bad_tail_request_stops_before_the_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'Unsupported tail request'):
                self.signoff_run(Path(directory), dict(tail={'setup_below': 0.3}))
            self.assertFalse((Path(directory) / 'output').exists())

    def test_reader_sorts_groups_and_marks_groups_absent_in_another_build(self):
        import contextlib, io
        import tail_report
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, limit in (('ssA', 5000), ('ssB', 5000), ('ssC', 6)):
                (root / name).mkdir()
                log = self.tail_capture(root / name)
                if name == 'ssB':      # this build has no tag-RAM group and a better rect group
                    table = (root / name / 'tail-setup-corner1.txt').read_text().splitlines(keepends=True)
                    (root / name / 'tail-setup-corner1.txt').write_text(
                        ''.join(l for l in table if 'itagram' not in l).replace('-0.897', '-0.100'))
                groups, info = runner.parse_tail_reports(root / name, log, {'max_paths': limit}, self.PERIODS, self.WAIVERS)
                info['cpu_hold_sdc_removed'] = True
                (root / name / 'frozen-fit-result.json').write_text(json.dumps(dict(
                    tail=groups, tail_info=info, release_timing_passed=False)))
            builds = [tail_report.load(str(root / 'ssA')), tail_report.load(f"B={root / 'ssB' / 'frozen-fit-result.json'}"),
                      tail_report.load(str(root / 'ssC'))]
            self.assertEqual([b['label'] for b in builds], ['ssA', 'B', 'ssC'])
            rows = tail_report.comparison(builds[:2])
            slacks = [min(v for c in cells for v in c.values() if v is not None) for _, _, cells in rows]
            self.assertEqual(slacks, sorted(slacks))
            tag = [r for r in rows if 'ram_block1a*' in r[1][3]]
            self.assertEqual([(m, cells) for m, _, cells in tag],
                             [(True, [dict(setup=-0.482, hold=None), dict(setup=None, hold=None)])])
            rect = [r for r in rows if r[1][2].endswith('|xCnt')][0]
            self.assertEqual((rect[0], [c['setup'] for c in rect[2]]), (False, [-0.897, -0.481]))
            self.assertEqual(sum(m for m, _, _ in rows), 1)
            text = tail_report.comparison_text(builds)
            line = [l for l in text.splitlines() if 'ram_block1a*' in l][0]
            self.assertTrue(line.startswith('* '))
            self.assertRegex(line, r'-0\.482/>lim +>lim/>lim +-0\.482/\?')     # ssC is cut: absence proves nothing
            self.assertIn('ic|psx|component_core|icpu|writebackTarget -> ', line)   # instance names only
            one = tail_report.build_text(builds[0])
            self.assertLess(one.index('-0.897'), one.index('-0.482'))
            self.assertIn('hold-margin SDC removed: True', one)
            (root / 'old').mkdir()
            (root / 'old' / 'frozen-fit-result.json').write_text('{"mode": "signoff"}')
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                tail_report.load(str(root / 'old'))

    # --- video sync constraint (prepare_video_sync_constraints.py) and 'extra_sdc' ---
    def sync_signoff(self, root, request, sdc, delays=(6.1, 5.2), new_rt=0, old_rt=1):
        """signoff() with the tools mocked. release_timing 'sees' whether pocket.sdc holds the block:
        without it the video rows are the old -6.037 / -5.652; with it they are gone."""
        import prepare_video_sync_constraints as sync
        source, output = root / 'source', root / 'output'
        source.mkdir()
        (source / 'physical-result.json').write_text(json.dumps(dict(fit_verified=True, measurement_valid=True)))
        (source / 'apf_top.qsf').write_text('set_global_assignment -name SDC_FILE pocket.sdc\n')
        (source / 'pocket.sdc').write_text(sdc)
        (source / 'signoff-request.json').write_text(json.dumps(request))
        calls, a = [], self.CORE + 'gpu:igpu|gpu_videoout:igpu_videoout|gpu_videoout_async:igpu_videoout_async|'
        old = dict(passed=False, clocks=self.PERIODS, waived=[
            dict(corner='Slow_1100mV_85C_Model', check='setup', slack=-5.652, **{'from': self.VID, 'to': self.C0})],
            failures=[dict(corner='Slow_1100mV_85C_Model', check='setup', slack=-6.037, why='below waiver floor -6.0',
                           **{'from': self.VID, 'to': self.C1}),
                      dict(corner='Fast_1100mV_0C_Model', check='hold', slack=-0.074, why='below waiver floor -0.06',
                           **{'from': self.C0, 'to': self.C1})])

        def tool(command, **kwargs):
            calls.append([str(c) for c in command])
            block = sync.BEGIN in (output / 'pocket.sdc').read_text()
            if 'release_timing.py' in calls[-1][1]:
                calls[-1].append('block' if block else 'no block')
                Path(calls[-1][calls[-1].index('--output') + 1]).write_text(
                    json.dumps(dict(passed=new_rt == 0, clocks=self.PERIODS, waived=[], failures=[]) if block else old))
                return SimpleNamespace(returncode=new_rt if block else old_rt)
            if calls[-1][-1].endswith('signoff-video-sync.tcl'):
                for n, corner in enumerate(('Slow 1100mV 85C Model', 'Slow 1100mV 0C Model', 'Fast 1100mV 85C Model',
                                            'Fast 1100mV 0C Model'), 1):
                    kwargs['stdout'].write(f'VIDEO_SYNC_CORNER {n} {corner}\n')
                    rows = [(f'{10 - d - 0.4:.3f}', a + f'xmax[{i}]', a + f'videoout_request_s1.fetchsize[{i}]', self.VID, self.C1)
                            for i, d in enumerate(delays)]
                    text = self.tail_table(rows)
                    for (_, _, _, _, _), d in zip(rows, delays):       # the Data Delay column of each row
                        text = text.replace('; 14.762       ; -0.176     ; 14.869     ;',
                                            f'; 10.000       ; -0.400     ; {d:.3f}     ;', 1)
                    (output / f'video-sync-setup-corner{n}.txt').write_text(text)
                    (output / f'video-sync-hold-corner{n}.txt').write_text(self.tail_table(
                        [('15.300', a + 'xmax[0]', a + 'videoout_request_s1.fetchsize[0]', self.VID, self.C1)]))
                kwargs['stdout'].flush()
            return SimpleNamespace(returncode=0)
        tools = SimpleNamespace(ROOT=Path(runner.__file__).resolve().parents[1], find_tool=lambda _: '/tools/quartus_sh')
        with mock.patch.object(runner, 'executor_guard'), mock.patch.dict(sys.modules, {'tool_paths': tools}), \
             mock.patch.object(runner, 'job_context', return_value={}), \
             mock.patch.object(runner.subprocess, 'run', side_effect=tool), \
             mock.patch('builtins.print'):
            ok = runner.signoff(SimpleNamespace(source=source, output=output, result_file=None))
        names = [Path(c[0]).name if 'quartus' in c[0] else Path(c[1]).name + ' ' + c[-1] for c in calls]
        return output, names, json.loads((output / 'frozen-fit-result.json').read_text()), ok

    def test_project_without_the_video_sync_marker_is_signed_off_as_before(self):
        with tempfile.TemporaryDirectory() as directory:
            output, names, result, _ = self.sync_signoff(Path(directory), dict(requested_by='test'), 'set_false_path -to x\n', old_rt=0)
            self.assertEqual(names, ['release_timing.py no block', 'quartus_asm'])    # one run, no extra script
            self.assertEqual((output / 'pocket.sdc').read_text(), 'set_false_path -to x\n')
            self.assertFalse(list(output.glob('*video-sync*')) + list(output.glob('*old-waiver*')))
            for key in ('video_sync_constraint', 'video_crossing_old_waiver', 'extra_sdc', 'timing_only'):
                self.assertNotIn(key, result)
            self.assertEqual(list(result)[-2:], ['save_abort_update', 'save_trace_bundle'])

    def test_extra_sdc_applies_the_constraint_without_refit_and_reports_both_rules(self):
        import prepare_video_sync_constraints as sync
        with tempfile.TemporaryDirectory() as directory:
            output, names, result, ok = self.sync_signoff(
                Path(directory), dict(extra_sdc=sync.constraint_lines()), 'set_false_path -to x\n')
            # the old rule first, on the SDC without the block; then the verdict run with it; never assembled
            self.assertEqual(names, ['release_timing.py no block', 'release_timing.py block', 'quartus_sta'])
            self.assertTrue(ok)
            self.assertEqual((output / 'pocket.sdc').read_text(),
                             'set_false_path -to x\n' + '\n'.join(sync.constraint_lines()) + '\n')
            self.assertEqual((Path(directory) / 'source/pocket.sdc').read_text(), 'set_false_path -to x\n')
            old = result['video_crossing_old_waiver']
            self.assertEqual((old['worst_slack_ns'], old['would_pass'], old['release_timing_passed']), (-6.037, False, False))
            self.assertEqual([(r['slack_ns'], r['verdict']) for r in old['rows']], [(-6.037, 'FAIL'), (-5.652, 'waived')])
            new = result['video_sync_constraint']
            self.assertEqual((new['bound_ns'], new['passed'], new['failures'], len(new['corners'])), (10.0, True, [], 4))
            corner = new['corners']['Slow 1100mV 85C Model']
            self.assertEqual((corner['paths'], corner['worst_setup_slack_ns'], corner['max_data_delay_ns'],
                              corner['worst_hold_slack_ns']), (2, 3.5, 6.1, 15.3))
            self.assertTrue(corner['worst_setup_to'].endswith('videoout_request_s1.fetchsize[0]'))
            self.assertTrue(result['release_timing_passed'])
            self.assertEqual((result['timing_only'], result['assembly_exit_code']), (True, None))
            script = (output / 'signoff-video-sync.tcl').read_text()
            for pattern in sync.patterns():
                self.assertIn(pattern, script)
            self.assertIn(f'[file join {{{output}}} video-sync-setup-corner$n.txt]', script)

    def test_covered_path_above_the_bound_fails_even_where_the_waiver_would_pass_it(self):
        import prepare_video_sync_constraints as sync
        with tempfile.TemporaryDirectory() as directory:
            sdc = 'set_false_path -to x\n' + '\n'.join(sync.constraint_lines()) + '\n'       # a project fitted with the block
            output, names, result, ok = self.sync_signoff(Path(directory), dict(requested_by='test'), sdc, delays=(10.6, 5.2))
            self.assertEqual(names, ['release_timing.py no block', 'release_timing.py block', 'quartus_sta', 'quartus_sta'])
            self.assertFalse(ok)
            self.assertFalse(result['release_timing_passed'])
            self.assertEqual(result['release_timing_exit_code'], 0)       # the table alone would have passed
            self.assertEqual(len(result['video_sync_constraint']['failures']), 4)       # one path, four corners
            self.assertRegex(result['setup_failures'][0], r'^FAIL Slow_1100mV_85C_Model setup -1\.000 .*video sync bound 10\.0 ns '
                                                          r'\(data delay 10\.6\) at .*fetchsize\[0\]$')
            self.assertIsNone(result['assembly_exit_code'])
            self.assertEqual((output / 'pocket.sdc').read_text(), sdc)                  # the block is back in place

    def test_video_sync_report_without_paths_fails_closed(self):
        report = runner.parse_video_sync('/nonexistent', 'VIDEO_SYNC_CORNER 1 Slow 1100mV 85C Model\n', 10.0)
        self.assertFalse(report['passed'])
        self.assertEqual(len(report['failures']), 2)       # no rows at the corner, and not four corners
        self.assertTrue(all(' video-sync ' in l for l in report['failures']))

    def test_data_delay_above_the_bound_fails_with_positive_slack(self):
        a = self.CORE + 'gpu:igpu|gpu_videoout:igpu_videoout|gpu_videoout_async:igpu_videoout_async|'
        row = [('0.500', a + 'xmax[7]', a + 'videoout_request_s1.fetchsize[7]', self.VID, self.C1)]
        with tempfile.TemporaryDirectory() as root:
            log = ''
            for n, corner in enumerate(('Slow 1100mV 85C Model', 'Slow 1100mV 0C Model', 'Fast 1100mV 85C Model',
                                        'Fast 1100mV 0C Model'), 1):
                log += f'VIDEO_SYNC_CORNER {n} {corner}\n'
                late = self.tail_table(row).replace('; 14.869     ;', '; 10.600     ;' if n == 1 else '; 6.037     ;')
                (Path(root) / f'video-sync-setup-corner{n}.txt').write_text(late)
                (Path(root) / f'video-sync-hold-corner{n}.txt').write_text(self.tail_table(row))
            report = runner.parse_video_sync(root, log, 10.0)
            self.assertEqual(len(report['failures']), 1)
            self.assertRegex(report['failures'][0], r'^FAIL Slow_1100mV_85C_Model setup -0\.600 .*data delay 10\.6')
            self.assertEqual(report['corners']['Slow 1100mV 0C Model']['max_data_delay_ns'], 6.037)
            self.assertTrue(runner.parse_video_sync(root, log, 11.0)['passed'])
            (Path(root) / 'video-sync-hold-corner4.txt').write_text(self.tail_table(row).replace('0.500', '-0.100'))
            self.assertRegex(runner.parse_video_sync(root, log, 11.0)['failures'][0], r'^FAIL Fast_1100mV_0C_Model hold -0\.100')

    def test_extra_sdc_request_is_checked(self):
        self.assertEqual(runner.extra_sdc_lines({'extra_sdc': ['set_max_delay 1 -to x']}), ['set_max_delay 1 -to x'])
        for bad in ('set_max_delay', [], [''], ['a\nb'], [1]):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'Unsupported extra_sdc'):
                runner.extra_sdc_lines({'extra_sdc': bad})
        with self.assertRaisesRegex(ValueError, 'timing-only'):
            runner.extra_sdc_lines({'extra_sdc': ['x'], 'test_only_assemble_despite_setup_failure': 'owner'})


if __name__ == '__main__':
    unittest.main()
