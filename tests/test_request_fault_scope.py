"""Keep every named physical copy of the one request fault flag scoped."""
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
    quartus=find_tool('quartus_sh')
    return [quartus,'-t'] if quartus else []


class RequestFaultScope(unittest.TestCase):
    @unittest.skipUnless(tcl_command(),'Tcl interpreter unavailable')
    def test_exact_query_collection_and_other_fields(self):
        text=(ROOT/'scripts/pocket_video_request_scope.tcl').read_text()
        proc=text[:text.index('\nset request_src_clock')]
        prefix='core_top:ic|gpu_videoout_async:igpu_videoout_async|psx_video_request_cdc:video_request|psx_video_config_cdc:channel|'
        cases=[('src_fault',1,[prefix+'src_fault'],True),
            ('src_fault',1,[prefix+'src_fault',prefix+'src_fault~DUPLICATE'],True),
            ('src_fault',1,[prefix+'src_fault~DUPLICATE2'],True),
            ('src_fault',1,[],False),
            ('src_fault',1,[prefix+'src_fault~unknown'],False),
            ('src_fault',1,[prefix+'src_fault[0]'],False),
            ('src_fault',1,[prefix+'src_fault','other|'+prefix+'src_fault~DUPLICATE'],False),
            ('src_fault',1,[prefix.replace('video_request','unrelated')+'src_fault'],False),
            ('source_online_meta',1,[prefix+'source_online_meta',prefix+'source_online_meta~DUPLICATE'],False),
            ('src_reset_pipe[*]',3,[prefix+'src_reset_pipe[0]',prefix+'src_reset_pipe[1]'],False)]
        with tempfile.TemporaryDirectory() as tmp:
            script=Path(tmp)/'scope.tcl'
            for tail,count,nodes,good in cases:
                with self.subTest(tail=tail,nodes=nodes):
                    values=' '.join('{'+v+'}' for v in nodes)
                    query='*|gpu_videoout_async:igpu_videoout_async|psx_video_request_cdc:video_request|psx_video_config_cdc:channel|'+tail
                    script.write_text('''proc get_registers {args} {set ::query $args;return $::nodes}
proc get_collection_size {c} {llength $c}
proc get_node_info {args} {lindex $args end}
proc foreach_in_collection {var collection body} {
 foreach item $collection {uplevel 1 [list set $var $item];uplevel 1 $body}
}
'''+proc+f'''\nset nodes [list {values}]
set result [request_regs {{{tail}}} {count}]
if {{$result ne $::nodes}} {{error RETURN_CHANGED}}
if {{$::query ne [list -nowarn {{{query}}}]}} {{error QUERY_CHANGED}}
''')
                    run=subprocess.run([*tcl_command(),str(script)],capture_output=True,text=True,timeout=15)
                    self.assertEqual(run.returncode==0,good,(nodes,run.stdout,run.stderr))
                    if not good:
                        self.assertTrue(any(s in run.stdout+run.stderr for s in (
                            'Unrecognized request source fault register',
                            'Missing/ambiguous request source fault geometry',
                            'Missing request CDC registers')))


if __name__=='__main__':unittest.main()
