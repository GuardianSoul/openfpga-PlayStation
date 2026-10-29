"""prepare_boot_waits_mount.py: the boot timer waits for a running cue mount.

The generated timer (from prepare_pocket_disc_swap's --boot-waits-disc text,
then this step) is simulated with its counter scaled down by 2^20: 2^10
cycles stand for 14.46 s and 2^12 for the 57.8 s cap.
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
TIMER, CAP = 1 << 10, 1 << 12


def timer_module(text, name, width):
    """The timer register and always block of text as a module, counter width scaled."""
    reg = re.search(r'^    reg \[\d+:0\] disc_wait=0; reg disc_wait_done=0;\n', text, re.M)[0]
    block = re.search(r'^(    wire cue_mount_busy=.*\n)?    always @\(posedge clk_74a\)\n.*\n.*disc_wait_done<=1\'b1; end\n', text, re.M)[0]
    body = (reg + block).replace('[31:0]', f'[{width - 1}:0]').replace('reg [29:0]', f'reg [{width - 1}:0]').replace('[31:30]', f'[{width - 1}:{width - 2}]').replace('[29:0]', f'[{width - 3}:0]')
    return (f'module {name}(input wire clk_74a, power_reset_n, reset_n, dataslot_allcomplete, cue_s_allcomplete,\n'
            f'              output wire done);\n{body}    assign done=disc_wait_done;\nendmodule\n')


TB = '''module tb;
    reg clk=0, prst=0, run=0, apf=0, cue=0;
    wire done_new, done_old;
    timer_new n(.clk_74a(clk),.power_reset_n(prst),.reset_n(run),.dataslot_allcomplete(apf),.cue_s_allcomplete(cue),.done(done_new));
    timer_old o(.clk_74a(clk),.power_reset_n(prst),.reset_n(run),.dataslot_allcomplete(apf),.cue_s_allcomplete(cue),.done(done_old));
    integer t, t_new, t_old, apf_at, cue_at;
    always #1 clk=~clk;
    initial begin
        if(!$value$plusargs("apf=%d",apf_at)) apf_at=-1;
        if(!$value$plusargs("cue=%d",cue_at)) cue_at=-1;
        t_new=-1; t_old=-1;
        repeat(3) @(negedge clk); prst=1; @(negedge clk); run=1;
        for(t=0;t<6000;t=t+1) begin
            if(t==apf_at) apf=1;
            if(t==cue_at) cue=1;
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
            src.write_text(timer_module(text, 'timer_new', 12) + timer_module(TOP, 'timer_old', 10) + TB)
            subprocess.run([self.iverilog, '-o', f'{tmp}/tb.vvp', str(src)], check=True)
            out = subprocess.run([self.vvp, f'{tmp}/tb.vvp', *[f'+{k}={v}' for k, v in events.items()]],
                                 capture_output=True, text=True, check=True).stdout
        new, old = map(int, re.search(r'new (-?\d+) old (-?\d+)', out).groups())
        return new, old

    def test_no_mount_ends_as_before(self):
        # No game, or APF still loading: the 14.46 s timer, as without the step.
        new, old = self.run_tb(transform(TOP))
        self.assertEqual(new, old)
        self.assertAlmostEqual(new, TIMER, delta=2)

    def test_mount_done_before_timer(self):
        new, old = self.run_tb(transform(TOP), apf=100, cue=500)
        self.assertEqual(new, old)

    def test_slow_mount_holds_the_cpu(self):
        # The mount ends past 14.46 s: the timer waits for it (the CPU used to
        # leave reset with no disc at 14.46 s).
        new, old = self.run_tb(transform(TOP), apf=100, cue=2500)
        self.assertAlmostEqual(old, TIMER, delta=2)
        self.assertAlmostEqual(new, 2500, delta=3)

    def test_hung_mount_hits_the_cap(self):
        new, _ = self.run_tb(transform(TOP), apf=100)
        self.assertAlmostEqual(new, CAP, delta=2)

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
