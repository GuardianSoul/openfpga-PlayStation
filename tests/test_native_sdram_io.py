"""Guards for pin constraints: mode/clock identity and no scope hiding."""
import json
import re
import shutil
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_native_sdram_io import controller_contract,prepare_project,project_contract
from prepare_resource_targets import digest
from tool_paths import ROOT


class NativeSDRAMIO(unittest.TestCase):
    def project(self,root):
        ctrl=root/'psx_sdram_ctrl.sv';ctrl.write_text((ROOT/'rtl/memory/psx_sdram_ctrl.sv').read_text())
        wrapper=root/'psx_native_memory.sv'
        wrapper.write_text((ROOT/'rtl/pocket/psx_native_memory.sv').read_text().replace('    sdram #(', '    psx_sdram_ctrl #('))
        (root/'pocket.sdc').write_text('derive_pll_clocks\n')
        (root/'apf_top.qsf').write_text(''.join(f'set_global_assignment -name SYSTEMVERILOG_FILE "{p}" -library work\n' for p in [ctrl,wrapper]))
        return ctrl

    def test_current_mode_and_pin_contract(self):
        text=(ROOT/'rtl/memory/psx_sdram_ctrl.sv').read_text()
        cas=controller_contract(text);self.assertIn(cas,(2,3))
        for old,new in [(".datain_h(1'b0)",".datain_h(1'b1)"),('posedge clk) dq_in','negedge clk) dq_in')]:
            with self.assertRaises(ValueError):controller_contract(text.replace(old,new))

    def test_generation_and_duplicate_leave_parent_sources_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ctrl=self.project(root);sha=digest(ctrl)
            r=prepare_project(root,sha,analysis_only=True)
            self.assertFalse(r['assumptions_verified']);self.assertFalse(r['multicycle_added'])
            self.assertFalse(r['false_paths_added']);self.assertEqual(digest(ctrl),sha)
            qsha=digest(root/'apf_top.qsf')
            with self.assertRaises(ValueError):prepare_project(root,sha)
            self.assertEqual(digest(root/'apf_top.qsf'),qsha)
            scope=(root/'native-sdram-io/scope.tcl').read_text()
            self.assertNotIn('set_false_path',scope);self.assertNotIn('set_multicycle_path',scope)
            self.assertEqual(scope.count('set_input_delay'),2);self.assertEqual(scope.count('set_output_delay'),2)

    def test_bad_identity_clock_cut_and_flight_reject_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ctrl=self.project(root);sha=digest(ctrl)
            for kwargs in [dict(expected_controller_sha256='0'*64),dict(expected_controller_sha256=sha,board_min_ns=-1),dict(expected_controller_sha256=sha,board_max_ns=float('nan')),dict(expected_controller_sha256=sha,board_min_ns=1,board_max_ns=.5)]:
                with self.assertRaises(ValueError):prepare_project(root,**kwargs)
                self.assertFalse((root/'native-sdram-io').exists())
            (root/'pocket.sdc').write_text('set_clock_groups -asynchronous -group A -group B\n')
            with self.assertRaises(ValueError):prepare_project(root,sha)
            self.assertFalse((root/'native-sdram-io').exists())

    def parameterized_fixture(self,root):
        """Interface parser fixture only, not a tested read-pipeline variant."""
        ctrl=self.project(root);text=ctrl.read_text()
        if 'parameter integer CAS_LATENCY' not in text:
            text=text.replace('parameter SINGLE_CHIP = 1,',
                              'parameter SINGLE_CHIP = 1,\n    parameter integer CAS_LATENCY = 2,')
            text,count=re.subn(r"a\s*<=\s*13'h0221;", "a <= CAS_LATENCY == 3 ? 13'h0231 : 13'h0221;",text)
            self.assertEqual(count,1)
            ctrl.write_text(text)
        return ctrl

    def test_active_wrapper_override_does_not_use_source_default(self):
        for override in (None,2,3):
            with self.subTest(override=override),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);ctrl=self.parameterized_fixture(root)
                wrapper=root/'psx_native_memory.sv'
                if override is not None:
                    wrapper.write_text(wrapper.read_text().replace('.SINGLE_CHIP(1)) controller',
                                      f'.SINGLE_CHIP(1), .CAS_LATENCY({override})) controller'))
                sha=digest(ctrl);_,_,active,selected=project_contract(root,sha)
                self.assertEqual(selected,override)
                self.assertEqual(active,override if override is not None else controller_contract(ctrl.read_text()))
                r=prepare_project(root,sha,analysis_only=True)
                self.assertEqual(r['cas_latency'],active)
                self.assertEqual(r['cas_wrapper_override'],override)
                self.assertEqual(r['minimum_clock_period_ns'],6 if active==3 else 12)
                self.assertEqual(digest(ctrl),sha)
                self.assertIn(f'set native_sdram_cas {active}\n',(root/'native-sdram-io/pocket_native_sdram_io.sdc').read_text())

    def test_unknown_mode_or_parameter_override_rejects_before_writes(self):
        for value in ('4','runtime_cas','3), .UNKNOWN(1'):
            with self.subTest(value=value),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);ctrl=self.parameterized_fixture(root);wrapper=root/'psx_native_memory.sv'
                wrapper.write_text(wrapper.read_text().replace('.SINGLE_CHIP(1)) controller',
                                   f'.SINGLE_CHIP(1), .CAS_LATENCY({value})) controller'))
                with self.assertRaises(ValueError):prepare_project(root,digest(ctrl))
                self.assertFalse((root/'native-sdram-io').exists())
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ctrl=self.parameterized_fixture(root)
            ctrl.write_text(ctrl.read_text().replace("CAS_LATENCY == 3 ? 13'h0231 : 13'h0221",
                                                   "CAS_LATENCY == 2 ? 13'h0231 : 13'h0221"))
            with self.assertRaises(ValueError):prepare_project(root,digest(ctrl))
            self.assertFalse((root/'native-sdram-io').exists())

    def test_reviewed_io_recipe_preserves_hold_and_targets_only_dq_enable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ctrl=self.project(root);wrapper=root/'psx_native_memory.sv'
            wrapper.write_text(wrapper.read_text().replace('.SINGLE_CHIP(1)) controller',
                               '.SINGLE_CHIP(1), .CAS_LATENCY(3)) controller'))
            source_sha=digest(ctrl);r=prepare_project(root,source_sha,implementation=True)
            self.assertEqual(r['cas_latency'],3);self.assertTrue(r['multicycle_added'])
            self.assertFalse(r['read_hold_relaxation']);self.assertFalse(r['timing_verified'])
            capture=(root/'native-sdram-io/previous-beat.sdc').read_text()
            self.assertIn('set_multicycle_path -setup 2',capture)
            self.assertNotIn('set_multicycle_path -hold',capture)
            self.assertNotIn('set_false_path',capture)
            self.assertEqual(len(r['physical_assignments']),3)
            off=[s for s in r['physical_assignments'] if 'ALLOW_SYNCH_CTRL_USAGE OFF' in s]
            self.assertEqual(len(off),1);self.assertTrue(off[0].endswith('|dq_oe"'))
            self.assertEqual(digest(ctrl),source_sha)

    def test_io_recipe_rejects_cas2_override_analysis_mode_and_unreviewed_source(self):
        for reason in ('cas2','analysis','source'):
            with self.subTest(reason=reason),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);ctrl=self.project(root);wrapper=root/'psx_native_memory.sv'
                if reason!='cas2':
                    wrapper.write_text(wrapper.read_text().replace('.SINGLE_CHIP(1)) controller',
                                      '.SINGLE_CHIP(1), .CAS_LATENCY(3)) controller'))
                if reason=='source':ctrl.write_text(ctrl.read_text()+'\n// unreviewed revision\n')
                with self.assertRaises(ValueError):
                    prepare_project(root,digest(ctrl),implementation=True,analysis_only=reason=='analysis')
                self.assertFalse((root/'native-sdram-io').exists())

    @unittest.skipUnless(shutil.which('tclsh'),'Tcl interpreter unavailable')
    def test_io_recipe_clock_guard_runs_before_applying_the_capture_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ctrl=self.project(root);wrapper=root/'psx_native_memory.sv'
            wrapper.write_text(wrapper.read_text().replace('.SINGLE_CHIP(1)) controller',
                               '.SINGLE_CHIP(1), .CAS_LATENCY(3)) controller'))
            prepare_project(root,digest(ctrl),implementation=True)
            capture=root/'native-sdram-io/previous-beat.sdc'
            for period in (9.841,8.0,12.0):
                stub=root/'guard.tcl'
                stub.write_text(f'''set native_sdram_cas 3
set native_sdram_period {period}
set native_sdram_caps captures
set native_sdram_inputs inputs
proc get_collection_size {{collection}} {{return 16}}
proc set_multicycle_path {{args}} {{puts CAPTURE_APPLIED}}
source {{{capture}}}
''')
                run=subprocess.run(['tclsh',str(stub)],capture_output=True,text=True,timeout=5)
                self.assertEqual(run.returncode==0,period==9.841)
                self.assertEqual('CAPTURE_APPLIED' in run.stdout,period==9.841)


if __name__=='__main__':unittest.main()
