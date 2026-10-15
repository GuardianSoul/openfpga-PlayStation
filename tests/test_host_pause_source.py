"""Host pause preparation must preserve the rest of the connected top/scope."""
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_host_pause_cdc import adapt_top,prepare_project,PATCHES
from prepare_host_config_cdc import INSTANCE
from prepare_resource_targets import digest
from tool_paths import ROOT


class HostPauseSource(unittest.TestCase):
    def fixture(self,root):
        core=root/'core_top.sv';scope=root/'host.sdc';qsf=root/'apf_top.qsf'
        core.write_text("    reg pause=0;\n    reg isPal=0;\n"+INSTANCE+
            "if(bridge_wr && bridge_addr==32'h11000018) pause<=bridge_wr_data[0:0];\n"+
            "if(bridge_wr && bridge_addr==32'h11000154) isPal<=bridge_wr_data[0:0];\n"+
            ".pause(pause),\n.isPal(isPal),\n32'h12000158: service_read={31'b0,config_current};\n")
        scope.write_text((ROOT/'scripts/pocket_config_scope.tcl').read_text())
        qsf.write_text(f'set_global_assignment -name SYSTEMVERILOG_FILE "{core}" -library work\n'+
                       f'set_global_assignment -name SDC_FILE "{scope}"\n')
        return core,scope,qsf

    def test_top_restores_exactly_and_only_adds_snapshot_pause_bit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);core,scope,qsf=self.fixture(root)
            original=core.read_text();old_scope=scope.read_text();qsha=digest(qsf)
            r=prepare_project(root,digest(core));restored=core.read_text()
            for before,after in reversed(PATCHES):restored=restored.replace(after,before,1)
            self.assertEqual(restored,original);self.assertEqual(digest(qsf),qsha)
            self.assertIn('.pause(pause)',core.read_text())
            self.assertIn('.src_data({isPal_host,pause_host,biosregion_host,region_host})',core.read_text())
            self.assertIn('.dst_data({isPal,pause,biosregion,region})',core.read_text())
            # The old bounds and exception commands survive byte-for-byte;
            # only width queries and an explicit width tag differ.
            changed=scope.read_text().split('set config_width 6\n',1)[1]
            for name in ('payload','dst_data'):changed=changed.replace('config_regs '+name+' 6','config_regs '+name+' 4')
            self.assertEqual(changed,old_scope)
            self.assertFalse(r['numeric_bounds_changed']);self.assertFalse(r['guest_reset_or_boot_gating_changed'])
            with self.assertRaises(ValueError):prepare_project(root,digest(core))

    def test_bad_identity_gating_or_scope_reject_without_writes(self):
        for reason in ('sha','gating','scope'):
            with self.subTest(reason=reason),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);core,scope,qsf=self.fixture(root)
                if reason=='gating':core.write_text(core.read_text().replace("32'h12000158: service_read={31'b0,config_current};",'assign reset=config_current;'))
                if reason=='scope':scope.write_text(scope.read_text()+'set_false_path -from [get_clocks X]\n')
                before={p:digest(p) for p in (core,scope,qsf)}
                with self.assertRaises(ValueError):prepare_project(root,'0'*64 if reason=='sha' else digest(core))
                self.assertEqual(before,{p:digest(p) for p in before})
                self.assertFalse((root/'host-pause-cdc.json').exists())

    def test_changed_raw_pause_register_or_duplicate_binding_rejects(self):
        with tempfile.TemporaryDirectory() as tmp:
            core,_,_=self.fixture(Path(tmp));raw=core.read_text()
            for changed in (raw.replace('reg pause=0;','reg pause=1;'),raw+'.pause(pause)'):
                with self.assertRaises(ValueError):adapt_top(changed)


if __name__=='__main__':unittest.main()
