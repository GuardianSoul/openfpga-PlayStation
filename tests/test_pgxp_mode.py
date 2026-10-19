"""Runtime transitions must clear provenance and wait for a quiet boundary."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BENCH = r'''
`timescale 1ns/1ps
module tb;
reg clk=0;always #5 clk=~clk;
reg reset=1,requested=0,quiescent=0;wire enabled,clear;
integer sweep=17,clears=0,changes=0;wire initializing=sweep!=0;
psx_pgxp_mode dut(.*);
always@(posedge clk)begin
 if(reset || clear)sweep<=17;else if(sweep!=0)sweep<=sweep-1;
 if(clear && !reset)clears++;
end
reg old_enabled=0,old_quiet=0,old_initializing=1,old_reset=1;
always@(posedge clk)begin
 old_enabled=enabled;old_quiet=quiescent;old_initializing=initializing;old_reset=reset;
 #1;
 if(!reset && !old_reset)begin
  if(enabled!=old_enabled)begin
   if(!old_quiet)$fatal(1,"Mode changed during geometry/DMA/memory activity");changes++;
   if(enabled && old_initializing)$fatal(1,"Mode enabled before metadata clear finished");
  end
 end
end
task tick;begin @(negedge clk);end endtask
initial begin
 repeat(5)tick();reset=0;quiescent=1;repeat(40)tick();
 if(enabled || initializing)$fatal(1,"Default Off or initial clear failed");
 clears=0;
 // No clear/enable change while a packet, DMA or physical transaction is live.
 for(integer epoch=0;epoch<40;epoch++)begin
  requested=~requested;quiescent=0;repeat(8)tick();
  if(enabled===requested)$fatal(1,"Request bypassed quiescence");
  begin integer old_clears,waited;
   old_clears=clears;quiescent=1;waited=0;
   while(enabled!==requested || clears==old_clears || initializing)begin
    tick();waited++;if(waited>60)$fatal(1,"Stable requested mode failed to converge/clear");
   end
  end
  repeat(8)tick();
 end
 if(clears!=40 || changes!=40)$fatal(1,"Missing or repeated mode transition clear %0d/%0d",clears,changes);
 // A guest reset during initialization cancels the old precision context.
 requested=1;repeat(4)tick();reset=1;repeat(3)tick();reset=0;
 repeat(50)tick();if(!enabled || initializing)$fatal(1,"Reset recovery lost desired setting");
 $display("PASS PGXP mode transitions clears=%0d changes=%0d",clears,changes);$finish;
end
initial begin #100000;$fatal(1,"Mode test timeout");end
endmodule
'''


@unittest.skipUnless(shutil.which('iverilog') and shutil.which('vvp'), 'Icarus Verilog is required')
class PgxpMode(unittest.TestCase):
    def simulate(self, source):
        with tempfile.TemporaryDirectory(prefix='pgxp-mode-') as directory:
            directory = Path(directory)
            (directory / 'mode.sv').write_text(source)
            (directory / 'tb.sv').write_text(BENCH)
            compiled = subprocess.run(['iverilog', '-g2012', '-s', 'tb', '-o', str(directory / 'run'),
                                       str(directory / 'mode.sv'), str(directory / 'tb.sv')],
                                      capture_output=True, text=True, timeout=30)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            return subprocess.run(['vvp', '-N', str(directory / 'run')],
                                  capture_output=True, text=True, timeout=30)

    def test_runtime_contract(self):
        result = self.simulate((ROOT / 'rtl/pocket/psx_pgxp_mode.sv').read_text())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS PGXP mode transitions', result.stdout)

    def test_real_transition_faults(self):
        source = (ROOT / 'rtl/pocket/psx_pgxp_mode.sv').read_text()
        faults = {
            'live-packet': ('request_sync[1]!=enabled && quiescent', 'request_sync[1]!=enabled'),
            'unfinished-clear': ('!initializing && quiescent', 'quiescent'),
            'missing-clear': ('enabled<=0;clear<=1;state<=CLEAR_WAIT;', 'enabled<=0;state<=CLEAR_WAIT;'),
        }
        for name, (old, new) in faults.items():
            with self.subTest(fault=name):
                self.assertIn(old, source)
                result = self.simulate(source.replace(old, new))
                self.assertNotEqual(result.returncode, 0, name + ' escaped the protocol oracle')
                self.assertIn('FATAL', result.stdout)


if __name__ == '__main__':
    unittest.main()
