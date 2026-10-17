"""Reject mismatched CD topology; preserve the tested CD functional body."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from diagnose_pocket_cd_cdc import MAX, MINS
from prepare_pocket_cd_cdc import (CD_TABLE_SHA, FIRST_STAGE, SYNC_ATTRIBUTE, preserve_sync_flops,
                                  MENU_FIRST_STAGE, MENU_MAX, MENU_MINS, preserve_menu_flops,
                                  prepare_project, scope_text, top_contract, top_profile, legacy_held_lists)
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool
from prepare_pocket_disc_swap import SWAP, disc_menu_text


def top():
    return ('module core_top;\n' + SWAP.replace('SLOT_VALUE', "16'd101").replace(
        'cd_stream_reset', 'cd_core_reset') + '''
    psx_cd_stream #(.DEFAULT_SLOT(16'd101), .MAX_TRACKS(19), .TRACK_STRIDE(16'd1)) disc_stream (
        .host_clk(clk_74a), .host_reset(cd_host_reset), .clk(clk1x), .reset(cd_core_reset),
        .disc_slot_id(swap_slot_id), .disc_select_strobe(swap_select_strobe),
        .disc_change_done(cd_disc_change_done), .disc_pending(cd_disc_pending), .has_disc(hasCD),
        .trackinfo_write(trackinfo_write), .trackinfo_data(trackinfo_data), .trackinfo_addr(trackinfo_addr));
    psx_mem_cdc loader (.src_reset(cd_host_reset), .dst_reset(cd_core_reset), .dummy());
endmodule
''')


def menu_top():
    raw = top().replace('cd_core_reset', 'cd_stream_reset').replace(
        '.has_disc(hasCD),\n', '.has_disc(hasCD), \n')
    return disc_menu_text(raw, 4).replace('cd_stream_reset', 'cd_core_reset')


class CDCDCGuards(unittest.TestCase):
    def test_complete_table_and_libcrypt_legacy_lists_get_endpoint_scope(self):
        for menu in (False, True):
            for table, libcrypt in ((False, False), (True, False), (False, True)):
                for held in legacy_held_lists(disc_menu=menu, table_offset=table, libcrypt=libcrypt):
                    for bound in ('0', '-13'):
                        text = (f'set_max_delay 13 -from [get_registers {{{held}}}]\n'
                                f'set_min_delay {bound} -from [get_registers {{{held}}}]\n' +
                                FIRST_STAGE + (MENU_FIRST_STAGE if menu else '') + '# keep\n')
                        changed = scope_text(text, Path('/tmp/cd.sdc'), disc_menu=menu,
                                             table_offset=table, libcrypt=libcrypt)
                        self.assertEqual(changed, '# keep\n\nsource "/tmp/cd.sdc"\n')
                        for bad in (text.replace(held, held+' *|unknown[*]'),
                                    text+text.splitlines(True)[0], text.replace('set_max_delay 13 ', 'set_max_delay 14 ')):
                            with self.assertRaises(ValueError):
                                scope_text(bad, Path('/tmp/cd.sdc'), disc_menu=menu,
                                           table_offset=table, libcrypt=libcrypt)

    def test_one_file_profile_requires_exact_offset_tracks_and_disabled_libcrypt(self):
        for base in (top(), menu_top()):
            for offset in ("16'd1", "16'h1", "16'h0001"):
                text = base.replace('.MAX_TRACKS(19)', f'.MAX_TRACKS(1), .TABLE_OFFSET({offset})')
                self.assertEqual(top_profile(text)['table_offset'], 1)
                for old, new in (('.MAX_TRACKS(1)', '.MAX_TRACKS(19)'),
                                 (f'.TABLE_OFFSET({offset})', ".TABLE_OFFSET(16'd2)"),
                                 (f'.TABLE_OFFSET({offset})', '.TABLE_OFFSET(table_offset)'),
                                 ('.MAX_TRACKS(1)', ".MAX_TRACKS(1), .LIBCRYPT_SLOT(16'd99)"),
                                 ('.MAX_TRACKS(1)', ".MAX_TRACKS(1), .TABLE_OFFSET(16'd1)")):
                    with self.subTest(old=old, new=new), self.assertRaises(ValueError):
                        top_profile(text.replace(old, new))
            for zero in ("16'd0", "16'h0", "16'h0000"):
                self.assertEqual(top_profile(base.replace('.MAX_TRACKS(19)',
                    f'.MAX_TRACKS(19), .TABLE_OFFSET({zero})'))['table_offset'], 0)
            with self.assertRaises(ValueError):
                top_profile(base.replace('.MAX_TRACKS(19)', '.MAX_TRACKS(1)'))

    def test_one_file_project_records_payload_without_modifying_functional_source(self):
        parent = ROOT / 'build/sim/cd-cdc-unit'; parent.mkdir(parents=True, exist_ok=True)
        for base, menu in ((top(), False), (menu_top(), True)):
            with tempfile.TemporaryDirectory(dir=parent) as tmp:
                directory = Path(tmp); core = directory / 'core_top.sv'
                core.write_text(base.replace('.MAX_TRACKS(19)', ".MAX_TRACKS(1), .TABLE_OFFSET(16'd1)"))
                sources = [core, ROOT / 'rtl/pocket/psx_cd_stream.sv']
                if menu:
                    sources += [ROOT / 'rtl/pocket/psx_disc_menu.sv', ROOT / 'rtl/pocket/psx_disc_swap.sv']
                qsf = directory / 'apf_top.qsf'
                qsf.write_text(''.join(f'set_global_assignment -name SYSTEMVERILOG_FILE "{p}"\n' for p in sources))
                (directory / 'pocket.sdc').write_text(
                    MENU_MAX + MENU_MINS[0] + FIRST_STAGE + MENU_FIRST_STAGE if menu else MAX + MINS[0] + FIRST_STAGE)
                before = {p: digest(p) for p in sources}
                report = prepare_project(directory, digest(core))
                self.assertEqual(report['table_offset'], 1)
                self.assertEqual(report['maximum_track_files'], 1)
                self.assertFalse(report['libcrypt_enabled'])
                self.assertFalse(report['physical_timing_verified'])
                self.assertIn('set cd_cdc_table_offset 1\n', (directory / 'cd-cdc/cd.sdc').read_text())
                self.assertEqual({p: digest(p) for p in sources}, before)
                prepared = (directory / 'cd-cdc/psx_cd_stream.sv').read_text()
                self.assertEqual(prepared.replace(SYNC_ATTRIBUTE, ''), sources[1].read_text())

    def test_table_payload_geometry_and_disc_settled_crossing(self):
        command = [shutil.which('tclsh')] if shutil.which('tclsh') else [find_tool('quartus_sh'), '-t']
        text = (ROOT / 'scripts/pocket_cd_scope.tcl').read_text()
        procedures = text[:text.index('if {![info exists cd_cdc_libcrypt]')]
        root = 'core_top:ic|psx_cd_stream:disc_stream|'
        stubs = '''proc get_registers {args} {return $::nodes}
proc get_collection_size {c} {llength $c}
proc get_node_info {args} {lindex $args end}
proc foreach_in_collection {v c body} {foreach x $c {uplevel 1 [list set $v $x];uplevel 1 $body}}
'''
        cases = [('cd_cdc_regs m_tb 1', [root+'m_tb', root+'m_tb~DUPLICATE2'], True),
                 ('cd_cdc_regs m_tb 1', [], False),
                 ('cd_cdc_regs m_tb 1', [root+'m_tb[0]'], False),
                 ('cd_cdc_one_file_count_regs', [root+'m_ntracks[0]'], True),
                 ('cd_cdc_one_file_count_regs', [root+'m_ntracks[0]', root+'m_ntracks[1]'], True),
                 ('cd_cdc_one_file_count_regs', [root+'m_ntracks[1]'], False),
                 ('cd_cdc_one_file_count_regs', [root+'m_ntracks[0]', root+'m_ntracks[2]'], False)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'table.tcl'
            for call, nodes, good in cases:
                path.write_text(stubs + procedures + '\nset nodes [list ' +
                    ' '.join('{'+n+'}' for n in nodes) + ']\n' + call + '\n')
                result = subprocess.run([*command, str(path)], capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode == 0, good, result.stdout + result.stderr)
            path.write_text(procedures + '\n' + '\n'.join(
                f'if {{[cd_cdc_cross_target host_core {{{root}{stem}}}] != {expected}}} {{error {{Wrong classification {stem}}}}}'
                for stem, expected in [('disc_settled', 1), ('disc_settled~DUPLICATE2', 1),
                                       ('mount_waiting', 0), ('tb_hold', 0), ('m_tb', 0), ('q[0]', 0)]))
            result = subprocess.run([*command, str(path)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_released_menu_profile_requires_exact_domains_parameters_and_exclusive_nets(self):
        t = menu_top()
        self.assertEqual(top_profile(t), dict(libcrypt=False, disc_menu=True, table_offset=0))
        for old, new in (
                (".DISC_STEP(16'd100)", ".DISC_STEP(16'd0)"),
                (".STEP(16'd100)", ".STEP(16'd256)"),
                ('.DISCS(4)', '.DISCS(3)'),
                ('.discs_present(cd_discs_present)', '.discs_present(other_present)'),
                ('.disc_setting(disc_setting)', '.disc_setting(other_setting)'),
                ('.clk(clk1x)', '.clk(clk2x)'),
                ('.reset(cd_core_reset)', '.reset(guest_reset)'),
                ('.MAX_TRACKS(19)', '.MAX_TRACKS(19), .INDEX_SECTOR(1)'),
                ('.MAX_TRACKS(19)', '.MAX_TRACKS(19), .MAX_TRACKS(19)')):
            with self.subTest(old=old), self.assertRaises(ValueError):
                top_profile(t.replace(old, new))
        for extra in ('\nassign extra=swap_slot_id;\n', '\npsx_disc_select rogue();\n', t):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                top_profile(t + extra)

    def test_menu_constraints_remove_only_the_exact_known_expansion(self):
        for minimum in MENU_MINS:
            before = '# before\n' + MENU_MAX + minimum + FIRST_STAGE + MENU_FIRST_STAGE + '# after\n'
            changed = scope_text(before, Path('/build/menu.sdc'), disc_menu=True)
            self.assertEqual(changed, '# before\n# after\n\nsource "/build/menu.sdc"\n')
            with self.assertRaises(ValueError): scope_text(before, Path('/build/menu.sdc'))
            for bad in (before.replace('m_present', 'other_present'),
                        before + MENU_FIRST_STAGE, before + MAX,
                        before + 'set_min_delay -99 -from *disc_stream|unknown\n'):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    scope_text(bad, Path('/build/menu.sdc'), disc_menu=True)

    def test_menu_preservation_changes_declarations_only(self):
        source = (ROOT / 'rtl/pocket/psx_disc_menu.sv').read_text()
        result = preserve_menu_flops(source)
        self.assertEqual(source[source.index('    reg change_req_d=0;'):],
                         result[result.index('    reg change_req_d=0;'):])
        self.assertIn('preserve, dont_merge *) reg [1:0] disc_sync=0;', result)
        self.assertIn('preserve, dont_merge *) reg [1:0] disc_last=0;', result)
        for bad in (result, source + '\n', source.replace('disc_sync<=disc_meta', 'disc_sync<=disc_setting')):
            with self.assertRaises(ValueError): preserve_menu_flops(bad)

    def test_menu_project_keeps_original_sources_and_records_dynamic_profile(self):
        parent = ROOT / 'build/sim/cd-cdc-unit'; parent.mkdir(parents=True, exist_ok=True)
        sources = [ROOT / 'rtl/pocket' / n for n in
                   ('psx_cd_stream.sv', 'psx_disc_menu.sv', 'psx_disc_swap.sv')]
        before = {p: digest(p) for p in sources}
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            d = Path(tmp); core = d / 'core_top.sv'; core.write_text(menu_top())
            (d / 'apf_top.qsf').write_text(''.join(
                f'set_global_assignment -name SYSTEMVERILOG_FILE "{p}" -library work\n'
                for p in [core, *sources]))
            (d / 'pocket.sdc').write_text(MENU_MAX + MENU_MINS[1] + FIRST_STAGE + MENU_FIRST_STAGE)
            report = prepare_project(d, digest(core))
            self.assertTrue(report['disc_presence_enabled'])
            self.assertIsNone(report['constant_selection_slot'])
            self.assertEqual(report['selection_slots'], [101, 201, 301, 401])
            self.assertFalse(report['physical_timing_verified'])
            self.assertEqual({p: digest(p) for p in sources}, before)
            self.assertIn('set cd_cdc_disc_menu 1', (d / 'cd-cdc/cd.sdc').read_text())
            self.assertIn(str(d / 'cd-cdc/psx_disc_menu.sv'), (d / 'apf_top.qsf').read_text())
            self.assertNotIn('set_min_delay -13 -from', (d / 'pocket.sdc').read_text())

    def test_menu_slot_alias_geometry_requires_every_variable_class_and_keeps_copies(self):
        command = [shutil.which('tclsh')] if shutil.which('tclsh') else [find_tool('quartus_sh'), '-t']
        text = (ROOT / 'scripts/pocket_cd_scope.tcl').read_text()
        procedures = text[:text.index('if {![info exists cd_cdc_libcrypt]')]
        root = 'core_top:ic|psx_cd_stream:disc_stream|'
        # Prove the admitted equivalence classes against all four literal IDs.
        patterns = {bit: tuple((slot >> bit) & 1 for slot in [101, 201, 301, 401]) for bit in range(16)}
        self.assertEqual(patterns[2], patterns[5])
        self.assertEqual(patterns[2], tuple(1-x for x in patterns[7]))
        self.assertEqual(patterns[6], tuple(1-x for x in patterns[8]))
        variable = {b for b, p in patterns.items() if len(set(p)) > 1}
        self.assertEqual(variable, {2, 3, 4, 5, 6, 7, 8})
        cases = [([2, 3, 4, 6], True), ([2, 3, 4, 6, 7, 8], True),
                 ([3, 4, 5, 8], True), ([0, 2, 3, 4, 5, 6, 7, 8], True),
                 ([2, 3, 6], False), ([2, 4, 6], False), ([2, 3, 4], False),
                 ([3, 4, 6], False), ([2, 3, 4, 6, 9], False)]
        stubs = '''proc get_registers {args} {return $::nodes}
proc get_collection_size {c} {llength $c}
proc get_node_info {args} {lindex $args end}
proc foreach_in_collection {v c body} {foreach x $c {uplevel 1 [list set $v $x];uplevel 1 $body}}
'''
        with tempfile.TemporaryDirectory() as tmp:
            for stem in ('m_slot', 'sel_slot'):
                nodes_cases = [([root + f'{stem}[{b}]' for b in bits], good) for bits, good in cases]
                base = [root + f'{stem}[{b}]' for b in [2, 3, 4, 6]]
                nodes_cases += [(base + [base[0] + '~DUPLICATE2'], True),
                                (base + [base[0] + '~UNKNOWN'], False),
                                ([x.replace('core_top:ic', 'other') for x in base], False)]
                for nodes, good in nodes_cases:
                    p = Path(tmp) / 'slots.tcl'
                    p.write_text(stubs + procedures + '\nset nodes [list ' +
                                 ' '.join('{' + n + '}' for n in nodes) +
                                 f']\nputs [cd_cdc_menu_slot_regs {stem}]\n')
                    r = subprocess.run([*command, str(p)], capture_output=True, text=True, timeout=15)
                    with self.subTest(nodes=nodes):
                        self.assertEqual(r.returncode == 0, good, r.stdout + r.stderr)
                        if good:
                            self.assertTrue(all(n in r.stdout for n in nodes))

    def test_menu_sync_requires_data_and_admits_only_the_final_local_reset(self):
        command = [shutil.which('tclsh')] if shutil.which('tclsh') else [find_tool('quartus_sh'), '-t']
        scope = (ROOT / 'scripts/pocket_cd_scope.tcl').read_text()
        procedures = scope[:scope.index('if {![info exists cd_cdc_libcrypt]')]
        owner = 'core_top:ic|psx_mem_cdc:loader_cdc|'
        reset = [owner + f'dst_reset_pipe[{i}]' for i in range(3)]
        cases = [(reset, ['data', reset[2]], True), (reset, ['data'], True),
                 (reset + [reset[2] + '~DUPLICATE'], ['data', reset[2] + '~DUPLICATE'], True),
                 (reset, [reset[2]], False), (reset, ['other-data', reset[2]], False),
                 (reset, ['data', reset[1]], False), (reset[:2], ['data'], False),
                 (reset + [owner + 'dst_reset_pipe[3]'], ['data'], False),
                 ([n.replace('loader_cdc', 'other') for n in reset], ['data'], False)]
        stubs = '''proc get_registers {args} {return $::nodes}
proc get_collection_size {c} {llength $c}
proc get_node_info {args} {lindex $args end}
proc foreach_in_collection {v c body} {foreach x $c {uplevel 1 [list set $v $x];uplevel 1 $body}}
proc set_false_path {args} {error RESET_PATH_MUST_STAY_TIMED}
'''
        with tempfile.TemporaryDirectory() as tmp:
            for nodes, actual, good in cases:
                p = Path(tmp) / 'reset.tcl'
                p.write_text(stubs + procedures + '\nset nodes [list ' +
                    ' '.join('{' + n + '}' for n in nodes) + ']\nset resets [cd_menu_domain_reset]\n' +
                    'cd_menu_check_inputs [list ' + ' '.join('{' + n + '}' for n in actual) + '] {data} $resets\n')
                r = subprocess.run([*command, str(p)], capture_output=True, text=True, timeout=15)
                with self.subTest(nodes=nodes, actual=actual):
                    self.assertEqual(r.returncode == 0, good, r.stdout + r.stderr)

    def test_attributes_restore_body_exactly_and_reject_source_drift(self):
        before = (ROOT / 'rtl/pocket/psx_cd_stream.sv').read_text()
        after = preserve_sync_flops(before)
        self.assertEqual(after.replace(SYNC_ATTRIBUTE, ''), before)
        self.assertEqual(after.count(SYNC_ATTRIBUTE), 3)
        self.assertEqual(before.count("DISC_STEP=16'h0"), 1)
        self.assertEqual(before.count('if(DISC_STEP!=0 && !probe_done)'), 1)
        for other in (before + '\n', after, before.replace('h_sel_s=0', 'h_sel_s=1'),
                      before.replace("DISC_STEP=16'h0", "DISC_STEP=16'h1"),
                      before.replace('if(DISC_STEP!=0 && !probe_done)', 'if(!probe_done)')):
            with self.assertRaises(ValueError): preserve_sync_flops(other)

    def test_live_profile_with_and_without_libcrypt(self):
        self.assertFalse(top_contract(top()))
        self.assertTrue(top_contract(top().replace('.MAX_TRACKS(19)', ".MAX_TRACKS(19), .LIBCRYPT_SLOT(16'd99)")))
        for old, new in (('MAX_TRACKS(19)', 'MAX_TRACKS(4)'), ('TRACK_STRIDE(16\'d1)', 'TRACK_STRIDE(16\'d2)'),
                         ('clk1x', 'clk2x'), ('clk_74a', 'clk_74b'), ('cd_core_reset', 'guest_reset'),
                         ('DISC_SLOT(16\'d101)', 'DISC_SLOT(16\'d0)'), ('DEFAULT_SLOT(16\'d101)', 'DEFAULT_SLOT(16\'d0)'),
                         ('.disc_pending(cd_disc_pending)', '.disc_pending(other_pending)'),
                         ('.disc_select_strobe(swap_select_strobe)', ".disc_select_strobe(1'b0)"),
                         ('.change_done(swap_change_done)', '.change_done(other_done)'),
                         ('.MAX_TRACKS(19)', ".MAX_TRACKS(19), .LIBCRYPT_SLOT(16'd98)")):
            with self.subTest(old=old), self.assertRaises(ValueError): top_contract(top().replace(old, new))

    def test_only_known_source_and_first_stage_constraints_replaced(self):
        for minimum in MINS:
            before = '# before\n' + MAX + minimum + FIRST_STAGE + 'set_min_delay 0 -from local -to local\n'
            out = scope_text(before, Path('/build/cd-cdc/cd.sdc'))
            self.assertEqual(out, '# before\nset_min_delay 0 -from local -to local\n\nsource "/build/cd-cdc/cd.sdc"\n')
        for bad in (MAX + MINS[0], MAX + MINS[0] + FIRST_STAGE * 2,
                    MAX + MINS[0] + FIRST_STAGE + 'set_max_delay 999 -from *disc_stream|unknown\n'):
            with self.assertRaises(ValueError): scope_text(bad, Path('/build/cd.sdc'))

    def test_disc_presence_profile_only_accepts_explicit_or_default_zero(self):
        for token in ("16'd0", "16'h0", "16'h0000"):
            self.assertFalse(top_contract(top().replace('.MAX_TRACKS(19)',
                             '.MAX_TRACKS(19), .DISC_STEP(' + token + ')')))
        for token in ("16'd256", "16'h0100", '0', 'disc_step', "16'd0), .DISC_STEP(16'd0"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                top_contract(top().replace('.MAX_TRACKS(19)',
                             '.MAX_TRACKS(19), .DISC_STEP(' + token + ')'))

    def test_project_prepares_only_fresh_sources_and_keeps_root_untouched(self):
        parent = ROOT / 'build/sim/cd-cdc-unit'; parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            directory = Path(tmp)
            core = directory / 'core_top.sv'; core.write_text(top())
            source = ROOT / 'rtl/pocket/psx_cd_stream.sv'; sha = digest(source)
            qsf = directory / 'apf_top.qsf'
            qsf.write_text(''.join(f'set_global_assignment -name SYSTEMVERILOG_FILE "{p}" -library work\n' for p in (core, source)))
            (directory / 'pocket.sdc').write_text(MAX + MINS[0] + FIRST_STAGE)
            with self.assertRaises(ValueError): prepare_project(directory, '0' * 64)
            self.assertFalse((directory / 'cd-cdc').exists())
            report = prepare_project(directory, digest(core))
            self.assertTrue(report['functional_CD_body_unchanged'])
            self.assertTrue(report['live_disc_swap'])
            self.assertEqual(report['constant_selection_slot'], 101)
            self.assertEqual(digest(source), sha)
            self.assertEqual(report['CD_source_sha256'], CD_TABLE_SHA)
            self.assertEqual(report['CD_source_contract'], 'optional-sector-buffer-track-table')
            self.assertFalse(report['disc_presence_enabled'])
            self.assertTrue(report['inactive_disc_presence_must_specialize'])
            self.assertFalse(report['physical_timing_verified'])
            self.assertFalse(report['production_selected'])
            self.assertIn('AUTO_SHIFT_REGISTER_RECOGNITION OFF', qsf.read_text())
            with self.assertRaises(ValueError): prepare_project(directory, digest(core))

    def test_changed_shared_controller_rejected_before_project_mutation(self):
        parent = ROOT / 'build/sim/cd-cdc-unit'; parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            directory = Path(tmp)
            core = directory / 'core_top.sv'; core.write_text(top())
            source = directory / 'psx_cd_stream.sv'
            raw = (ROOT / 'rtl/pocket/psx_cd_stream.sv').read_text()
            self.assertEqual(raw.count("mul_x*32'd2352"), 1)
            source.write_text(raw.replace("mul_x*32'd2352", "mul_x*32'd2336"))
            qsf = directory / 'apf_top.qsf'
            qsf.write_text(''.join(f'set_global_assignment -name SYSTEMVERILOG_FILE "{p}" -library work\n'
                                   for p in (core, source)))
            sdc = directory / 'pocket.sdc'; sdc.write_text(MAX + MINS[0] + FIRST_STAGE)
            before = {p: digest(p) for p in (core, source, qsf, sdc)}
            with self.assertRaisesRegex(ValueError, 'source identity'):
                prepare_project(directory, digest(core))
            self.assertFalse((directory / 'cd-cdc').exists())
            self.assertEqual({p: digest(p) for p in before}, before)

    def test_tcl_geometry_keeps_replicas_and_rejects_missing_bits_and_wrong_owner(self):
        command = [shutil.which('tclsh')] if shutil.which('tclsh') else [find_tool('quartus_sh'), '-t']
        scope = (ROOT / 'scripts/pocket_cd_scope.tcl').read_text()
        procedures = scope[:scope.index('if {![info exists cd_cdc_libcrypt]')]
        root = 'core_top:ic|psx_cd_stream:disc_stream|'
        cases = [('h_sel_s', 3, [root+f'h_sel_s[{i}]' for i in range(3)], True),
                 ('h_sel_s', 3, [root+f'h_sel_s[{i}]' for i in range(2)], False),
                 ('req_t', 1, [root+'req_t', root+'req_t~DUPLICATE2'], True),
                 ('resp_buf', 1, [root+'resp_buf[0]'], True),
                 ('resp_buf', 1, [root+'resp_buf'], False),
                 ('m_lc_bytes', 12, [root+f'm_lc_bytes[{i}]' for i in range(12)]+[root+'m_lc_bytes[3]~DUPLICATE'], True),
                 ('m_lc_bytes', 12, [root+f'm_lc_bytes[{i}]' for i in range(11)], False),
                 ('req_t', 1, ['other|req_t'], False)]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'test.tcl'
            for stem, width, nodes, good in cases:
                body = ' '.join('{'+n+'}' for n in nodes)
                p.write_text('''proc get_registers {args} {return $::nodes}
proc get_collection_size {collection} {llength $collection}
proc get_node_info {args} {lindex $args end}
proc foreach_in_collection {var collection body} {
 foreach item $collection {uplevel 1 [list set $var $item];uplevel 1 $body}
}
'''+procedures+f'\nset nodes [list {body}]\ncd_cdc_regs {stem} {width}\n')
                r = subprocess.run([*command, str(p)], capture_output=True, text=True, timeout=15)
                self.assertEqual(r.returncode == 0, good, (stem, nodes, r.stdout, r.stderr))

    def test_inactive_presence_rejects_any_surviving_payload_or_replica(self):
        command = [shutil.which('tclsh')] if shutil.which('tclsh') else [find_tool('quartus_sh'), '-t']
        text = (ROOT / 'scripts/pocket_cd_scope.tcl').read_text()
        procedures = text[:text.index('if {![info exists cd_cdc_libcrypt]')]
        root = 'core_top:ic|psx_cd_stream:disc_stream|'
        cases = [([], True), ([root + 'm_present[0]'], False),
                 ([root + 'm_present[3]~DUPLICATE2'], False),
                 ([root + 'discs_present[0]'], False),
                 ([root + 'discs_present[3]~DUPLICATE'], False)]
        stubs = '''proc get_registers {args} {
 set found {};foreach pattern [lindex $args end] {foreach n $::nodes {
  if {$pattern eq $n || [string match $pattern $n]} {lappend found $n}
 }};return [lsort -unique $found]
}
proc get_collection_size {c} {llength $c}
'''
        with tempfile.TemporaryDirectory() as tmp:
            for nodes, good in cases:
                p = Path(tmp) / 'presence.tcl'
                p.write_text(stubs + procedures + '\nset nodes [list ' +
                             ' '.join('{' + n + '}' for n in nodes) +
                             ']\ncd_cdc_inactive_presence\n')
                result = subprocess.run([*command, str(p)], text=True, capture_output=True, timeout=15)
                with self.subTest(nodes=nodes):
                    self.assertEqual(result.returncode == 0, good, result.stdout + result.stderr)
                    if not good:
                        self.assertIn('Disabled DISC_STEP field did not specialize', result.stderr + result.stdout)

    def test_only_observed_constant_slot_residual_is_admitted_as_timed_payload(self):
        command = [shutil.which('tclsh')] if shutil.which('tclsh') else [find_tool('quartus_sh'), '-t']
        text = (ROOT / 'scripts/pocket_cd_scope.tcl').read_text()
        procedures = text[:text.index('if {![info exists cd_cdc_libcrypt]')]
        root = 'core_top:ic|psx_cd_stream:disc_stream|'
        cases = [([], True), ([root + 'm_slot[0]'], True),
                 ([root + 'm_slot[0]', root + 'm_slot[0]~DUPLICATE2'], True),
                 ([root + 'm_slot[12]'], False), ([root + 'm_slot[0]', root + 'm_slot[1]'], False),
                 ([root + 'sel_slot[0]'], False), ([root + 'm_slot[0]~UNKNOWN'], False),
                 (['other|psx_cd_stream:disc_stream|m_slot[0]'], False)]
        stubs = '''proc get_registers {args} {
 set found {};foreach pattern [lindex $args end] {foreach n $::nodes {
  if {$pattern eq $n || [string match $pattern $n]} {lappend found $n}
 }};return [lsort -unique $found]
}
proc get_collection_size {c} {llength $c}
proc get_node_info {args} {lindex $args end}
proc foreach_in_collection {v c body} {foreach x $c {uplevel 1 [list set $v $x];uplevel 1 $body}}
'''
        with tempfile.TemporaryDirectory() as tmp:
            for nodes, good in cases:
                p = Path(tmp) / 'slot.tcl'
                p.write_text(stubs + procedures + '\nset nodes [list ' +
                             ' '.join('{' + n + '}' for n in nodes) +
                             ']\nputs [cd_cdc_slot_fields]\n')
                result = subprocess.run([*command, str(p)], text=True, capture_output=True, timeout=15)
                with self.subTest(nodes=nodes):
                    self.assertEqual(result.returncode == 0, good, result.stdout + result.stderr)
                    if good and nodes: self.assertIn('m_slot 1', result.stdout)


if __name__ == '__main__': unittest.main()
