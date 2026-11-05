"""prepare_boot_waits_mount.py: the CPU leaves reset on the disc mount alone, with no timer.

The boot gate from prepare_pocket_disc_swap's --boot-waits-disc text, before and
after this step, runs in Icarus with its 30-bit timer scaled down by 2^20.
"""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from scripts.prepare_boot_waits_mount import transform
from scripts.prepare_pocket_disc_swap import BOOT_WAIT
from scripts.tool_paths import find_tool

# The parts of core_top the step edits, in their order there.
TOP = ('    wire dataslot_allcomplete;\n' + BOOT_WAIT.replace('READY_SIGNAL', 'bios_go') +
       '    wire cue_s_allcomplete, cue_table_grant;\n')
TIMER = 1 << 10


def gate_module(text, name):
    """The boot gate of text as a module, its timer scaled."""
    body = text.replace('reg [29:0]', 'reg [9:0]')
    return (f'module {name}(input wire clk_74a, power_reset_n, reset_n, bios_go, cd_disc_settled, output wire done);\n'
            f'{body}    assign done=boot_disc_gate;\nendmodule\n')


TB = '''module tb;
    reg clk=0, prst=0, run=0, go=0, settled=0;
    wire done_new, done_old;
    gate_new n(.clk_74a(clk),.power_reset_n(prst),.reset_n(run),.bios_go(go),.cd_disc_settled(settled),.done(done_new));
    gate_old o(.clk_74a(clk),.power_reset_n(prst),.reset_n(run),.bios_go(go),.cd_disc_settled(settled),.done(done_old));
    integer t, t_new, t_old, settle_at;
    always #1 clk=~clk;
    initial begin
        if(!$value$plusargs("settle=%d",settle_at)) settle_at=-1;
        t_new=-1; t_old=-1;
        repeat(3) @(negedge clk); prst=1; go=1; @(negedge clk); run=1;
        for(t=0;t<6000;t=t+1) begin
            if(t==settle_at) settled=1;
            @(negedge clk);
            if(done_new && t_new<0) t_new=t;
            if(done_old && t_old<0) t_old=t;
        end
        $display("new %0d old %0d", t_new, t_old);
        $finish;
    end
endmodule
'''


class BootWaitsMountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.iverilog, cls.vvp = find_tool('iverilog'), find_tool('vvp')

    def run_tb(self, text, **events):
        if not self.iverilog: self.skipTest('iverilog not found')
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)/'tb.v'
            src.write_text(gate_module(text, 'gate_new') + gate_module(TOP, 'gate_old') + TB)
            subprocess.run([self.iverilog, '-g2012', '-o', f'{tmp}/tb.vvp', str(src)], check=True)
            out = subprocess.run([self.vvp, f'{tmp}/tb.vvp', *[f'+{k}={v}' for k, v in events.items()]],
                                 capture_output=True, text=True, check=True).stdout
        new, old = map(int, re.search(r'new (-?\d+) old (-?\d+)', out).groups())
        return new, old

    def test_timer_is_gone(self):
        self.assertNotIn('disc_wait', transform(TOP))

    def test_mount_before_the_old_timer_releases_as_before(self):
        new, old = self.run_tb(transform(TOP), settle=100)
        self.assertEqual(new, old)
        self.assertAlmostEqual(new, 100, delta=4)

    def test_slow_mount_holds_the_cpu(self):
        # The cold-card race: the old gate let the CPU go at the timer, before the mount.
        new, old = self.run_tb(transform(TOP), settle=2500)
        self.assertAlmostEqual(old, TIMER, delta=4)
        self.assertAlmostEqual(new, 2500, delta=4)

    def test_no_mount_keeps_the_cpu_in_reset(self):
        new, old = self.run_tb(transform(TOP))
        self.assertAlmostEqual(old, TIMER, delta=4)
        self.assertEqual(new, -1)

    def test_needs_cue_mount(self):
        with self.assertRaises(ValueError):
            transform(TOP.replace('    wire cue_s_allcomplete, cue_table_grant;\n', ''))

    def test_project_edits_the_compiled_core_top(self):
        # prepare_save_states.py compiles save-states/core_top.sv; the root copy must stay untouched
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project/'save-states').mkdir()
            (project/'core_top.sv').write_text(TOP)
            (project/'save-states'/'core_top.sv').write_text(TOP)
            (project/'apf_top.qsf').write_text(
                f'set_global_assignment -name SYSTEMVERILOG_FILE "{project}/save-states/core_top.sv" -library work\n')
            subprocess.run(['python3', str(Path(__file__).resolve().parents[1]/'scripts/prepare_boot_waits_mount.py'), str(project)],
                           check=True, capture_output=True)
            self.assertEqual((project/'save-states'/'core_top.sv').read_text(), transform(TOP))
            self.assertEqual((project/'core_top.sv').read_text(), TOP)


if __name__ == '__main__':
    unittest.main()
