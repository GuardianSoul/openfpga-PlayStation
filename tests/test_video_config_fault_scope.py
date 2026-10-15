"""The sticky fault guard accepts named copies without changing its query."""
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
    q=find_tool('quartus_sh')
    return [q,'-t'] if q else []


class VideoFaultScope(unittest.TestCase):
    @unittest.skipUnless(tcl_command(),'Tcl interpreter unavailable')
    def test_named_replicas_keep_exact_query_and_other_fields_stay_strict(self):
        text=(ROOT/'scripts/pocket_video_config_scope.tcl').read_text()
        proc=text[:text.index('\nset video_cfg_src_clock')]
        root='core_top:ic|gpu_videoout_async:igpu_videoout_async|psx_video_config_cdc:video_config|'
        cases=[('src_fault',1,[root+'src_fault'],True),
            ('src_fault',1,[root+'src_fault',root+'src_fault~DUPLICATE'],True),
            ('src_fault',1,[],False),
            ('src_fault',1,[root+'src_fault~unknown'],False),
            ('src_fault',1,[root+'src_fault','other|'+root+'src_fault~DUPLICATE'],False),
            ('source_online_meta',1,[root+'source_online_meta',root+'source_online_meta~DUPLICATE'],False)]
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'scope.tcl'
            for tail,n,nodes,good in cases:
                node_text=' '.join('{'+v+'}' for v in nodes)
                query='*|gpu_videoout_async:igpu_videoout_async|psx_video_config_cdc:video_config|'+tail
                p.write_text('''proc get_registers {args} {set ::last_query $args;return $::nodes}
proc get_collection_size {collection} {llength $collection}
proc get_node_info {args} {lindex $args end}
proc foreach_in_collection {var collection body} {
 foreach item $collection {uplevel 1 [list set $var $item];uplevel 1 $body}
}
'''+proc+f'''\nset nodes [list {node_text}]
set r [video_cfg_regs {tail} {n}]
if {{$r ne $::nodes}} {{error RETURN_CHANGED}}
if {{$::last_query ne [list -nowarn {{{query}}}]}} {{error QUERY_CHANGED}}
''')
                r=subprocess.run([*tcl_command(),str(p)],capture_output=True,text=True,timeout=15)
                self.assertEqual(r.returncode==0,good,(tail,nodes,r.stdout,r.stderr))
                if not good:
                    self.assertTrue(any(v in r.stdout+r.stderr for v in (
                        'Missing/ambiguous video source fault geometry',
                        'Unrecognized video source fault register',
                        'Missing video config registers')))


if __name__=='__main__':unittest.main()
