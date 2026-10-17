"""The clean memory scope must retain local timing and reject CDC drift."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_clean_memory_cdc import (CARD_SHA, VRAM_SHA, SYNC_ATTRIBUTE, legacy_lines,
                                      prepare_project, preserved, scope_text, top_contract)
from prepare_pocket_card_cdc import TOP_NEW, TOP_OLD
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def top():
    shared = ','.join(f'.{p}(mc_{p})' for p in ('aux_req', 'aux_we', 'aux_addr', 'aux_din', 'aux_dout', 'aux_done', 'card_write'))
    return '''module core_top;
    assign bridge_endian_little=1;
    wire vram_park=!reset_n;   // synchronised inside psx_vram_psram
''' + TOP_OLD + '''
    always @(posedge clk_74a) begin mc_idle74<={mc_idle74[0],!mc_busy}; boot_ready_card<=(bios_go) && mc_idle74[1]; end
    assign memcard_load_done=(bios_go);
    core_bridge_cmd commands (.clk(clk_74a),.reset_n(reset_n));
    wire mc_aux_req,mc_aux_we,mc_aux_addr,mc_aux_din,mc_aux_dout,mc_aux_done,mc_card_write;
    psx_memcard_slot memcard_slot (.clk_74a(clk_74a),.clk2x(clk2x),.reset_2x(mc_reset2x[1]),
        .busy(mc_busy),.load_done(memcard_load_done),.bridge_addr(bridge_addr),.bridge_wr(bridge_wr),
        .bridge_wr_data(bridge_wr_data),.bridge_rd(bridge_rd),.bridge_rd_data(memcard_rd_data),.overflow(),
        ''' + shared + ''');
    psx_vram_psram vram (.clk(clk2x),.reset(!cpu_locked),.park(vram_park),''' + shared + ''');
endmodule
'''


class CleanMemoryCDC(unittest.TestCase):
    def test_preserve_only_existing_sync_attributes(self):
        for path, sha, card, count in (('rtl/pocket/psx_memcard_slot.sv', CARD_SHA, True, 5),
                                       ('rtl/memory/psx_vram_psram.sv', VRAM_SHA, False, 1)):
            raw = (ROOT / path).read_text()
            out = preserved(raw, sha, card=card)
            self.assertEqual(out.count(SYNC_ATTRIBUTE), count)
            self.assertEqual(out.replace(SYNC_ATTRIBUTE, ''), raw)
            for bad in (raw + '\n', out, raw.replace('posedge', 'negedge', 1)):
                with self.assertRaises(ValueError): preserved(bad, sha, card=card)

    def test_top_requires_exact_exclusive_ownership_and_domains(self):
        self.assertEqual(top_contract(top()).replace(TOP_NEW, TOP_OLD), top())
        for bad in (top().replace('.clk(clk2x)', '.clk(clk1x)'),
                    top().replace('.clk(clk_74a)', '.clk(clk_74b)'),
                    top().replace('.aux_dout(mc_aux_dout)', '.aux_dout(other)', 1),
                    top() + '\nassign tapped=mc_aux_dout;',
                    top().replace('vram_park=!reset_n', 'vram_park=!power_reset_n'),
                    top().replace('.park(vram_park)', '.park(reset_n)'),
                    top().replace('bridge_endian_little=1', 'bridge_endian_little=0'),
                    top().replace('memcard_load_done=(bios_go)', 'memcard_load_done=(passed)'),
                    top().replace('psx_memcard_slot memcard_slot', 'psx_memcard_slot #(.FAULT(6)) memcard_slot'),
                    top_contract(top())):
            with self.subTest(bad=bad), self.assertRaises(ValueError): top_contract(bad)

    def test_exact_constraints_removed_and_unrelated_paths_unchanged(self):
        reset = 'set_false_path -from [get_registers {*|boot_ready_card}] -to [get_registers {*boot_gate*|reset_pipe[*]}]\n'
        raw = '# keep\n' + ''.join(legacy_lines()) + reset
        self.assertEqual(scope_text(raw, Path('/build/memory.sdc')),
                         '# keep\n' + reset + '\nsource "/build/memory.sdc"\n')
        for bad in (raw.replace(legacy_lines()[0], ''), raw + legacy_lines()[0],
                    raw + 'set_false_path -from [get_registers {*|psx_vram_psram:vram|anything}]\n',
                    raw.replace('set_min_delay -13', 'set_min_delay -20', 1)):
            with self.assertRaises(ValueError): scope_text(bad, Path('/build/memory.sdc'))

    def test_validation_precedes_mutation_and_sources_remain_identical(self):
        parent = ROOT / 'build/sim/clean-memory-cdc-unit'; parent.mkdir(parents=True, exist_ok=True)
        sources = [ROOT / p for p in ('rtl/pocket/psx_memcard_slot.sv', 'rtl/memory/psx_vram_psram.sv',
                                     'references/MIRLO64/target/pocket/core_bridge_cmd.v')]
        with tempfile.TemporaryDirectory(dir=parent) as temp:
            d = Path(temp); core = d / 'core_top.sv'; core.write_text(top())
            sdc = d / 'pocket.sdc'; sdc.write_text(''.join(legacy_lines()))
            qsf = d / 'apf_top.qsf'
            qsf.write_text(''.join(f'set_global_assignment -name {"VERILOG_FILE" if p.suffix == ".v" else "SYSTEMVERILOG_FILE"} "{p}"\n'
                                   for p in [core, *sources]))
            protected = {p: digest(p) for p in [core, sdc, qsf, *sources]}
            with self.assertRaises(ValueError): prepare_project(d, '0' * 64)
            self.assertEqual(protected, {p: digest(p) for p in protected})
            self.assertFalse((d / 'clean-memory-cdc').exists())
            result = prepare_project(d, digest(core))
            self.assertEqual(result['preserved_sync_flops'], 14)
            self.assertTrue(result['ordinary_local_and_settling_paths_timed'])
            self.assertFalse(result['physical_timing_verified'])
            for p in sources: self.assertEqual(digest(p), protected[p])
            with self.assertRaises(ValueError): prepare_project(d, digest(core))

    def test_physical_geometry_actual_fanout_and_chain_faults(self):
        stubs = r'''
proc get_collection_size {c} {llength $c}
proc foreach_in_collection {v c body} {foreach x $c {uplevel 1 [list set $v $x];uplevel 1 $body}}
proc get_node_info {flag n} {if {$flag eq "-type"} {return reg};return $n}
proc get_registers {args} {
 set found {};foreach p [lindex $args end] {foreach n $::nodes {
  if {$p eq $n || [string match $p $n]} {lappend found $n}
 }};return [lsort -unique $found]
}
proc get_clocks {args} {return [lindex $args end]}
proc get_clock_info {flag c} {if {$c eq "clk_74a"} {return $::host_period};return 14.76255}
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
set c {core_top:ic|psx_memcard_slot:memcard_slot};set v {core_top:ic|psx_vram_psram:vram}
set nodes {};set edges {};set bounds {};set cuts {};set host_period 13.468
proc node {n} {lappend ::nodes $n;return $n}
proc link {a b} {dict lappend ::edges $a $b}
foreach {stem width} {req_we_74 1 req_addr_74 15 req_data_74 32 ready_tgl_74 1} {
 for {set i 0} {$i<$width} {incr i} {
  if {$width==1} {set a [node ${c}|$stem]} else {set a [node [format {%s|%s[%d]} $c $stem $i]]}
  link $a $a
  link $a [node ${c}|w0_magic]
  link $a [node ${v}|act_we]
 }
}
for {set i 0} {$i<32} {incr i} {
 set a [node [format {%s|aux_dout[%d]} $v $i]]
 link $a [node [format {%s|bridge_rd_data[%d]} $c $i]]
 link $a [node ${c}|checked]
}
foreach {owner stem src} [list $c s2x_req ${c}|req_tgl_74 $c s2x_ready ${c}|ready_74 \
  $c s2x_rack [format {%s|s74_rst[1]} $c] $c s74_rst ${c}|rst_req_2x \
  $c s74_take ${c}|take_tgl_2x core_top:ic mc_idle74 ${c}|busy] {
 set last [node $src]
 for {set i 0} {$i<2} {incr i} {set n [node [format {%s|%s[%d]} $owner $stem $i]];link $last $n;set last $n}
}
set p [node {core_top:ic|core_bridge_cmd:commands|reset_n}]
link $p [node ${v}|park_m];link ${v}|park_m [node ${v}|park_s]
set dup [node [format {%s|req_data_74[0]~DUPLICATE2} $c]];link $dup ${v}|act_we
set nodes [lsort -unique $nodes]
'''
        checks = r'''
if {[llength $cuts]!=7 || [llength $bounds]!=25} {error "Wrong number of boundaries"}
foreach b $bounds {
 set i [lsearch -exact $b -to];set targets [lindex $b [expr {$i+1}]]
 foreach n $targets {if {$n in $::clean_mem_local_names(req_data_74) || $n in $::clean_mem_local_names(aux_dout)} {
   error "Local payload path relaxed"
 }}
}
if {[get_collection_size $clean_mem_source(req_data_74)]!=33} {error "Lost physical payload replica"}
puts PASS
'''
        controls = [('', True),
            (r'set nodes [lsearch -all -inline -not -exact $nodes [format {%s|req_addr_74[14]} $c]]', False),
            (r'node [format {%s|req_data_74[32]} $c]', False),
            (r'node [format {%s|req_data_74[0]~COPY9} $c]', False),
            (r'link [format {%s|s2x_req[0]} $c] ${c}|checked', False),
            (r'link ${c}|req_we_74 [format {%s|s2x_req[1]} $c]', False),
            (r'link ${c}|req_we_74 [node {core_top:ic|foreign|data}]', False),
            (r'link ${c}|req_we_74 [node ${v}|child|data]', False),
            (r'dict set edges [format {%s|req_addr_74[1]} $c] {}', False),
            ('set host_period 20', False)]
        runner = [shutil.which('tclsh')] if shutil.which('tclsh') else [find_tool('quartus_sh'), '-t']
        with tempfile.TemporaryDirectory(dir=ROOT / 'build/sim') as temp:
            script = Path(temp) / 'check.tcl'
            for mutation, positive in controls:
                script.write_text(stubs + '\n' + mutation + '\nsource {' + str(ROOT / 'scripts/pocket_clean_memory_scope.tcl') + '}\n' + checks)
                r = subprocess.run([*runner, str(script)], capture_output=True, text=True)
                with self.subTest(mutation=mutation):
                    self.assertEqual(r.returncode == 0, positive, r.stdout + r.stderr)
                    if positive: self.assertIn('PASS', r.stdout)


if __name__ == '__main__': unittest.main()
