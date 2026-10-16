"""Keep all four logical sampling bits and every named physical replica."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from tool_paths import ROOT, find_tool


def interpreter():
    if shutil.which('tclsh'):
        return [shutil.which('tclsh')]
    quartus = find_tool('quartus_sh')
    return [quartus, '-t'] if quartus else []


STUBS = '''
proc get_registers {args} {set ::query $args;return $::nodes}
proc get_collection_size {c} {llength $c}
proc get_node_info {args} {lindex $args end}
proc foreach_in_collection {var collection body} {
 foreach item $collection {uplevel 1 [list set $var $item];uplevel 1 $body}
}
'''


class VideoPointerReplicas(unittest.TestCase):
    @unittest.skipUnless(interpreter(), 'Tcl interpreter unavailable')
    def test_all_sampling_buses_keep_exact_queries_and_physical_copies(self):
        scope = (ROOT / 'scripts/pocket_video_config_scope.tcl').read_text()
        procedures = scope[:scope.index('\nset video_cfg_src_clock')]
        prefix = 'core_top:ic|gpu_videoout_async:igpu_videoout_async|psx_video_config_cdc:video_config|'
        fifo = prefix + 'dual_clock_fifo:snapshots|'
        cases = []
        for stem in ('wr_addr_gray_rd', 'wr_addr_gray_rd_r', 'rd_addr_gray_wr', 'rd_addr_gray_wr_r'):
            bus = [fifo + f'{stem}[{bit}]' for bit in range(4)]
            cases += [(stem, bus, True), (stem, bus + [bus[0] + '~DUPLICATE'], True),
                      (stem, [bus[0] + '~DUPLICATE2', *bus[1:]], True),
                      (stem, list(reversed(bus)), True),
                      (stem, [*bus[:3], bus[0] + '~DUPLICATE'], False),
                      (stem, [bus[0].replace('[0]', '[4]'), *bus[1:]], False),
                      (stem, [bus[0] + '~unknown', *bus[1:]], False),
                      (stem, ['other|' + bus[0], *bus[1:]], False),
                      (stem, [bus[0].replace('video_config|', 'unrelated|'), *bus[1:]], False),
                      (stem, [bus[0].replace(stem, 'wr_addr_gray'), *bus[1:]], False)]
        program = STUBS + procedures
        for index, (stem, nodes, good) in enumerate(cases):
            values = ' '.join('{' + n + '}' for n in nodes)
            tail = 'dual_clock_fifo:snapshots|' + stem + '[*]'
            query = '*|gpu_videoout_async:igpu_videoout_async|psx_video_config_cdc:video_config|' + tail
            program += f'''
set nodes [list {values}]
set code [catch {{video_cfg_regs {{{tail}}} 4}} result]
if {{($code==0)!={int(good)}}} {{error CASE_{index}_WRONG_VERDICT}}
if {{$code==0 && $result ne $nodes}} {{error CASE_{index}_DROPPED_COPY}}
if {{$::query ne [list -nowarn {{{query}}}]}} {{error CASE_{index}_CHANGED_QUERY}}
'''
        program += 'puts PASS_VIDEO_POINTER_REPLICAS_40\n'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'check.tcl'
            path.write_text(program)
            run = subprocess.run([*interpreter(), str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('PASS_VIDEO_POINTER_REPLICAS_40', run.stdout)

    def test_physical_validator_does_not_add_exceptions_or_relax_bounds(self):
        text = (ROOT / 'scripts/pocket_video_config_scope.tcl').read_text()
        procedure = text[:text.index('\nset video_cfg_src_clock')]
        self.assertNotIn('set_false_path', procedure)
        self.assertNotIn('set_multicycle_path', text)
        for line in ('set_max_delay -from $from -to $to 10.0',
                     'set_net_delay -max -from $from -to $to 10.0',
                     'set_net_delay -min -from $from -to $to 0.0',
                     'set_max_skew -from $from -to $to 10.0'):
            self.assertEqual(text.count(line), 1)


if __name__ == '__main__':
    unittest.main()
