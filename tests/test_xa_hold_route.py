"""Only the reviewed XA write-data cone receives the routing lower bound."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_xa_hold_route import prepare_project
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def tcl_command():
    if shutil.which('tclsh'):return [shutil.which('tclsh')]
    q=find_tool('quartus_sh');return [q,'-t'] if q else []


class XAHoldRoute(unittest.TestCase):
    def fixture(self,p):
        top=p/'core_top.sv';top.write_text('module core_top; endmodule\n')
        xa=p/'cd_xa.vhd';xa.write_text('-- identity fixture, no source transform\n')
        (p/'apf_top.qsf').write_text(f'set_global_assignment -name SYSTEMVERILOG_FILE "{top}"\nset_global_assignment -name VHDL_FILE "{xa}"\n')
        (p/'pocket.sdc').write_text('derive_clock_uncertainty\n')
        return digest(top),digest(xa)

    def test_only_qsf_and_new_scope_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);ids=self.fixture(p);before={f:digest(f) for f in p.iterdir()}
            result=prepare_project(p,*ids)
            self.assertFalse(result['rtl_or_clock_changed']);self.assertFalse(result['latency_changed'])
            self.assertFalse(result['exceptions_added']);self.assertFalse(result['setup_or_hold_relaxed'])
            self.assertEqual(result['minimum_net_ns'],.5)
            for f,h in before.items():
                if f.name!='apf_top.qsf':self.assertEqual(digest(f),h)
            q=digest(p/'apf_top.qsf')
            with self.assertRaises(ValueError):prepare_project(p,*ids)
            self.assertEqual(digest(p/'apf_top.qsf'),q)

    def test_invalid_inputs_fail_before_writing(self):
        for fault in ('top','xa','nan','zero','big'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp);ids=list(self.fixture(p));kwargs={}
                if fault in ('top','xa'):ids[0 if fault=='top' else 1]='0'*64
                else:kwargs['minimum_ns']={'nan':float('nan'),'zero':0,'big':3}[fault]
                before={f:digest(f) for f in p.iterdir()}
                with self.assertRaises(ValueError):prepare_project(p,*ids,**kwargs)
                self.assertFalse((p/'xa-hold-route').exists())
                self.assertTrue(all(digest(f)==h for f,h in before.items()))

    @unittest.skipUnless(tcl_command(),'Tcl interpreter unavailable')
    def test_complete_stereo_geometry_and_exact_bound(self):
        prefix='core_top:ic|cd_top:icd_top|cd_xa:icd_xa|'
        for fault in ('none','routed-aliases','alias-missing','alias-wrong-bank','mapped','mapped-missing','mapped-wrong-bit','mixed','source-copy','source-missing','source-unknown','target-missing','wrong-instance','wrong-channel','bank-bit-duplicate','period'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as tmp:
                sources=[prefix+f'clamped[{i}]' for i in range(16)]
                targets=[prefix+f'SyncFifo:ififoADPCM_{channel}|altsyncram:memory_rtl_0|altsyncram_m9q1:auto_generated|ram_block1a{bank}~PORT_A_DATA_IN_{bit}' for channel in ('L','R') for bank in range(8) for bit in range(8)]
                mapped=[prefix+f'SyncFifo:ififoADPCM_{channel}|altsyncram:memory_rtl_0|altsyncram_m9q1:auto_generated|ram_block1a{bank}~porta_datain_reg0' for channel in ('L','R') for bank in range(16)]
                if fault.startswith('mapped'):targets=mapped
                if fault=='mapped-missing':targets.pop()
                if fault=='mapped-wrong-bit':targets[0]=targets[0][:-1]+'1'
                if fault=='mixed':targets+=mapped
                if fault in ('routed-aliases','alias-missing','alias-wrong-bank'):
                    aliases=[prefix+f'SyncFifo:ififoADPCM_{channel}|altsyncram:memory_rtl_0|altsyncram_m9q1:auto_generated|ram_block1a{bank}~porta_datain_reg0' for channel in ('L','R') for bank in range(8)]
                    if fault=='alias-missing':aliases.pop()
                    if fault=='alias-wrong-bank':aliases[0]=aliases[0].replace('ram_block1a0','ram_block1a99')
                    targets+=aliases
                if fault=='source-copy':sources.append(sources[0]+'~DUPLICATE')
                if fault=='source-missing':sources.pop()
                if fault=='source-unknown':sources[0]+='~unknown'
                if fault=='target-missing':targets.pop()
                if fault=='wrong-instance':targets[0]='other|'+targets[0]
                if fault=='wrong-channel':targets[0]=targets[0].replace('ififoADPCM_L','ififoADPCM_X')
                if fault=='bank-bit-duplicate':targets[0]=targets[1]
                period=29.524 if fault!='period' else 14.762
                values=lambda rows:' '.join('{'+r+'}' for r in rows)
                script=Path(tmp)/'scope.tcl';script.write_text(f'''set pocket_xa_min_net_ns 0.5
set sources [list {values(sources)}]
set targets [list {values(targets)}]
proc get_registers {{args}} {{return $::sources}}
proc get_keepers {{args}} {{return $::targets}}
proc get_clocks {{args}} {{return cpu1x}}
proc get_collection_size {{c}} {{llength $c}}
proc get_node_info {{args}} {{lindex $args end}}
proc get_clock_info {{args}} {{return {period}}}
proc foreach_in_collection {{var collection body}} {{foreach item $collection {{uplevel 1 [list set $var $item];uplevel 1 $body}}}}
proc set_net_delay {{args}} {{set ::applied $args;puts APPLIED}}
source {{{ROOT/'scripts/pocket_xa_hold_scope.tcl'}}}
if {{$::applied ne [list -min -from $::sources -to $::targets 0.5]}} {{error BOUND_CHANGED}}
''')
                r=subprocess.run([*tcl_command(),str(script)],capture_output=True,text=True,timeout=15)
                good=fault in ('none','routed-aliases','mapped','source-copy')
                self.assertEqual(r.returncode==0,good,(fault,r.stdout,r.stderr));self.assertEqual('APPLIED' in r.stdout,good)


if __name__=='__main__':unittest.main()
