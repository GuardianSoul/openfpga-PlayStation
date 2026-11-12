from pathlib import Path
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import check_sdc_patterns
import prepare_video_sync_constraints as sync

ASYNC = '''
   -- clkvid -> clk1x
   process (clk1x)
   begin
      if rising_edge(clk1x) then
         videoout_reports_s1  <= videoout_reports;
         videoout_reports_s2  <= videoout_reports_s1;
         videoout_reports_1x  <= videoout_reports_s2;

         allowunpause_s1      <= allowunpause;
         allowunpause_s2      <= allowunpause_s1;
         allowunpause1x       <= allowunpause_s2;
      end if;
   end process;
   process (clk2x)
   begin
      if rising_edge(clk2x) then
         videoout_request_s1  <= videoout_request;
         videoout_request_s2  <= videoout_request_s1;
         videoout_request_2x  <= videoout_request_s2;
      end if;
   end process;
   reset_s1 <= reset_s2;
   videoout_request.fetchsize <= to_unsigned(xmax, 10);
   xmax <= xCount + 1;
'''
TOP = '''
            case (state) is
               when WAITNEWLINE =>
                  requestVRAMMirror <= '0';
                  if (videoout_on = '1' and videoout_request_clk2x.lineInNext /= lineAct and videoout_request_clk2x.fetch = '1') then
                     waitcnt <= 3;
                     state   <= WAITREQUEST;
                  end if;
               when WAITREQUEST =>
                  if (waitcnt > 0) then
                     waitcnt <= waitcnt - 1;
                  else
                     state     <= REQUEST;
                     lineAct   <= videoout_request_clk2x.lineInNext;
                     reqSize <= '0' & videoout_request_clk2x.fetchsize;
                  end if;
               when REQUEST =>
                  state <= WAITREAD;
            end case;
   end process;
'''
PACKAGE = '''
   type tvideoout_reports is record
      vsync                   : std_logic;
      dotclock                : std_logic;
   end record;
'''


