#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Bench of the Swap Discs read-back (scripts/prepare_save_disc_readback.py).

The disc setting's register block and the bridge read multiplexer are cut verbatim out of a
project's core_top.sv, once as it is (OLD) and once after the step (NEW), and run side by side in
iverilog under one stimulus (the other inputs of the multiplexer are stand-ins that differ per
address). Checks:
  manual    menu picks 1, 2, 0, 1, 2 (Disc 2, 3, 1, 2, 3): the setting the swap logic gets is the
            pick, and a read of 0x50000044 in the very next cycle returns the pick (never the old
            value); a pick written in any byte lane (the Pocket's checkbox habit) reads back the same
  load      the routine's DISC_SET toggle (a Memory load on disc 3) with the menu on Disc 1: the
            read-back follows once the toggle is synchronised; a menu pick afterwards wins again
  equal     every other address read during the run (a sweep of the settings window and the
            device addresses) returns the same in OLD and NEW; OLD reads 0 at 0x50000044 (test36)
Every planted fault of the step (FAULTS) must fail a check.
Usage: run_save_disc_readback.py PROJECT   (a frozen snapshot before the step, e.g. E4b project-s6)
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_save_disc_readback as step


def cut(text):
    """-> (register block, read multiplexer) cut verbatim from core_top.sv."""
    m = re.search(r"    \(\* async_reg=\"true\", preserve, dont_merge \*\) reg \[1:0\] save_disc_set_sync=2'b00;\n.*?\n    end\n", text, re.S)
    mux = re.search(r'    always @\* begin\n        bridge_rd_data=service_read;\n.*?\n    end\n', text, re.S)
    if not m or not mux:
        raise ValueError('disc setting block or read multiplexer not found')
    return m.group(0), mux.group(0)


def module(name, text):
    regs, mux = cut(text)
    return f'''
module {name}(input clk_74a, input bridge_wr, input [31:0] bridge_addr, input [31:0] bridge_wr_data,
              input save_disc_set_tgl, input [2:0] save_disc_set, output reg [31:0] bridge_rd_data, output [2:0] setting);
    reg [2:0] disc_setting=0;
    wire [31:0] service_read = bridge_addr[31:24]==8'h50 ? 32'b0 : {{bridge_addr[15:0], 16'h5E5E}};   // the core's has no 0x50 cases
    wire [31:0] cmd_bridge_rd_data = 32'hC0DE0000 | bridge_addr[15:0];
    wire [31:0] cue_struct_rd = 32'h2C2C0000 | bridge_addr[15:0];
    wire [31:0] memcard_rd_data = 32'h3C3C0000 | bridge_addr[15:0];
    wire checking=1'b1, failed=1'b0, passed=1'b1; wire [1:0] mem_ready74=2'b10;
{regs}{mux}    assign setting = disc_setting;
endmodule
'''


TB = r'''
module tb;
    reg clk=0; always #5 clk=~clk;
    reg wr=0, tgl=0; reg [31:0] addr=0, wdata=0; reg [2:0] sset=0;
    wire [31:0] rd_old, rd_new; wire [2:0] set_old, set_new;
    integer errors=0, i, k;
    reg [31:0] sweep [0:15];
    old_m o(.clk_74a(clk),.bridge_wr(wr),.bridge_addr(addr),.bridge_wr_data(wdata),.save_disc_set_tgl(tgl),.save_disc_set(sset),.bridge_rd_data(rd_old),.setting(set_old));
    new_m n(.clk_74a(clk),.bridge_wr(wr),.bridge_addr(addr),.bridge_wr_data(wdata),.save_disc_set_tgl(tgl),.save_disc_set(sset),.bridge_rd_data(rd_new),.setting(set_new));
    task fail(input [8*72-1:0] what); begin $display("FAIL: %0s", what); errors=errors+1; end endtask
    task others; begin   // every other address: OLD == NEW
        for(k=0;k<16;k=k+1) begin addr=sweep[k]; #1; if(rd_old!==rd_new) begin $display("addr %08x old %08x new %08x",addr,rd_old,rd_new); fail("equal: another address reads differently"); end end
        addr=32'h50000044; #1; if(rd_old!==0) fail("equal: OLD reads nonzero at 0x50000044");
    end endtask
    task pick(input [2:0] d, input integer lane); begin
        @(negedge clk); wr=1; addr=32'h50000044; wdata={29'b0,d}<<(8*lane); @(negedge clk); wr=0;
        // the very next cycle: the read-back is the pick and so is the swap logic's setting
        addr=32'h50000044; #1;
        if(rd_new!=={29'b0,d}) begin $display("pick %0d lane %0d read %08x",d,lane,rd_new); fail("manual: read-back is not the pick"); end
        if(set_new!==d) fail("manual: the setting is not the pick");
        if(set_old!==set_new) fail("manual: setting differs from OLD");
        others;
    end endtask
    initial begin
        sweep[0]=32'h50000040; sweep[1]=32'h50000048; sweep[2]=32'h50000004; sweep[3]=32'h50000088;
        sweep[4]=32'h10000000; sweep[5]=32'h12000158; sweep[6]=32'h13000000; sweep[7]=32'h30000000;
        sweep[8]=32'h20000044; sweep[9]=32'hF8000044; sweep[10]=32'h2C000044; sweep[11]=32'h50000045;
        sweep[12]=32'h40000044; sweep[13]=32'h50000144; sweep[14]=32'h500000C4; sweep[15]=32'hD0000044;
        repeat(3) @(negedge clk);
        addr=32'h50000044; #1; if(rd_new!==0) fail("reset: read-back is not Disc 1");
        others;
        // manual: Disc 2, 3, 1, 2, 3; then in other byte lanes
        pick(1,0); pick(2,0); pick(0,0); pick(1,0); pick(2,0);
        pick(1,1); pick(2,2); pick(0,3); pick(1,3);
        // the read-back stays while nothing writes (no snap back)
        repeat(50) @(negedge clk); addr=32'h50000044; #1; if(rd_new!==1) fail("manual: read-back changed by itself");
        // load: menu on Disc 1, a Memory load on Disc 3 (DISC_SET toggle)
        pick(0,0);
        @(negedge clk); sset=2; tgl=~tgl;
        repeat(5) @(negedge clk); addr=32'h50000044; #1;
        if(rd_new!==2) fail("load: read-back does not follow DISC_SET");
        if(set_new!==2 || set_old!==2) fail("load: setting does not follow DISC_SET");
        others;
        // a menu pick afterwards wins again
        pick(1,0);
        repeat(5) @(negedge clk); addr=32'h50000044; #1; if(rd_new!==1) fail("load: a later pick lost");
        if(errors==0) $display("PASS"); else $display("%0d errors",errors);
        $finish;
    end
endmodule
'''


def run(old_text, new_text, work, iverilog, vvp):
    (work / 'tb.sv').write_text(module('old_m', old_text) + module('new_m', new_text) + TB)
    r = subprocess.run([iverilog, '-g2012', '-o', str(work / 'tb.vvp'), str(work / 'tb.sv')], capture_output=True, text=True)
    if r.returncode:
        return False, r.stdout + r.stderr
    r = subprocess.run([vvp, str(work / 'tb.vvp')], capture_output=True, text=True)
    return 'PASS' in r.stdout, r.stdout + r.stderr


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('project', type=Path)
    args = ap.parse_args()
    from tool_paths import ROOT
    iverilog = shutil.which('iverilog') or str(ROOT / 'build/tools/iverilog/bin/iverilog')
    vvp = shutil.which('vvp') or str(ROOT / 'build/tools/iverilog/bin/vvp')
    old = step.compiled(args.project.resolve(), 'core_top.sv', 'SYSTEMVERILOG_FILE').read_text()
    new = step.core_top(old)
    failures = []
    with tempfile.TemporaryDirectory() as d:
        ok, log = run(old, new, Path(d), iverilog, vvp)
        print('step:', 'pass' if ok else 'FAIL'); print(log.strip()) if not ok else None
        if not ok: failures.append('step')
        for name, (a, b) in step.FAULTS.items():
            if new.count(a) != 1: raise SystemExit(f'fault {name}: anchor')
            ok, log = run(old, new.replace(a, b), Path(d), iverilog, vvp)
            first = next((l for l in log.splitlines() if l.startswith('FAIL')), log.strip().splitlines()[-1] if log.strip() else '')
            print(f'fault {name}:', 'NOT DETECTED' if ok else f'caught ({first})')
            if ok: failures.append('fault ' + name)
    if failures: raise SystemExit('disc read-back check failed: ' + ', '.join(failures))
    print('PASS disc read-back')


if __name__ == '__main__':
    main()
