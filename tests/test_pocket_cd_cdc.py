"""Reject mismatched CD topology; preserve the tested CD functional body."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from diagnose_pocket_cd_cdc import MAX, MINS
from prepare_pocket_cd_cdc import (CD_PRESENCE_SHA, FIRST_STAGE, SYNC_ATTRIBUTE, preserve_sync_flops,
                                  prepare_project, scope_text, top_contract)
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool
from prepare_pocket_disc_swap import SWAP


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


class CDCDCGuards(unittest.TestCase):
    def test_attributes_restore_body_exactly_and_reject_source_drift(self):
        before = (ROOT / 'rtl/pocket/psx_cd_stream.sv').read_text()
        after = preserve_sync_flops(before)
        self.assertEqual(after.replace(SYNC_ATTRIBUTE, ''), before)
        self.assertEqual(after.count(SYNC_ATTRIBUTE), 3)
        for other in (before + '\n', after, before.replace('h_sel_s=0', 'h_sel_s=1')):
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
            self.assertEqual(report['CD_source_sha256'], CD_PRESENCE_SHA)
            self.assertEqual(report['CD_source_contract'], 'disc-presence-default-off')
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
