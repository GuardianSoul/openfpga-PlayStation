"""Card timing scope must reject source/top drift and hidden local paths."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_pocket_card_cdc import (ATTRIBUTES, CARD_SHA, DONE, FIRST, MAX, MIN,
                                    TOP_NEW, TOP_OLD, prepare_project,
                                    preserve_sync_flops, scope_text, top_contract)
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def top():
    return '''module core_top;
''' + TOP_OLD + '''
    always @(posedge clk_74a) begin mc_idle74<={mc_idle74[0],!mc_busy}; boot_ready_card<=(bios_go) && mc_idle74[1]; end
    assign memcard_load_done=(bios_go);
    psx_bios_patch bios_patch (.clk(clk_74a), .reset(!power_reset_n), .loader_ready(passed), .host_run(reset_n),
        .skip(logo_skip), .bios_ready(bios_go), .patched(bios_patched), .l_valid(ldr_valid));
    wire mc_aux_req,mc_aux_we,mc_aux_addr,mc_aux_din,mc_aux_dout,mc_aux_done,mc_card_write;
    pocket_memcard_format memcard_slot (.clk_74a(clk_74a),.clk2x(clk2x),.reset_2x(mc_reset2x[1]),
        .busy(mc_busy),.load_done(memcard_load_done),.bridge_addr(bridge_addr),.bridge_wr(bridge_wr),
        .bridge_wr_data(bridge_wr_data),.bridge_rd(bridge_rd),.bridge_rd_data(memcard_rd_data),.overflow(),
        .aux_req(mc_aux_req),.aux_we(mc_aux_we),.aux_addr(mc_aux_addr),.aux_din(mc_aux_din),
        .aux_dout(mc_aux_dout),.aux_done(mc_aux_done),.card_write(mc_card_write));
    vram_psram vram (.clk(clk2x),.reset(!cpu_locked),
        .aux_req(mc_aux_req),.aux_we(mc_aux_we),.aux_addr(mc_aux_addr),.aux_din(mc_aux_din),
        .aux_dout(mc_aux_dout),.aux_done(mc_aux_done),.card_write(mc_card_write));
endmodule
'''


class CardCDC(unittest.TestCase):
    def test_only_attributes_change_and_unknown_source_rejects(self):
        raw = (ROOT / 'rtl/pocket/pocket_memcard_format.vhd').read_text()
        adapted = preserve_sync_flops(raw)
        self.assertEqual(adapted.replace('\n' + ATTRIBUTES, '', 1), raw)
        for bad in (raw + '\n', adapted, raw.replace('rd_ack   <= rd_tog_s(2)', 'rd_ack   <= not rd_tog_s(2)')):
            self.assertNotEqual(bad, raw)
            with self.assertRaises(ValueError): preserve_sync_flops(bad)

    def test_actual_aux_ownership_and_launch_domains_are_required(self):
        self.assertEqual(top_contract(top()).replace(TOP_NEW, TOP_OLD), top())
        for bad in (top().replace('.clk(clk2x)', '.clk(clk1x)'),
                    top().replace('.aux_dout(mc_aux_dout)', '.aux_dout(other)', 1),
                    top() + '\nassign tapped = mc_aux_dout;\n',
                    top().replace('.load_done(memcard_load_done)', ".load_done(1'b1)"),
                    top().replace('memcard_load_done=(bios_go)', 'memcard_load_done=(passed)'),
                    top().replace('mc_idle74[0],!mc_busy', 'mc_idle74[0],mc_busy'),
                    top().replace('bios_patch (.clk(clk_74a)', 'bios_patch (.clk(clk_74b)'),
                    top().replace('memcard_slot (', 'another_card ('), top_contract(top())):
            with self.subTest(top=bad), self.assertRaises(ValueError): top_contract(bad)

    def test_only_exact_legacy_card_bounds_are_replaced(self):
        old = '# keep\n' + FIRST + MAX + MIN + DONE + 'set_min_delay 0 -from local -to local\n'
        scope = Path('/build/card.sdc')
        self.assertEqual(scope_text(old, scope), '# keep\nset_min_delay 0 -from local -to local\n\nsource "/build/card.sdc"\n')
        for bad in (old.replace(MIN, ''), old + FIRST,
                    old + 'set_max_delay 999 -from [get_registers {*|pocket_memcard_format:*|unknown}]\n'):
            with self.assertRaises(ValueError): scope_text(bad, scope)

    def test_fresh_project_validates_before_mutation_and_preserves_dependencies(self):
        parent = ROOT / 'build/sim/card-cdc-unit'; parent.mkdir(parents=True, exist_ok=True)
        sources = [ROOT / p for p in ('rtl/pocket/pocket_memcard_format.vhd',
                   'references/pocket_sotn/pocket/src/fpga/core/vram_psram.vhd', 'rtl/pocket/psx_bios_patch.sv')]
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            d = Path(tmp); core = d / 'core_top.sv'; core.write_text(top())
            q = d / 'apf_top.qsf'; sdc = d / 'pocket.sdc'; sdc.write_text(FIRST + MAX + MIN + DONE)
            q.write_text(''.join(f'set_global_assignment -name {"VHDL_FILE" if p.suffix==".vhd" else "SYSTEMVERILOG_FILE"} "{p}" -library work\n'
                                 for p in [core, *sources]))
            protected = {p: digest(p) for p in [q, core, sdc, *sources]}
            with self.assertRaises(ValueError): prepare_project(d, '0' * 64)
            self.assertEqual({p: digest(p) for p in protected}, protected)
            self.assertFalse((d / 'card-cdc').exists())
            result = prepare_project(d, digest(core))
            self.assertEqual(result['preserved_sync_flops'], 11)
            self.assertTrue(result['ordinary_local_clock_relationships_retained'])
            self.assertFalse(result['physical_timing_verified'])
            self.assertFalse(result['actual_APF_pacing_verified'])
            self.assertEqual(digest(sources[0]), CARD_SHA)
            self.assertEqual({p: digest(p) for p in sources}, {p: protected[p] for p in sources})
            self.assertIn('AUTO_SHIFT_REGISTER_RECOGNITION OFF', q.read_text())
            with self.assertRaises(ValueError): prepare_project(d, digest(core))

    def test_tcl_geometry_local_scope_and_real_chain_faults(self):
        command = [shutil.which('tclsh')] if shutil.which('tclsh') else [find_tool('quartus_sh'), '-t']
        scope = ROOT / 'scripts/pocket_card_scope.tcl'
        stubs = r'''
proc get_collection_size {c} {llength $c}
proc foreach_in_collection {v c body} {foreach x $c {uplevel 1 [list set $v $x];uplevel 1 $body}}
proc get_node_info {flag n} {if {$flag eq "-type"} {return reg};return $n}
proc get_registers {args} {
 set found {}
 foreach pattern [lindex $args end] {
  foreach n $::nodes {if {$pattern eq $n || [string match $pattern $n]} {lappend found $n}}
 }
 return [lsort -unique $found]
}
proc get_clocks {args} {return [lindex $args end]}
proc get_clock_info {flag c} {if {$c eq "clk_74a"} {return 13.468};return 14.76255}
proc get_fanouts {nodes} {
 set found {};foreach n $nodes {if {[dict exists $::edges $n]} {lappend found {*}[dict get $::edges $n]}}
 return [lsort -unique $found]
}
proc get_fanins {flag nodes} {
 set found {};dict for {a bs} $::edges {foreach b $bs {if {$b in $nodes} {lappend found $a}}}
 return [lsort -unique $found]
}
proc set_max_delay {args} {lappend ::bounds [list max {*}$args]}
proc set_min_delay {args} {lappend ::bounds [list min {*}$args]}
proc set_net_delay {args} {lappend ::bounds [list net {*}$args]}
proc set_max_skew {args} {lappend ::bounds [list skew {*}$args]}
proc set_false_path {args} {lappend ::cuts $args}
set o {core_top:ic|pocket_memcard_format:memcard_slot}
set nodes {};set edges {};set bounds {};set cuts {}
proc node {n} {lappend ::nodes $n;return $n}
proc link {a b} {dict lappend ::edges $a $b}
for {set i 0} {$i<15} {incr i} {
 set a [node [format {%s|rd_word[%d]} $o $i]]
 link $a $a
 link $a [node [format {core_top:ic|vram_psram:vram|o_dq0[%d]} $i]]
}
for {set i 0} {$i<32} {incr i} {
 set a [node [format {%s|rd_data[%d]} $o $i]]
 link $a $a
 link $a [node [format {%s|bridge_rd_data[%d]} $o $i]]
}
foreach {owner stem width src} [list $o rd_tog_s 3 ${o}|rd_tog $o rd_ack_s 3 ${o}|rd_ack $o done_s 3 {core_top:ic|psx_bios_patch:bios_patch|bios_ready} core_top:ic mc_idle74 2 ${o}|busy] {
 set last [node $src]
 for {set i 0} {$i<$width} {incr i} {set n [node [format {%s|%s[%d]} $owner $stem $i]];link $last $n;set last $n}
}
# A real physical replica must remain in the source collection.
set a [node [format {%s|rd_word[0]~DUPLICATE2} $o]]
link $a [format {core_top:ic|vram_psram:vram|o_dq0[0]}]
'''
        controls = (
            ('good', '', True),
            ('missing-bit', 'set nodes [lreplace $nodes [lsearch -exact $nodes [format {%s|rd_word[14]} $o]] [lsearch -exact $nodes [format {%s|rd_word[14]} $o]]]', False),
            ('wrong-owner', 'link [format {%s|rd_word[0]} $o] [node {core_top:ic|unknown|data[0]}]', False),
            ('local-stage-cut', 'link [format {%s|rd_tog_s[0]} $o] [node {core_top:ic|unexpected_consumer}]', False),
            ('wrong-launch', 'link [node {core_top:ic|wrong_launch}] [format {%s|rd_ack_s[0]} $o]', False),
            ('missing-consumer', 'dict set edges [format {%s|rd_data[31]} $o] [list [format {%s|rd_data[31]} $o]]', False),
        )
        checks = r'''
if {[llength $cuts]!=4} {error CUT_COUNT}
if {[get_collection_size $card_cdc_source(host_core)]!=16} {error DROPPED_REPLICA}
foreach row $bounds {
 set to [lindex $row [expr {[lsearch -exact $row -to]+1}]]
 foreach n $to {
  if {[string first |rd_word $n]>=0 || [string first |rd_data $n]>=0} {error LOCAL_PATH_RELAXED}
 }
}
foreach row $cuts {
 foreach n [lindex $row [expr {[lsearch -exact $row -to]+1}]] {
  if {[string range $n end-2 end] ne {[0]}} {error LATER_STAGE_CUT}
 }
}
puts PASS_CARD_SCOPE
'''
        with tempfile.TemporaryDirectory() as tmp:
            for name, mutate, good in controls:
                p = Path(tmp) / (name + '.tcl')
                p.write_text(stubs + '\n' + mutate + '\nsource {' + str(scope) + '}\n' + checks)
                result = subprocess.run([*command, str(p)], text=True, capture_output=True, timeout=30)
                with self.subTest(case=name):
                    self.assertEqual(result.returncode == 0, good, result.stdout + result.stderr)
                    if good: self.assertIn('PASS_CARD_SCOPE', result.stdout)
                    else: self.assertIn('card', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
