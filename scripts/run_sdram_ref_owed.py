#!/usr/bin/env python3
"""Lockstep prepare_sdram_ref_owed.py against the controller it transforms.

Two copies of sim/memory/tb_psx_sdram_ctrl.sv's sdram_bench run the same
seeded traffic (CPU, cache, DMA, SPU, DMA FIFO chains, forced refreshes),
one on rtl/memory/psx_sdram_ctrl.sv and one on the transformed controller,
each with its own chip model; the traffic waits on the controller, so it
stays identical only while the controllers do. Every clk3x edge compares
the SDRAM pins (command, A, BA, DQM, driven DQ), every channel output and
need_ref, and checks ref_owed_nz == (ref_owed != 0). The build's setting
(CL3, SPU_FIRST, OPEN_ROW) runs at the normal refresh interval and with
REFRESH_CYCLES 40, where refreshes fall due under load and ref_owed climbs
to its cap of 8, and with REFRESH_CYCLES 12 for a fixed 2 ms window (the
traffic cannot finish), where owed refreshes often issue on the edge the
interval timer runs out. Planted faults in the new register must be caught
(in the REFRESH_CYCLES 12 window).
Usage: run_sdram_ref_owed.py [--ops N]
"""
import argparse
from pathlib import Path
import re
import shutil
import subprocess

from prepare_sdram_ref_owed import ref_owed_text
from run_sdram_ctrl import ALTDDIO
from tool_paths import ROOT, find_tool

CTRL = ROOT/'rtl/memory/psx_sdram_ctrl.sv'
BENCH = ROOT/'sim/memory/tb_psx_sdram_ctrl.sv'
SIGNALS = ('SDRAM_A', 'SDRAM_BA', 'SDRAM_nRAS', 'SDRAM_nCAS', 'SDRAM_nWE', 'SDRAM_CKE', 'SDRAM_DQML', 'SDRAM_DQMH',
           'dq_oe', 'need_ref', 'ram_idle', 'ch1_ready', 'ch1_dout32', 'ch2_ready', 'ch3_ready', 'ch3_dout',
           'dma_wr', 'dma_data', 'dma_reqprocessed', 'cache_wr', 'cache_data', 'cache_addr', 'dmafifo_read',
           'ref_owed')
WINDOW_US = 2000   # REFRESH_CYCLES 12: a 2 ms window, owed refreshes as the timer runs out
FAULTS = {   # (old, new): must make the lockstep fail
    'interval-not-set': ("ref_owed <= ref_owed + 1'b1; ref_owed_nz <= 1'b1; end", "ref_owed <= ref_owed + 1'b1; end"),
    'owed-last-clear': ("ref_owed_nz <= ref_owed != 4'd1 || (ref_timer == 0 && ref_owed != 4'd8);",
                        "ref_owed_nz <= ref_owed != 4'd1;"),
    'forced-keep': ("ref_owed_nz <= ref_owed != 4'd1 || ref_timer == 0; end", "end"),
}

TOP = """
module tb_sdram_ref_owed #(parameter integer OPS = 4000, parameter integer SEED = 1, parameter integer RC = 780,
                          parameter integer WINDOW_US = 0);
    reg clk3 = 0, clk1 = 0;
    always #4.921 clk3 = ~clk3;
    always #14.763 clk1 = ~clk1;
    wire done_a, done_b; integer err_a, err_b;
    integer mism = 0, cycles = 0, max_owed = 0, owed_refs = 0, due_refs = 0;
    sdram_bench_orig #(.PREFIX("orig"), .OPS(OPS), .SEED(SEED), .CAS(3), .SPU_FIRST(1), .OPEN_ROW(1), .RC(RC), .TAC(5.5)) a (.clk3(clk3), .clk1(clk1), .done(done_a), .errors(err_a));
    sdram_bench #(.PREFIX("new"), .OPS(OPS), .SEED(SEED), .CAS(3), .SPU_FIRST(1), .OPEN_ROW(1), .RC(RC), .TAC(5.5)) b (.clk3(clk3), .clk1(clk1), .done(done_b), .errors(err_b));
    always @(negedge clk3) begin
        cycles = cycles + 1;
        if (a.new_dut.dut.ref_owed > max_owed) max_owed = a.new_dut.dut.ref_owed;
        if (b.new_dut.dut.ref_owed_nz !== (b.new_dut.dut.ref_owed != 0)) begin
            mism = mism + 1;
            if (mism < 5) $display("MISMATCH %0t ref_owed_nz %b ref_owed %0d", $time, b.new_dut.dut.ref_owed_nz, b.new_dut.dut.ref_owed);
        end
%CHECKS%    end
    // REFRESH commands issued while more than one was owed, and owed refreshes
    // issued on the edge the interval timer runs out (the count stays put)
    always @(posedge clk3) begin
        if (a.new_dut.dut.cmd == 3'b001 && a.new_dut.dut.ref_owed > 1) owed_refs = owed_refs + 1;
        if (a.new_dut.dut.ready && !a.new_dut.dut.busy && a.new_dut.dut.need_ref && a.new_dut.dut.ref_timer == 0) due_refs = due_refs + 1;
    end
    initial begin
        wait (done_a && done_b);
        report(err_a, err_b);
    end
    task report(input integer ea, eb);
        begin
            $display("LOCKSTEP cycles %0d mismatches %0d errors %0d %0d max_owed %0d refreshes %0d %0d owed_refreshes %0d due_edges %0d",
                     cycles, mism, ea, eb, max_owed, a.chip.refreshes, b.chip.refreshes, owed_refs, due_refs);
            $finish;
        end
    endtask
    // WINDOW_US: a fixed window (refresh so frequent that the traffic cannot finish)
    initial if (WINDOW_US > 0) begin #(WINDOW_US * 1000.0); report(a.chip.errors, b.chip.errors); end   // delays in ns
    initial begin #400000000; $display("LOCKSTEP timeout mismatches %0d", mism); $finish; end
endmodule
"""


