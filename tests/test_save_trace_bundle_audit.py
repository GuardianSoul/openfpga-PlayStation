"""Exercise the real Tcl gate with mocked STA results; not physical proof."""
import ctypes
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MOCK = r'''
package provide ::quartus::project 1.0
package provide ::quartus::sta 1.0
proc project_open args {}
proc project_close args {}
proc create_timing_netlist args {}
proc delete_timing_netlist args {}
proc read_sdc args {}
proc update_timing_netlist args {}
proc set_operating_conditions args {}
set collection_id 0
proc collection xs {
    set c C[incr ::collection_id];set ::collections($c) $xs;return $c
}
proc members c {
    if {![info exists ::collections($c)]} {error "Expected collection, got node/filter $c"}
    return $::collections($c)
}
proc foreach_in_collection {name c body} {uplevel 1 [list foreach $name [members $c] $body]}
proc get_collection_size c {llength [members $c]}
proc get_registers args {
    set pattern [lindex $args end]
    if {[regexp {^(node|header)[0-9]+} $pattern]} {
        set xs [list r_$pattern]
        if {$pattern eq "node1" && [lsearch -exact $args -no_duplicates]<0} {lappend xs r_node1~DUPLICATE}
        return [collection $xs]
    }
    if {[string first header_q $pattern]>=0} {
        set xs {};for {set i 0} {$i<32} {incr i} {lappend xs r_header$i};return [collection $xs]
    }
    set xs {};for {set i 0} {$i<$::source_count} {incr i} {lappend xs r_node$i}
    if {[lsearch -exact $args -no_duplicates]<0} {lappend xs r_node1~DUPLICATE}
    return [collection $xs]
}
proc get_fanins args {
    regexp {r_header([0-9]+)} [members [lindex $args end]] unused bit
    set xs {}
    for {set i $bit} {$i<$::source_count} {incr i 32} {
        if {$i==1} {lappend xs r_node1~DUPLICATE} else {lappend xs r_node$i}
    }
    return [collection $xs]
}
proc get_available_operating_conditions {} {collection [lrange {a b c d} 0 [expr {$::mock_corners-1}]]}
proc get_timing_paths args {
    if {[lsearch -exact $args -false_path]>=0} {return [collection {}]}
    set from [members [lindex $args [expr {[lsearch -exact $args -from]+1}]]]
    set to [members [lindex $args [expr {[lsearch -exact $args -to]+1}]]]
    if {[llength $from]!=1 || [llength $to]!=1} {error "Expected exact physical pair"}
    if {$::hidden || ($::hidden_replica && [string match *~DUPLICATE $from])} {return [collection {}]}
    return [collection [list [list $from $to]]]
}
proc get_path_info {option p} {
    switch -- $option {
        -data_delay {return $::delay}
        -slack {return 1.0}
        -from {return [lindex $p 0]}
        -to {return [lindex $p 1]}
        -from_clock {return cpu}
        -to_clock {return read}
        default {error "Unexpected mock path option $option"}
    }
}
proc get_clock_info {option clock} {
    if {$option eq "-period"} {return $::period}
    if {$clock eq "cpu"} {return $::launch};return clk_74a
}
proc get_node_info {option node} {return [string range $node 2 end]}
'''


class TraceBundleAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        paths = list((ROOT / 'build/tools').glob('*/quartus/linux64/libtcl8.6.so'))
        if not paths:
            raise unittest.SkipTest('Tcl interpreter library unavailable')
        cls.tcl = ctypes.CDLL(str(paths[0]))
        cls.tcl.Tcl_CreateInterp.restype = ctypes.c_void_p
        cls.tcl.Tcl_Eval.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        cls.tcl.Tcl_GetStringResult.argtypes = [ctypes.c_void_p]
        cls.tcl.Tcl_GetStringResult.restype = ctypes.c_char_p
        cls.tcl.Tcl_DeleteInterp.argtypes = [ctypes.c_void_p]

    def run_gate(self, **changes):
        values = dict(delay=20, source_count=150, corners=4, hidden=0, hidden_replica=0, period=13.468,
                      launch='ic|cpu_pll|general[0].gpll~PLL_OUTPUT_COUNTER|divclk')
        values.update(changes)
        interp = self.tcl.Tcl_CreateInterp()
        try:
            with tempfile.TemporaryDirectory() as folder:
                script = MOCK + '\n' + '\n'.join(f'set {"mock_corners" if k == "corners" else k} {{{v}}}' for k,v in values.items())
                script += f'\nset quartus(args) [list {{{folder}/reports}}]\n'
                script += (ROOT / 'scripts/audit_save_trace_bundle.tcl').read_text()
                rc = self.tcl.Tcl_Eval(interp, script.encode())
                return rc, self.tcl.Tcl_GetStringResult(interp).decode()
        finally:
            self.tcl.Tcl_DeleteInterp(interp)

    def test_accepts_bound(self):
        self.assertEqual(self.run_gate()[0], 0)

    def test_rejects_bad_physical_evidence(self):
        for change, reason in [({'delay':20.001}, 'exceeds frozen window'),
                               ({'source_count':149}, 'geometry'),
                               ({'hidden':1}, 'Hidden/unconstrained'),
                               ({'hidden_replica':1}, 'Hidden/unconstrained'),
                               ({'corners':3}, 'four trace corners'),
                               ({'launch':'wrong-clock'}, 'bundle clocks'),
                               ({'period':10}, 'exceeds frozen window')]:
            with self.subTest(change=change):
                rc, message = self.run_gate(**change)
                self.assertNotEqual(rc, 0)
                self.assertIn(reason, message)
