"""Exercise the precision FIFO's independent X/Y strobes and CPU priority."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.tool_paths import find_tool

ROOT = Path(__file__).resolve().parents[1]
BENCH = r'''
module tb;
    reg clk=0, reset=1, ce=1, enabled=0, px=0, py=0, clipped=0, wr=0;
    reg [3:0] fraction=0;
    reg [5:0] wa=0, ra=0;
    reg [9:0] incoming=0;
    wire [9:0] actual;
    psx_pgxp_gte_fifo dut(clk,reset,ce,enabled,px,py,fraction,clipped,
                         wr,wa,ra,incoming,actual);
    reg [4:0] x[0:2], y[0:2];
    reg [4:0] nx,ny;
    reg [9:0] expected;
    integer cycle, i, a, slot, checked=0, valid=0, disabled=0;
    integer state=32'h52454f47;
    function integer random32;
        input integer unused;
        begin
            state=state^(state<<13);state=state^(state>>17);state=state^(state<<5);
            random32=state;
        end
    endfunction
    initial begin
        for(i=0;i<3;i=i+1)begin x[i]=0;y[i]=0;end
        for(cycle=0;cycle<100000;cycle=cycle+1)begin
            {wr,clipped,px,py,ce}=random32(0);
            wa=random32(0);fraction=random32(0);incoming=random32(0);
            // Frequent real SXY writes, plus unrelated concurrent writes.
            if(cycle%3==0)wa=12+(cycle%4);
            reset=(cycle%137==0);enabled=(cycle%113<97);
            nx=wr ? {incoming[8] && wa>=12 && wa<=15,incoming[3:0]} :
                    {!clipped,fraction};
            ny=wr ? {incoming[9] && wa>=12 && wa<=15,incoming[7:4]} :
                    {!clipped,fraction};
            if(reset || !enabled)begin
                for(i=0;i<3;i=i+1)begin x[i]=0;y[i]=0;end
            end else if(ce)begin
                if(px || (wr && wa==15))begin x[0]=x[1];x[1]=x[2];x[2]=nx;end
                else if(wr && wa>=12 && wa<=14)x[wa-12]=nx;
                if(py || (wr && wa==15))begin y[0]=y[1];y[1]=y[2];y[2]=ny;end
                else if(wr && wa>=12 && wa<=14)y[wa-12]=ny;
            end
            #2;clk=1;#2;clk=0;
            for(a=0;a<64;a=a+1)begin
                ra=a;#1;expected=0;
                if(enabled && a>=12 && a<=15)begin
                    slot=a==15 ? 2 : a-12;
                    expected={y[slot][4],x[slot][4],
                              y[slot][4] ? y[slot][3:0] : 4'd0,
                              x[slot][4] ? x[slot][3:0] : 4'd0};
                end
                if(actual!==expected)$fatal(1,"FIFO cycle %0d addr %0d got %h expected %h",cycle,a,actual,expected);
                checked=checked+1;
                if(|actual[9:8])valid=valid+1;
                if(!enabled)disabled=disabled+1;
            end
        end
        if(valid<10000 || disabled<10000)$fatal(1,"vacuous FIFO coverage");
        $display("PASS PGXP FIFO cycles=%0d checks=%0d valid=%0d disabled=%0d",cycle,checked,valid,disabled);
    end
endmodule
'''


class PGXPGTEFIFOTests(unittest.TestCase):
    def run_fifo(self, mutation=None):
        compiler, runtime = find_tool('iverilog'), find_tool('vvp')
        if not compiler or not runtime:
            self.skipTest('Icarus Verilog is required')
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = (ROOT / 'rtl/pocket/psx_pgxp_gte_fifo.sv').read_text()
            if mutation:
                old, new = mutation
                self.assertEqual(source.count(old), 1)
                source = source.replace(old, new)
            (work / 'fifo.sv').write_text(source)
            (work / 'tb.sv').write_text(BENCH)
            subprocess.run([compiler, '-g2012', '-s', 'tb', '-o', str(work / 'run'),
                            str(work / 'fifo.sv'), str(work / 'tb.sv')], check=True,
                           capture_output=True, text=True)
            return subprocess.run([runtime, str(work / 'run')], capture_output=True,
                                  text=True, timeout=45)

    def test_independent_strobes_cpu_collisions_modes_and_pauses(self):
        result = self.run_fifo()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('checks=6400000', result.stdout)

    def test_wrong_vertex_and_stale_enable_are_observable(self):
        for mutation in [('2\'d1 : 2\'d2', '2\'d1 : 2\'d0'),
                         ('reset || !enabled', 'reset')]:
            with self.subTest(mutation=mutation):
                result = self.run_fifo(mutation)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('FIFO cycle', result.stdout)


if __name__ == '__main__':
    unittest.main()
