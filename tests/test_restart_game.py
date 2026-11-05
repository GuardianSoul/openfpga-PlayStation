"""prepare_restart_game.py: the Restart Game action resets the console through
psx_boot_gate for 2^16 clk_74a cycles, only on its own bridge address, and
package --restart-game puts the action in interact.json above Swap Discs."""
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from prepare_restart_game import ADDRESS, HOLD_BITS, SDC, patch_core_top, prepare_project  # noqa: E402

CORE = '''module tb;
    reg clk_74a=0, clk1x=0;
    always #7 clk_74a=~clk_74a;
    always #15 clk1x=~clk1x;
    reg bridge_wr=0; reg [31:0] bridge_addr=0; reg reset_n=1;
    wire cpu_reset, freeze_ack;
    psx_boot_gate boot_gate (
        .host_clk(clk_74a),.cpu_clk(clk1x),.reset_async(1'b0),.cpu_locked(1'b1),
        .host_run(reset_n),.bios_ready(1'b1),.freeze_toggle(1'b0),
        .cpu_reset(cpu_reset),.freeze_ack(freeze_ack));
    task write(input [31:0] a); begin @(negedge clk_74a); bridge_addr=a; bridge_wr=1; @(negedge clk_74a); bridge_wr=0; end endtask
    integer n;
    initial begin
        repeat(20) @(negedge clk_74a);
        if(cpu_reset) $fatal(1,"console not running before the restart");
        write(32'h50000044);   // another setting: no restart
        repeat(200) @(negedge clk_74a);
        if(cpu_reset) $fatal(1,"another setting restarted the console");
        write(ADDR);
        repeat(10) @(negedge clk_74a);
        if(!cpu_reset) $fatal(1,"Restart Game did not reset the console");
        n=0; while(cpu_reset) begin @(negedge clk_74a); n=n+1; end
        $display("RESET %0d", n);
        repeat(1000) @(negedge clk_74a);
        if(cpu_reset) $fatal(1,"reset came back");
        $finish;
    end
endmodule
'''


class RestartGame(unittest.TestCase):
    def test_patch_needs_its_anchors(self):
        with self.assertRaises(ValueError): patch_core_top('module x; endmodule')
        text = patch_core_top(CORE)
        self.assertIn('.host_run(reset_n && !restart_hold),.bios_ready(', text)
        self.assertEqual(text.count('reg restart_hold'), 1)

    @unittest.skipUnless(shutil.which('iverilog'), 'iverilog not installed')
    def test_action_resets_the_console(self):
        with TemporaryDirectory() as tmp:
            tb = Path(tmp)/'tb.sv'
            tb.write_text(patch_core_top(CORE).replace('ADDR', f"32'h{ADDRESS:08X}"))
            subprocess.run(['iverilog', '-g2012', '-o', str(Path(tmp)/'tb.vvp'), str(tb), str(ROOT/'rtl/pocket/psx_boot_gate.sv')], check=True)
            out = subprocess.run(['vvp', str(Path(tmp)/'tb.vvp')], capture_output=True, text=True, check=True).stdout
            self.assertNotIn('FATAL', out)
            cycles = int(out.split('RESET ')[1].split()[0])
            self.assertTrue((1 << HOLD_BITS) - 20 <= cycles <= (1 << HOLD_BITS) + 20, cycles)   # counted from 10 cycles after the write

    def test_project_and_package(self):
        with TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out/'core_top.sv').write_text(CORE)
            (out/'apf_top.qsf').write_text(f'set_global_assignment -name SYSTEMVERILOG_FILE "{out}/core_top.sv"\n')
            (out/'pocket.sdc').write_text('# sdc\n')
            prepare_project(out)
            self.assertIn('restart_hold', (out/'core_top.sv').read_text())
            self.assertTrue((out/'pocket.sdc').read_text().endswith(SDC))

    def test_package_lists_it_after_swap_discs(self):
        import json
        with TemporaryDirectory() as tmp:
            project = Path(tmp)/'project'; out = Path(tmp)/'package'
            (project/'output_files').mkdir(parents=True)
            (project/'output_files/apf_top.fit.summary').write_text('Fitter Status : Successful\nTotal virtual pins : 0\n')
            (project/'output_files/apf_top.sta.summary').write_text('Type  : Setup clk\nSlack : 0.100\n')
            (project/'output_files/apf_top.rbf').write_bytes(bytes(20000))
            subprocess.run([sys.executable, str(ROOT/'scripts/package_pocket_early.py'), str(project), '--output', str(out),
                            '--platform', 'playstation', '--disc', '--multi-track', '4', '--one-file-disc', '--cue-mount',
                            '--memcard-slot', '--disc-menu', '4', '--restart-game'], check=True, capture_output=True)
            core = next((out/'SD/Cores').iterdir())
            variables = json.loads((core/'interact.json').read_text())['interact']['variables']
            self.assertEqual([v['id'] for v in variables[:2]], [44, 21])
            self.assertEqual(variables[0], {'name': 'Restart Game', 'id': 44, 'type': 'action', 'enabled': True,
                                            'address': f'0x{ADDRESS:08X}', 'value': 1})


if __name__ == '__main__':
    unittest.main()