class VideoSyncConstraint(unittest.TestCase):
    def test_block_names_only_the_covered_first_stages(self):
        lines = sync.constraint_lines()
        self.assertEqual(lines[0], '# POCKET_VIDEO_SYNC_BEGIN bound_ns=10.0')
        self.assertEqual(lines[-1], '# POCKET_VIDEO_SYNC_END')
        commands = [l for l in lines if not l.startswith('#')]
        self.assertEqual([c.split(' -to ')[0] for c in commands], ['set_max_delay 10.0', 'set_min_delay -10.0'])
        scope = '*|gpu_videoout_async:igpu_videoout_async|'
        for c in commands:
            self.assertEqual(re.search(r'\[get_registers \{([^}]*)\}\]$', c)[1].split(),
                             [scope + 'videoout_reports_s1.*', scope + 'allowunpause_s1*',
                              scope + 'videoout_request_s1.fetch*', scope + 'videoout_request_s1.lineInNext*'])
            self.assertNotIn('xpos', c)            # moves on every dot: stays under the old waiver if it exists
            self.assertNotIn('lineDisp', c)
            self.assertNotIn('-from', c)           # by endpoint only: no clock pair or domain is cut
        self.assertNotIn('set_false_path', '\n'.join(lines))

    def test_marker_and_removal(self):
        base = 'set_false_path -to [get_registers {*|a}]\n'
        with_block = base + '\n'.join(sync.constraint_lines()) + '\n'
        self.assertIsNone(sync.marker_bound(base))
        self.assertEqual(sync.marker_bound(with_block), 10.0)
        self.assertEqual(sync.without_block(with_block), base)
        self.assertEqual(sync.without_block(base), base)
        for broken in (base + sync.BEGIN + ' bound_ns=10.0\n', base + sync.END + '\n', with_block + with_block,
                       with_block.replace('bound_ns=10.0', 'bound_ns=ten')):
            with self.subTest(broken=broken[-40:]), self.assertRaisesRegex(ValueError, 'Malformed'):
                sync.marker_bound(broken)

    def test_guard_accepts_the_engine_and_catches_what_would_break_the_argument(self):
        self.assertTrue(sync.guard(ASYNC, TOP, PACKAGE))
        mutations = (
            (ASYNC.replace('videoout_request_s1  <= videoout_request;', 'videoout_request_s1  <= videoout_request_x;'), TOP, PACKAGE, 'request chain'),
            (ASYNC.replace('allowunpause_s1      <= allowunpause;', 'allowunpause_s1      <= allowunpause or x;'), TOP, PACKAGE, 'report chain'),
            (ASYNC + '   other_s1 <= other;\n', TOP, PACKAGE, 'another'),
            (ASYNC + '   xmax <= 5;\n', TOP, PACKAGE, 'fetch size'),
            (ASYNC, TOP.replace('waitcnt <= 3;', 'waitcnt <= 0;'), PACKAGE, 'four-cycle wait'),
            (ASYNC, TOP.replace("requestVRAMMirror <= '0';", 'lineAct <= videoout_request_clk2x.lineInNext; reqSize <= videoout_request_clk2x.fetchsize;'),
             PACKAGE, 'outside the waited state'),
            (ASYNC, TOP.replace('state <= WAITREAD;', 'x <= videoout_request_clk2x.lineInNext;'), PACKAGE, 'outside the waited state'),
            (ASYNC, TOP.replace('state <= WAITREAD;', 'x <= videoout_request_clk2x.xpos;'), PACKAGE, 'other request fields'),
            (ASYNC, TOP, PACKAGE.replace('dotclock                : std_logic;', 'dotclock : unsigned(1 downto 0);'), 'wider than one bit'),
        )
        for a, t, p, why in mutations:
            with self.subTest(why=why), self.assertRaisesRegex(ValueError, why):
                sync.guard(a, t, p)

    def project(self, root, name='p'):
        project = root / name
        (project / 'rtl').mkdir(parents=True)
        for file, text in (('gpu_videoout_async.vhd', ASYNC), ('gpu_videoout.vhd', TOP), ('pGPU.vhd', PACKAGE)):
            (project / 'rtl' / file).write_text(text)
        (project / 'apf_top.qsf').write_text(
            'set_global_assignment -name SDC_FILE pocket.sdc\n' + ''.join(
                f'set_global_assignment -name VHDL_FILE "/old/fits/{name}/rtl/{f}" -library work\n'
                for f in ('gpu_videoout_async.vhd', 'gpu_videoout.vhd', 'pGPU.vhd')))
        (project / 'pocket.sdc').write_text('set_false_path -to [get_registers {*|status_meta[*]}]\n')
        return project

    def test_apply_appends_once_and_only_to_a_guarded_project(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.project(Path(directory))
            report = sync.apply(project)
            text = (project / 'pocket.sdc').read_text()
            self.assertEqual(text, 'set_false_path -to [get_registers {*|status_meta[*]}]\n'
                             + '\n'.join(sync.constraint_lines()) + '\n')
            self.assertEqual((report['bound_ns'], report['timing_verified']), (10.0, False))
            with self.assertRaisesRegex(ValueError, 'already applied'):
                sync.apply(project)
            other = self.project(Path(directory), 'q')
            (other / 'rtl/gpu_videoout.vhd').write_text(TOP.replace('waitcnt <= 3;', 'waitcnt <= 1;'))
            with self.assertRaisesRegex(ValueError, 'four-cycle wait'):
                sync.apply(other)
            self.assertNotIn('POCKET_VIDEO_SYNC', (other / 'pocket.sdc').read_text())

    def frozen(self, root, also_touch=None):
        """prepare() on a small frozen project, with the fit helpers replaced by plain stand-ins:
        snapshot = copy and repoint, inputs = every project file with its hash."""
        import hashlib, json, shutil
        from types import SimpleNamespace
        from unittest import mock
        import run_frozen_pocket_fit as runner
        source, output = self.project(root, 'src'), root / 'out'
        sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
        inputs = lambda d: [dict(path=str(f), sha256=sha(f), assignment='X') for f in sorted(Path(d).rglob('*'))
                            if f.is_file() and f.name != 'fit-inputs.json']
        (source / 'fit-inputs.json').write_text(json.dumps(dict(top='apf_top', quartus_sources=inputs(source))))

        def snapshot(src, manifest, out, seed):
            shutil.copytree(src, out)
            (out / 'apf_top.qsf').write_text((out / 'apf_top.qsf').read_text() + f'set_global_assignment -name SEED {seed}\n')
            return dict(top='apf_top', directory=str(out), quartus_sources=inputs(out))

        def load_source(project, manifest):
            data = json.loads(Path(manifest).read_text())
            if data['quartus_sources'] != inputs(project): raise ValueError('Manifest source drift')
            return data
        real_apply = sync.apply

        def apply(project):
            report = real_apply(project)
            if also_touch: (Path(project) / also_touch).write_text('changed\n')
            return report
        (source / 'apf_top.qsf').write_text((source / 'apf_top.qsf').read_text() + 'set_global_assignment -name SEED 2\n')
        (source / 'fit-inputs.json').write_text(json.dumps(dict(top='apf_top', quartus_sources=inputs(source))))
        with mock.patch.dict(sys.modules, {'measure_pocket_seeds': SimpleNamespace(inputs=inputs)}), \
             mock.patch.object(runner, 'snapshot', snapshot), mock.patch.object(runner, 'load_source', load_source), \
             mock.patch.object(runner, 'hold_guard', lambda *a: None), mock.patch.object(sync, 'apply', apply):
            return source, output, inputs, load_source, sync.prepare(source, output, 7)

    def test_prepare_re_manifests_the_snapshot_and_only_the_sdc_changes(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            source, output, inputs, load_source, report = self.frozen(Path(directory))
            manifest = json.loads((output / 'fit-inputs.json').read_text())
            self.assertEqual(manifest['quartus_sources'], inputs(output))        # closes the edited inputs
            load_source(output, output / 'fit-inputs.json')                      # what the snapshot command checks first
            self.assertEqual((manifest['top'], manifest['directory'], report['seed']), ('apf_top', str(output), 7))
            before = {Path(r['path']).relative_to(source): r['sha256'] for r in inputs(source)}
            after = {Path(r['path']).relative_to(output): r['sha256'] for r in manifest['quartus_sources']}
            self.assertEqual({p for p in before if before[p] != after[p]}, {Path('pocket.sdc'), Path('apf_top.qsf')})
            self.assertIn('POCKET_VIDEO_SYNC_BEGIN', (output / 'pocket.sdc').read_text())
            self.assertNotIn('POCKET_VIDEO_SYNC', (source / 'pocket.sdc').read_text())   # the source is not edited
            # the stale manifest this replaces (the snapshot's own list, before the edit) is what failed
            stale = dict(manifest, quartus_sources=[dict(r, sha256='0' * 64) if r['path'].endswith('pocket.sdc') else r
                                                    for r in manifest['quartus_sources']])
            (output / 'fit-inputs.json').write_text(json.dumps(stale))
            with self.assertRaisesRegex(ValueError, 'Manifest source drift'):
                load_source(output, output / 'fit-inputs.json')

    def test_prepare_refuses_any_other_changed_input(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'only pocket.sdc'):
                self.frozen(Path(directory), also_touch='rtl/pGPU.vhd')

    def test_sdc_pattern_check_finds_the_new_patterns_and_nothing_else(self):
        import contextlib, io
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before, after = self.project(root, 'before'), self.project(root, 'after')
            for project in (before, after):      # the checker reads the HDL the QSF names
                q = (project / 'apf_top.qsf').read_text().replace(f'/old/fits/{project.name}/', str(project) + '/')
                (project / 'apf_top.qsf').write_text(q)
            sync.apply(after)
            rpt = root / 'map.rpt'
            rpt.write_text('; |apf_top|core_top:ic|gpu:igpu|gpu_videoout:igpu_videoout|gpu_videoout_async:igpu_videoout_async ; 1 ;\n')
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ok = check_sdc_patterns.prequeue(after, before, rpt)
            self.assertTrue(ok, out.getvalue())
            self.assertIn('4 new patterns, 0 unmatched', out.getvalue())
            # a record field the HDL does not have, or an instance the design does not have, is caught
            sdc = after / 'pocket.sdc'
            sdc.write_text(sdc.read_text().replace('videoout_request_s1.lineInNext*', 'videoout_request_s1.lineInNxt*'))
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertFalse(check_sdc_patterns.prequeue(after, before, rpt))
            self.assertIn('1 unmatched', out.getvalue())
            sdc.write_text(sdc.read_text().replace('lineInNxt', 'lineInNext').replace('igpu_videoout_async|allow', 'igpu_videoout_sync|allow'))
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertFalse(check_sdc_patterns.prequeue(after, before, rpt))
            self.assertIn('no hierarchy node matches the prefix', out.getvalue())


if __name__ == '__main__':
    unittest.main()
