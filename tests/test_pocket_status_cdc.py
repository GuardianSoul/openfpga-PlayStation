"""Exact diagnostic field bindings; live service/guest connections stay intact."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_pocket_status_cdc import adapt_top,restore_top,prepare_project,FIELDS
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def tcl_command():
    if shutil.which('tclsh'):return [shutil.which('tclsh')]
    q=find_tool('quartus_sh')
    return [q,'-t'] if q else []


class StatusBindings(unittest.TestCase):
    def top(self):
        return '''module core_top;
    (* preserve, dont_merge *) reg config_reset=1;
    psx_config_cdc #(.WIDTH(4)) host_config (
        .src_clk(clk_74a),.dst_clk(clk1x),.reset_async(config_reset));
    reg [31:0] service_read;
    always @* begin
        service_read=0;
        case(bridge_addr)
'''+''.join(f"        32'h{a}: service_read={n};\n" for n,a,b in FIELDS)+'''        32'h120000f0: service_read=memcard_changed;
        endcase
    end
    // SD service (psx_cd_stream), independent of diagnostic polling.
    psx_cd_stream disc_stream (.cd_hps_req(cd_hps_req),.cd_hps_lba(cd_hps_lba));
endmodule
'''

    def test_all_bit_bindings_and_non_diagnostic_source_restore_exactly(self):
        before=self.top();after=adapt_top(before)
        self.assertEqual(restore_top(after),before)
        for name,address,bits in FIELDS:
            self.assertIn(f"32'h{address}: service_read=status_snapshot[{bits}];",after)
        self.assertIn('.src_data({cd_hps_lba_sim,cd_hps_lba,region_out,resetFromCD,cd_hps_req,isPaused})',after)
        self.assertIn('psx_cd_stream disc_stream (.cd_hps_req(cd_hps_req),.cd_hps_lba(cd_hps_lba));',after)
        with self.assertRaises(ValueError):adapt_top(after)

    def test_unknown_fields_or_reset_geometry_fail_closed(self):
        for before,after in [("120000c0","120002c0"),('reg config_reset=1;','reg config_reset=0;'),
                ('host_config (','other_host_config ('),
                ('.cd_hps_lba(cd_hps_lba)','.cd_hps_lba(other_lba)')]:
            with self.subTest(before=before),self.assertRaises(ValueError):
                adapt_top(self.top().replace(before,after))

    def test_project_identity_guards_and_opt_in_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);top=root/'core_top.sv';top.write_text(self.top())
            qsf=root/'apf_top.qsf';qsf.write_text(f'set_global_assignment -name SYSTEMVERILOG_FILE "{top}" -library work\n')
            (root/'pocket.sdc').write_text('derive_clock_uncertainty\n')
            sha=digest(top)
            with self.assertRaises(ValueError):prepare_project(root,'0'*64)
            self.assertFalse((root/'status-cdc').exists())
            report=prepare_project(root,sha)
            self.assertTrue(report['non_diagnostic_source_identity_verified'])
            self.assertFalse(report['guest_or_memory_service_path_changed'])
            self.assertFalse(report['fit_verified']);self.assertFalse(report['hardware_verified'])
            self.assertEqual(len(report['added']),2)
            self.assertEqual(restore_top(top.read_text()),self.top())
            with self.assertRaises(ValueError):prepare_project(root,digest(top))

    @unittest.skipUnless(tcl_command(),'Tcl interpreter unavailable')
    def test_physical_replica_selector_keeps_exact_logical_bits_and_instance(self):
        text=(ROOT/'scripts/pocket_status_scope.tcl').read_text()
        proc=text[text.index('proc status_reg'):text.index('\nset status_source_clock')]
        root='core_top:ic|psx_status_cdc:status_readback|'
        cases=[('request',1,[root+'request~DUPLICATE'],True),
            ('request',1,[root+'request',root+'request~DUPLICATE2'],True),
            ('request',1,[],False),('request',1,[root+'request~other'],False),
            ('request',1,['another|request~DUPLICATE'],False),
            ('src_reset_pipe',3,[root+f'src_reset_pipe[{i}]' for i in range(3)],True),
            ('src_reset_pipe',3,[root+f'src_reset_pipe[{i}]' for i in range(2)],False)]
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'selector.tcl'
            for name,width,nodes,good in cases:
                body=' '.join('{'+n+'}' for n in nodes)
                p.write_text('''proc get_registers {args} {return $::nodes}
proc get_collection_size {collection} {llength $collection}
proc get_node_info {args} {lindex $args end}
proc foreach_in_collection {var collection body} {
 foreach item $collection {uplevel 1 [list set $var $item];uplevel 1 $body}
}
'''+proc+f'\nset nodes [list {body}]\nstatus_reg {{{root}}} {name} {width}\n')
                r=subprocess.run([*tcl_command(),str(p)],capture_output=True,text=True,timeout=15)
                self.assertEqual(r.returncode==0,good,(name,nodes,r.stdout,r.stderr))


if __name__=='__main__':unittest.main()
