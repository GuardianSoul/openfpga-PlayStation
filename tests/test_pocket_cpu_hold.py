"""The added hold request covers one complete related family and keeps derives."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_pocket_cpu_hold import prepare_project
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def tcl_command():
    if shutil.which('tclsh'):return [shutil.which('tclsh')]
    q=find_tool('quartus_sh');return [q,'-t'] if q else []


class CPUHold(unittest.TestCase):
    def fixture(self,p):
        top=p/'core_top.sv';top.write_text('module core_top; endmodule\n')
        (p/'apf_top.qsf').write_text(f'set_global_assignment -name SYSTEMVERILOG_FILE "{top}" -library work\n')
        (p/'apf_top.qpf').write_text('PROJECT_REVISION = apf_top\n')
        child=p/'pins.tcl';child.write_text('derive_clock_uncertainty\n')
        (p/'pocket.sdc').write_text(f'derive_clock_uncertainty\nsource "{child}"\n')
        return top

    def test_derived_constraints_and_rtl_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);top=self.fixture(p);before={f:digest(f) for f in p.iterdir()}
            r=prepare_project(p,digest(top));self.assertEqual(r['additional_hold_ns'],.1)
            self.assertEqual(r['inherited_derive_calls'],2);self.assertFalse(r['hold_relaxed']);self.assertFalse(r['exceptions_added'])
            for f,h in before.items():
                if f.name!='apf_top.qsf':self.assertEqual(digest(f),h)
            self.assertIn('All nine',r['scope'])
            q=digest(p/'apf_top.qsf')
            with self.assertRaises(ValueError):prepare_project(p,digest(top))
            self.assertEqual(digest(p/'apf_top.qsf'),q)

    def test_identity_bounds_cuts_and_nested_override_reject_before_writing(self):
        for fault in ('identity','nan','infinity','zero','big','cut','override','no-derive'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp);top=self.fixture(p);sha=digest(top);kw={}
                if fault=='identity':sha='0'*64
                if fault in ('nan','infinity','zero','big'):kw['margin_ns']={'nan':float('nan'),'infinity':float('inf'),'zero':0,'big':1}[fault]
                if fault=='cut':
                    with (p/'pocket.sdc').open('a') as f:f.write('set_false_path -hold -from [get_clocks {clk_74a}] -to [get_clocks {ic|cpu_pll|*}]\n')
                if fault=='override':(p/'pins.tcl').write_text('set_clock_uncertainty -hold 0 [get_clocks *]\n')
                if fault=='no-derive':(p/'pocket.sdc').write_text('')
                before={f:digest(f) for f in p.iterdir()}
                with self.assertRaises(ValueError):prepare_project(p,sha,**kw)
                self.assertFalse((p/'cpu-hold-margin').exists())
                self.assertTrue(all(digest(f)==h for f,h in before.items()))

    @unittest.skipUnless(tcl_command(),'Tcl interpreter unavailable')
    def test_only_one_complete_known_family_is_accepted(self):
        scope=ROOT/'scripts/pocket_cpu_hold_scope.tcl'
        for fault in ('none','vram','missing','extra','period','master','hierarchy','duplicate-index','unknown','two-vram','vram-period','vram-x3'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as tmp:
                clocks=['c0','c1','c2'];periods=[29.524,14.762,9.841]
                names=[f'ic|cpu_pll|general[{i}].gpll~PLL_OUTPUT_COUNTER|divclk' for i in range(3)];masters=['cpu-vco']*3
                if fault=='missing':clocks.pop()
                if fault=='extra':clocks.append('extra')
                if fault=='period':periods[1]=15.0
                if fault=='master':masters[2]='different-vco'
                if fault=='hierarchy':names[2]='other|'+names[2]
                if fault=='duplicate-index':names[2]=names[1]
                if fault=='unknown':names[2]='unknown'
                if fault in ('vram','two-vram','vram-period','vram-x3'):
                    clocks.append('c3');periods.append({'vram-period':15.0,'vram-x3':9.841}.get(fault,14.762))
                    names.append('ic|cpu_pll|general[3].gpll~PLL_OUTPUT_COUNTER|divclk');masters.append('cpu-vco')
                if fault=='two-vram':clocks.append('c4');periods.append(14.762);names.append(names[3]);masters.append('cpu-vco')
                data='\n'.join('dict set '+field+' c'+str(i)+' {'+str(value)+'}' for field,values in [('names',names),('periods',periods),('masters',masters)] for i,value in enumerate(values))
                script=Path(tmp)/'scope.tcl';script.write_text('''set pocket_cpu_hold_ns 0.1
proc get_clocks {pattern} {if {$pattern eq {*cpu_pll*OUTPUT_COUNTER*divclk}} {return $::clocks};foreach c $::clocks {if {[dict get $::names $c] eq $pattern} {return $c}};error NO_CLOCK}
proc get_collection_size {c} {llength $c}
proc foreach_in_collection {var collection body} {
 foreach item $collection {uplevel 1 [list set $var $item];uplevel 1 $body}
}
proc get_clock_info {field clock} {
 switch -- $field {-name {dict get $::names $clock} -period {dict get $::periods $clock} -master_clock {dict get $::masters $clock} default {error UNKNOWN_FIELD}}
}
proc set_clock_uncertainty {args} {puts "APPLIED $args"}
'''+data+'\nset clocks {'+' '.join(clocks)+'}\nsource {'+str(scope)+'}\n')
                r=subprocess.run([*tcl_command(),str(script)],capture_output=True,text=True,timeout=15)
                ok=fault in ('none','vram','vram-x3')
                self.assertEqual(r.returncode==0,ok,(fault,r.stdout,r.stderr))
                self.assertEqual('APPLIED' in r.stdout,ok)
                if ok:self.assertEqual(sorted(l for l in r.stdout.splitlines() if l.startswith('APPLIED')),
                    sorted(f'APPLIED -add -hold -enable_same_physical_edge -from c{a} -to c{b} 0.1' for a in range(3) for b in range(3)))


if __name__=='__main__':unittest.main()
