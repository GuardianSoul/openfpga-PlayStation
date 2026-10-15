"""Guard the added hold margin and its precise clock/constraint scope."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_pocket_2x_hold import prepare_project
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def tcl_command():
    if shutil.which('tclsh'):
        return [shutil.which('tclsh')]
    quartus=find_tool('quartus_sh')
    return [quartus,'-t'] if quartus else []


class SameClockHold(unittest.TestCase):
    def project(self,root):
        top=root/'core_top.sv';top.write_text('module core_top; endmodule\n')
        (root/'apf_top.qsf').write_text(f'set_global_assignment -name SYSTEMVERILOG_FILE "{top}" -library work\n')
        (root/'apf_top.qpf').write_text('PROJECT_REVISION = apf_top\n')
        (root/'pocket.sdc').write_text('derive_clock_uncertainty\n')
        return top

    def test_project_identity_rtl_and_derived_constraints_stay_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);top=self.project(root);sha=digest(top)
            r=prepare_project(root,sha)
            self.assertEqual(r['additional_hold_uncertainty_ns'],0.15)
            self.assertTrue(r['add_to_derived']);self.assertFalse(r['timing_verified'])
            self.assertFalse(r['hold_relaxed']);self.assertFalse(r['false_paths_added'])
            self.assertEqual(digest(top),sha)
            self.assertEqual((root/'pocket.sdc').read_text(),'derive_clock_uncertainty\n')
            q=digest(root/'apf_top.qsf')
            with self.assertRaises(ValueError):prepare_project(root,sha)
            self.assertEqual(digest(root/'apf_top.qsf'),q)

    def test_generated_pin_clock_can_rederive_uncertainty_before_the_added_margin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);top=self.project(root)
            extra=root/'pins.tcl';extra.write_text('derive_clock_uncertainty\n')
            with (root/'pocket.sdc').open('a') as f:f.write(f'source "{extra}"\n')
            r=prepare_project(root,digest(top))
            self.assertEqual(r['inherited_derive_calls'],2)

    def test_bad_identity_margin_and_nested_constraint_overrides_reject(self):
        for fault in ('identity','nan','zero','big','cut','nested-uncertainty','no-derive'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);top=self.project(root);sha=digest(top);kwargs={}
                if fault=='identity':sha='0'*64
                if fault in ('nan','zero','big'):
                    kwargs['margin_ns']={'nan':float('nan'),'zero':0,'big':1}[fault]
                if fault=='cut':
                    with (root/'pocket.sdc').open('a') as f:
                        f.write('set_false_path -hold -from [get_clocks {clk_74a}] -to [get_clocks {ic|cpu_pll|*}]\n')
                if fault=='nested-uncertainty':
                    child=root/'extra.tcl';child.write_text('set_clock_uncertainty -hold 0 [get_clocks *]\n')
                    with (root/'pocket.sdc').open('a') as f:f.write(f'source "{child}"\n')
                if fault=='no-derive':(root/'pocket.sdc').write_text('')
                before={str(p):digest(p) for p in root.iterdir() if p.is_file()}
                with self.assertRaises(ValueError):prepare_project(root,sha,**kwargs)
                self.assertFalse((root/'same-2x-hold').exists())
                self.assertTrue(all(digest(Path(p))==h for p,h in before.items()))

    @unittest.skipUnless(tcl_command(),'Tcl interpreter unavailable')
    def test_scope_requires_exact_clock_and_preserves_derived_hold(self):
        scope=ROOT/'scripts/pocket_2x_hold_scope.tcl'
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'tb.tcl'
            for count,period in ((1,14.76255),(0,14.76255),(2,14.76255),(1,29.5251)):
                p.write_text(f'''set pocket_2x_hold_ns 0.15
proc get_clocks {{args}} {{return cpu2x}}
proc get_collection_size {{args}} {{return {count}}}
proc foreach_in_collection {{var collection body}} {{uplevel 1 [list set $var cpu2x];uplevel 1 $body}}
proc get_clock_info {{args}} {{return {period}}}
proc set_clock_uncertainty {{args}} {{puts "APPLIED $args"}}
source {{{scope}}}
''')
                r=subprocess.run([*tcl_command(),str(p)],capture_output=True,text=True,timeout=15)
                good=count==1 and period==14.76255
                self.assertEqual(r.returncode==0,good)
                self.assertEqual('APPLIED' in r.stdout,good)
                if good:
                    self.assertIn('-add -hold -enable_same_physical_edge -from cpu2x -to cpu2x 0.15',r.stdout)


if __name__=='__main__':unittest.main()