def bench_text():
    text = BENCH.read_text()
    text = text[:text.index('module tb_psx_sdram_ctrl')]
    old = 'parameter integer OPEN_ROW = 0,\n'
    if text.count(old) != 1: raise ValueError('sdram_bench parameters changed')
    text = text.replace(old, 'parameter integer OPEN_ROW = 0, parameter integer RC = 780,\n')
    old = '        psx_sdram_ctrl #(.CAS_LATENCY(CAS), .SPU_FIRST(SPU_FIRST), .OPEN_ROW(OPEN_ROW)) dut ('
    if text.count(old) != 1: raise ValueError('sdram_bench DUT changed')
    text = text.replace(old, '        psx_sdram_ctrl #(.CAS_LATENCY(CAS), .SPU_FIRST(SPU_FIRST), .OPEN_ROW(OPEN_ROW), .REFRESH_CYCLES(RC)) dut (')
    orig = re.sub(r'\bsdram_bench\b', 'sdram_bench_orig', text).replace('psx_sdram_ctrl #(', 'psx_sdram_ctrl_orig #(')
    checks = ''.join(f'        if (a.new_dut.dut.{s} !== b.new_dut.dut.{s}) begin mism = mism + 1; '
                     f'if (mism < 5) $display("MISMATCH %0t {s} %h %h", $time, a.new_dut.dut.{s}, b.new_dut.dut.{s}); end\n'
                     for s in SIGNALS)
    return text + orig + TOP.replace('%CHECKS%', checks)


def run(work, new_text, ops, seed, rc, window_us=0):
    iverilog, vvp = find_tool('iverilog'), find_tool('vvp')
    shutil.rmtree(work, ignore_errors=True); work.mkdir(parents=True)
    (work/'altddio_out.sv').write_text(ALTDDIO)
    (work/'orig.sv').write_text(re.sub(r'\bmodule psx_sdram_ctrl\b', 'module psx_sdram_ctrl_orig', CTRL.read_text()))
    (work/'new.sv').write_text(new_text)
    (work/'tb.sv').write_text(bench_text())
    subprocess.run([iverilog, '-g2012', '-o', str(work/'tb.vvp'), '-s', 'tb_sdram_ref_owed',
                    f'-Ptb_sdram_ref_owed.OPS={ops}', f'-Ptb_sdram_ref_owed.SEED={seed}', f'-Ptb_sdram_ref_owed.RC={rc}', f'-Ptb_sdram_ref_owed.WINDOW_US={window_us}',
                    *map(str, (work/'altddio_out.sv', work/'orig.sv', work/'new.sv', ROOT/'sim/memory/sdram_chip_model.sv', work/'tb.sv'))],
                   check=True, capture_output=True, text=True)
    out = subprocess.run([vvp, '-n', str(work/'tb.vvp')], cwd=work, capture_output=True, text=True, timeout=7200).stdout
    (work/'sim.log').write_text(out)
    m = re.search(r'LOCKSTEP cycles (\d+) mismatches (\d+) errors (\d+) (\d+) max_owed (\d+) refreshes (\d+) (\d+) owed_refreshes (\d+) due_edges (\d+)', out)
    if not m: return False, out[-600:]
    c, mism, ea, eb, owed, ra, rb, orf, due = map(int, m.groups())
    ok = mism == 0 and ea == 0 and eb == 0 and ra == rb
    return ok, f'cycles {c} mismatches {mism} errors {ea}/{eb} refreshes {ra} max ref_owed {owed}, owed refreshes as the timer runs out {due}'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ops', type=int, default=4000)
    args = ap.parse_args()
    root = ROOT/'build/sim/sdram-ref-owed'
    new = ref_owed_text(CTRL.read_text())
    failures = []
    for name, (old, bad) in FAULTS.items():
        if new.count(old) != 1: raise SystemExit(f'fault anchor changed: {name}')
        ok, tail = run(root/('fault-'+name), new.replace(old, bad), args.ops, 1, 12, WINDOW_US)
        print(f'fault {name}:', 'NOT DETECTED' if ok else 'caught', '|', tail.splitlines()[-1] if tail else '', flush=True)
        if ok: failures.append(name)
    for seed, rc, window in ((1, 780, 0), (2, 780, 0), (3, 40, 0), (4, 40, 0), (5, 120, 0), (6, 12, WINDOW_US), (7, 12, WINDOW_US)):
        ok, tail = run(root/f'seed{seed}-rc{rc}', new, args.ops, seed, rc, window)
        print(f'lockstep seed {seed} REFRESH_CYCLES {rc}:', 'PASS' if ok else 'FAIL', '|', tail, flush=True)
        if not ok: failures.append(f'seed {seed} rc {rc}')
    if failures: raise SystemExit('sdram-ref-owed check failed: ' + ', '.join(failures))
    print('PASS sdram-ref-owed: pins, channel outputs and need_ref identical every clk3x edge; planted faults caught')


if __name__ == '__main__':
    main()
