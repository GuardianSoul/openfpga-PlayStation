"""Reject unknown source/geometry; scope exceptions only to raw reset pipes."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_pocket_reset_release import adapt_boot, adapt_cdc, adapt_top, prepare_project, CD_RESET_OLD, POWER_CUT, LOADER_OLD
from prepare_pocket_disc_swap import disc_swap_text
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def interpreter():
    if shutil.which('tclsh'):
        return [shutil.which('tclsh')]
    quartus = find_tool('quartus_sh')
    return [quartus, '-t'] if quartus else []


class ResetRelease(unittest.TestCase):
    def test_cue_host_releases_on_host_reset_without_changing_transport(self):
        cue = ('    psx_cue_mount #(' + ', '.join(
            f'.FIRMWARE{n}("psx_cue_fw{n}.hex")' for n in range(4)) +
            ') cue_mount (.clk(clk_74a), .reset(cd_stream_reset), .u_read(cd_target_read));\n')
        top = (LOADER_OLD + ');\n' + CD_RESET_OLD + '\npsx_cd_stream #() disc_stream ('
               '.host_clk(clk_74a),.clk(clk1x),.host_reset(cd_stream_reset),.reset(cd_stream_reset));\n' + cue)
        changed = adapt_top(top)
        self.assertIn(cue.replace('.reset(cd_stream_reset)', '.reset(cd_host_reset)'), changed)
        self.assertNotIn('cd_stream_reset', changed)
        for bad in (top+cue, top.replace('.clk(clk_74a)', '.clk(clk1x)'),
                    top.replace('psx_cue_fw0.hex', 'other.hex'),
                    top.replace('.reset(cd_stream_reset), .u_read', '.reset(guest_reset), .u_read'),
                    top+'assign rogue=cd_stream_reset;\n'):
            with self.assertRaises(ValueError):
                adapt_top(bad)

    def test_fresh_project_replaces_only_known_inputs_and_rejects_reapplication(self):
        parent = ROOT / 'build/sim/reset-release-fixtures'
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            directory = Path(tmp)
            core, qsf, sdc = (directory / n for n in ('core_top.sv', 'apf_top.qsf', 'pocket.sdc'))
            core.write_text(LOADER_OLD + ');\n' + CD_RESET_OLD + '\npsx_cd_stream #() disc_stream ('
                            '.host_clk(clk_74a),.clk(clk1x),.host_reset(cd_stream_reset),.reset(cd_stream_reset));\n')
            originals = [ROOT / 'rtl/pocket/psx_boot_gate.sv', ROOT / 'rtl/memory/psx_mem_cdc.sv']
            before = {p: digest(p) for p in originals}
            qsf.write_text(''.join(f'set_global_assignment -name SYSTEMVERILOG_FILE "{p}" -library work\n'
                                   for p in [core, *originals]))
            sdc.write_text(POWER_CUT + '# keep unrelated constraints byte-exact\n')
            report = prepare_project(directory, digest(core))
            self.assertEqual(sdc.read_text(), '# keep unrelated constraints byte-exact\n')
            self.assertEqual(before, {p: digest(p) for p in originals})
            for p in originals:
                self.assertNotIn(str(p), qsf.read_text())
                self.assertIn(str(directory / 'reset-release' / p.name), qsf.read_text())
            self.assertFalse(report['timing_verified'])
            self.assertTrue(report['configuration_reset_unchanged'])
            with self.assertRaisesRegex(ValueError, 'before map/fit'):
                prepare_project(directory, digest(core))
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            directory = Path(tmp)
            (directory / 'db').mkdir()
            with self.assertRaisesRegex(ValueError, 'before map/fit'):
                prepare_project(directory, 'unused')

    def test_source_identity_and_reset_contracts(self):
        boot = (ROOT / 'rtl/pocket/psx_boot_gate.sv').read_text()
        cdc = (ROOT / 'rtl/memory/psx_mem_cdc.sv').read_text()
        for transform, text in ((adapt_boot, boot), (adapt_cdc, cdc)):
            with self.assertRaisesRegex(ValueError, 'identity differs'):
                transform(text + '// drift\n')
            with self.assertRaises(ValueError):
                transform(transform(text))
        changed = adapt_boot(boot)
        self.assertIn('if(lock_sync && reset_seen[2] && cpu_reset) ack_cpu<=freeze_sync;', changed)
        self.assertIn('posedge cpu_transport_reset', changed)
        self.assertIn('posedge host_transport_reset', changed)
        self.assertNotIn('posedge cpu_reset', changed)
        changed = adapt_cdc(cdc)
        early = cdc
        for domain in ('src', 'dst'):
            early = early.replace(
                f'    always @(posedge {domain}_clk or posedge reset_async) begin\n        if (reset_async) begin',
                f'    always @(posedge {domain}_clk or posedge {domain}_reset) begin\n        if ({domain}_reset) begin')
        self.assertEqual(adapt_cdc(early), changed)
        self.assertEqual(changed.count('posedge reset_async'), 2)
        self.assertIn('posedge src_reset) begin', changed)
        self.assertIn('posedge dst_reset) begin', changed)
        self.assertNotIn('else if (src_reset)', changed)
        self.assertNotIn('else if (dst_reset)', changed)

    def test_top_requires_both_transport_ports_and_one_stream(self):
        top = (LOADER_OLD + ');\n' + CD_RESET_OLD + '\npsx_cd_stream #(.MAX_TRACKS(19)) disc_stream ('
               '.host_clk(clk_74a),.clk(clk1x),.host_reset(cd_stream_reset),.reset(cd_stream_reset));\n')
        good = adapt_top(top)
        self.assertEqual(good.count('.src_reset(cd_host_reset)'), 1)
        self.assertEqual(good.count('.dst_reset(cd_core_reset)'), 1)
        self.assertIn('.host_reset(cd_host_reset)', good)
        self.assertIn('.reset(cd_core_reset)', good)
        for bad in (top + top, top.replace('.reset(cd_stream_reset)', '.reset(other)'), good,
                    top.replace('!power_reset_n || !cpu_locked', '!power_reset_n', 1),
                    top.replace('.dst_clk(clk1x)', '.dst_clk(clk2x)', 1),
                    top.replace('.host_clk(clk_74a)', '.host_clk(clk_74b)', 1),
                    top.replace(LOADER_OLD, LOADER_OLD + '.src_reset(other),')):
            with self.assertRaises(ValueError):
                adapt_top(bad)

    def test_disc_swap_composition_requires_the_known_pair_and_matching_domains(self):
        top = (LOADER_OLD + ');\n' + CD_RESET_OLD + "\n    assign LIDopen=1'b0;\n"
               "    psx_cd_stream #(.DEFAULT_SLOT(16'h20)) disc_stream ("
               ".host_clk(clk_74a),.clk(clk1x),.host_reset(cd_stream_reset),.reset(cd_stream_reset),"
               ".disc_slot_id(16'h20), .disc_select_strobe(1'b0),.has_disc(hasCD), .disc_change_done(cd_disc_change_done));\n")
        swap = disc_swap_text(top)
        changed = adapt_top(swap)
        self.assertNotIn('cd_stream_reset', changed)
        self.assertEqual(changed.count('.reset(cd_core_reset)'), 3)
        self.assertEqual(changed.count('.host_reset(cd_host_reset)'), 1)
        self.assertEqual(changed.count('.dst_reset(cd_core_reset)'), 1)
        # Preserve every non-reset byte of the sequencers and their outputs.
        for module in ('psx_disc_swap disc_swap (', 'psx_disc_select #('):
            start = swap.index(module)
            before = swap[start:swap.index(');', start) + 2]
            self.assertIn(before.replace('.reset(cd_stream_reset)', '.reset(cd_core_reset)'), changed)
        swap_start = swap.index('    psx_disc_swap disc_swap (')
        swap_end = swap.index(');', swap_start) + 2
        select_start = swap.index('    psx_disc_select #(')
        select_end = swap.index(');', select_start) + 2
        for bad in (
                swap.replace('.clk(clk1x)', '.clk(clk2x)', 1),
                swap.replace('.reset(cd_stream_reset)', '.reset(other)', 1),
                swap.replace('psx_disc_swap disc_swap (', 'psx_disc_swap other ('),
                swap.replace('psx_disc_select #(.DISC_SLOT', 'psx_disc_select #(.OTHER_SLOT'),
                swap[:swap_start] + swap[swap_end:],
                swap[:select_start] + swap[select_end:],
                swap + swap[swap_start:swap_end],
                swap + '\nassign unknown_reset=cd_stream_reset;\n',
                changed):
            with self.subTest(top=bad):
                with self.assertRaises(ValueError):
                    adapt_top(bad)

    def test_released_disc_menu_uses_core_reset_without_changing_menu_wiring(self):
        top = (LOADER_OLD + ');\n' + CD_RESET_OLD + "\n    assign LIDopen=1'b0;\n"
               "    psx_cd_stream #(.DEFAULT_SLOT(16'h20)) disc_stream ("
               ".host_clk(clk_74a),.clk(clk1x),.host_reset(cd_stream_reset),.reset(cd_stream_reset),"
               ".disc_slot_id(16'h20), .disc_select_strobe(1'b0),.has_disc(hasCD), .disc_change_done(cd_disc_change_done));\n")
        swap = disc_swap_text(top)
        select_start = swap.index('    psx_disc_select #(')
        select_end = swap.index(');', select_start) + 2
        select = swap[select_start:select_end]
        # Exact 0.5.2 instance shape; the transport reset belongs to clk1x,
        # while disc_setting deliberately remains the module's CDC input.
        menu = """    psx_disc_menu #(.FIRST_SLOT(16'd101), .STEP(16'd100), .DISCS(4)) disc_menu (.clk(clk1x), .reset(cd_stream_reset),
        .disc_setting(disc_setting), .change_req(swap_change_req), .change_done(swap_change_done),
        .disc_slot_id(swap_slot_id), .disc_select_strobe(swap_select_strobe),
        .disc_change_done(cd_disc_change_done), .discs_present(cd_discs_present), .swap_request(disc_menu_request));"""
        released = swap[:select_start] + menu + swap[select_end:]
        changed = adapt_top(released)
        self.assertNotIn('cd_stream_reset', changed)
        self.assertEqual(changed.count('.reset(cd_core_reset)'), 3)
        self.assertEqual(changed.count('.host_reset(cd_host_reset)'), 1)
        self.assertIn(menu.replace('.reset(cd_stream_reset)', '.reset(cd_core_reset)'), changed)
        for bad in (
                released.replace(menu, ''),
                released.replace('psx_disc_swap disc_swap (', 'psx_disc_swap other ('),
                released + '\n' + select,
                released + '\n' + menu,
                released.replace(menu, menu.replace('.clk(clk1x)', '.clk(clk2x)')),
                released.replace(menu, menu.replace('.reset(cd_stream_reset)', '.reset(other)')),
                released.replace(menu, menu.replace('disc_menu (', 'other (')),
                released.replace(".FIRST_SLOT(16'd101)", '.FIRST_SLOT(first_slot)'),
                released.replace(".STEP(16'd100)", ".STEP(16'd1)"),
                released.replace('.DISCS(4)', '.DISCS(5)'),
                released + '\nassign unknown_reset=cd_stream_reset;\n',
                changed):
            with self.subTest(top=bad):
                with self.assertRaises(ValueError):
                    adapt_top(bad)
        swap_start = released.index('    psx_disc_swap disc_swap (')
        swap_end = released.index(');', swap_start) + 2
        with self.assertRaisesRegex(ValueError, 'complete disc-swap'):
            adapt_top(released[:swap_start] + released[swap_end:])

    @unittest.skipUnless(interpreter(), 'Tcl interpreter unavailable')
    def test_exact_reset_boundaries_include_all_copies_and_require_all_bits(self):
        text = (ROOT / 'scripts/pocket_reset_release_scope.tcl').read_text()
        procedure = text[:text.index('\nset reset_power_source')]
        stubs = '''
proc get_registers {args} {return $::nodes}
proc get_collection_size {c} {llength $c}
proc get_node_info {args} {lindex $args end}
proc foreach_in_collection {var collection body} {
 foreach item $collection {uplevel 1 [list set $var $item];uplevel 1 $body}
}
'''
        owner = 'core_top:ic|psx_mem_cdc:loader_cdc'
        bus = [owner + f'|dst_reset_pipe[{i}]' for i in range(3)]
        cases = [(bus, True), (bus + [bus[2] + '~DUPLICATE'], True),
                 ([bus[0] + '~DUPLICATE2', *bus[1:]], True),
                 (bus[:2], False), ([bus[0], bus[1], bus[1] + '~DUPLICATE'], False),
                 ([*bus[:2], bus[2].replace('[2]', '[3]')], False),
                 ([bus[0] + '~unknown', *bus[1:]], False),
                 ([bus[0].replace('loader_cdc', 'wrong'), *bus[1:]], False)]
        program = stubs + procedure
        for i, (nodes, good) in enumerate(cases):
            program += ('\nset nodes [list ' + ' '.join('{' + n + '}' for n in nodes) + ']\n' +
                        f'set code [catch {{reset_release_regs {{{owner}}} dst_reset_pipe 3}} result]\n' +
                        f'if {{($code==0)!={int(good)}}} {{error CASE_{i}_VERDICT}}\n' +
                        f'if {{$code==0 && $result ne $nodes}} {{error CASE_{i}_DROPPED_COPY}}\n')
        program += 'puts PASS_RESET_SCOPE_8\n'
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'scope.tcl'; path.write_text(program)
            p = subprocess.run([*interpreter(), str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('PASS_RESET_SCOPE_8', p.stdout)
        self.assertEqual(text.count('set_false_path'), 1)
        self.assertIn('set_false_path -from $reset_power_source -to $reset_boundary', text)
        for forbidden in ('set_clock_groups', 'set_multicycle_path', 'get_clocks', 'set_max_delay'):
            self.assertNotIn(forbidden, text)


if __name__ == '__main__':
    unittest.main()
