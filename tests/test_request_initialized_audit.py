"""An audit may accept named copies of one flag without changing its query."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from tool_paths import ROOT,find_tool


def tcl_command():
    if shutil.which('tclsh'):return [shutil.which('tclsh')]
    q=find_tool('quartus_sh');return [q,'-t'] if q else []


class RequestInitializedAudit(unittest.TestCase):
    @unittest.skipUnless(tcl_command(),'Tcl interpreter unavailable')
    def test_exact_query_and_all_physical_named_copies_are_retained(self):
        text=(ROOT/'scripts/audit_pocket_video_request_timing.tcl').read_text()
        proc=text[text.index('proc request_initialized_regs'):text.index('\nset corner 0')]
        root='core_top:ic|gpu_videoout_async:igpu_videoout_async|psx_video_request_cdc:video_request|psx_video_config_cdc:channel|'
        for nodes,good in [([root+'dst_initialized'],True),
                ([root+'dst_initialized',root+'dst_initialized~DUPLICATE'],True),
                ([root+'dst_initialized~DUPLICATE2'],True),([],False),
                ([root+'dst_initialized~unknown'],False),
                ([root+'dst_initialized','other|'+root+'dst_initialized~DUPLICATE'],False)]:
            with self.subTest(nodes=nodes),tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'audit.tcl';values=' '.join('{'+v+'}' for v in nodes)
                path.write_text('''proc get_registers {args} {set ::query $args;return $::nodes}
proc get_collection_size {c} {llength $c}
proc get_node_info {args} {lindex $args end}
proc foreach_in_collection {var collection body} {
 foreach item $collection {uplevel 1 [list set $var $item];uplevel 1 $body}
}
'''+proc+f'''\nset request_channel {{{root}}}
set nodes [list {values}]
set result [request_initialized_regs]
if {{$result ne $nodes}} {{error RETURN_CHANGED}}
if {{$::query ne [list -nowarn {{{root}dst_initialized}}]}} {{error QUERY_CHANGED}}
''')
                run=subprocess.run([*tcl_command(),str(path)],capture_output=True,text=True,timeout=15)
                self.assertEqual(run.returncode==0,good,(nodes,run.stdout,run.stderr))
                if not good:self.assertTrue(any(s in run.stdout+run.stderr for s in ('Unrecognized request initialized register','Missing/ambiguous request initialized geometry')))


if __name__=='__main__':unittest.main()
