#!/usr/bin/env python3
"""MiSTer main-port oracle through psx_mister_main_adapter and the paired PSRAM backend.

Reuses run_mister_main_adapter.py's vectors, pinned upstream SDRAM-model
oracle and checking body, with psx_psram_clients (behavioural psram.sv
stand-in, sim/pocket/psram_behavior.sv) in place of the native z-port
backend: byte masks, cache fills, DMA reads and ordering against MiSTer.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

from prepare_system_sim import prepare_memory_models
from prepare_resource_targets import UPSTREAM
from run_mister_main_adapter import vectors, oracle_text
from tool_paths import ROOT, find_tool

PREFIX = '''`timescale 1ns/1ps
module tb_psx_mister_main_adapter;
    parameter real MEM_HALF=4.8;
    parameter integer PAIRED=1;
    reg clk=0, rst=1;
    always #(MEM_HALF) clk=~clk;
    wire initialized;
    wire wait_error=1'b0;
'''

NATIVE_START = '    wire [3:0] p_ready,p_rsp_valid,p_rsp_error;\n'
NATIVE_END = 'assign ar=p_ready[0];assign arv=p_rsp_valid[0];assign are=p_rsp_error[0];assign ard=p_rsp_data[0];\n'
PSRAM = '''    wire fabric_idle;
    wire protocol_error=1'b0;
    wire [2:0] p_ready, p_rsp_valid, p_rsp_error;
    wire [127:0] p_rsp_data;
    wire [15:0] dq0, dq1;
    psx_psram_clients #(.T_INIT(60), .PAIRED(PAIRED)) native (.clk(clk), .reset(rst), .initialized(initialized), .idle(fabric_idle),
        .p_valid({2'b0,av}), .p_write({2'b0,aw}), .p_ready(p_ready), .p_addr({50'b0,aa}),
        .p_halfwords({8'b0,an}), .p_wdata({256'b0,ad}), .p_byte_en({32'b0,ab}),
        .r_valid(p_rsp_valid), .r_error(p_rsp_error), .r_ready({2'b0,arr}), .r_rdata(p_rsp_data),
        .fill_valid(), .fill_word(), .fill_data(),
        .cram0_a(), .cram1_a(), .cram0_dq(dq0), .cram1_dq(dq1), .cram0_wait(1'b0), .cram1_wait(1'b0),
        .cram0_adv_n(), .cram1_adv_n(), .cram0_cre(), .cram1_cre(), .cram0_ce0_n(), .cram0_ce1_n(),
        .cram1_ce0_n(), .cram1_ce1_n(), .cram0_oe_n(), .cram1_oe_n(), .cram0_we_n(), .cram1_we_n(),
        .cram0_ub_n(), .cram0_lb_n(), .cram1_ub_n(), .cram1_lb_n());
    assign ar=p_ready[0];assign arv=p_rsp_valid[0];assign are=p_rsp_error[0];assign ard=p_rsp_data;
    // The body waits for a chip read to start (z-port names of the native bench).
    wire valid=native.pair.burst_en || native.pair.write_en;
    wire write=native.pair.write_en;
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--single', action='store_true', help='PAIRED=0 (chip 0 alone)')
    parser.add_argument('--sync', action='store_true', help='psx_mister_main_sync with the PSRAM clients on clk3x (--psram-sync)')
    args = parser.parse_args()
    parent = ROOT/'build/sim/psram-main-adapter'; parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='checks-', dir=parent)); print(work, flush=True)
    rows, gold = vectors(work)
    model = prepare_memory_models(work/'upstream')[0]
    vhdl = work/'tb_oracle.vhd'; vhdl.write_text(oracle_text()); ghdl = find_tool('ghdl')
    with (work/'oracle.log').open('w') as log:
        for command in ([ghdl, '-a', '--std=08', '--work=tb', str(UPSTREAM/'sim/system/src/tb/globals.vhd'), model['path']],
                        [ghdl, '-a', '--std=08', str(vhdl)], [ghdl, '-e', '--std=08', 'tb_mister_oracle'],
                        [ghdl, '-r', '--std=08', 'tb_mister_oracle', '--max-stack-alloc=0', '--assert-level=error']):
            subprocess.run(command, cwd=work, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
    actual = sorted(((int(f[0]), f[1], *[int(x, 16) for x in f[2:]]) for f in
                     (row.split() for row in (work/'oracle.txt').read_text().splitlines())), key=lambda e: (e[0], e[1]))
    if actual != gold: raise SystemExit('Upstream oracle differs from the generated gold events')
    (work/'expected.txt').write_text(''.join(f'{i:x} {e} {m:x} {a:x} {d:x}\n' for i, e, m, a, d in actual))
    body = (ROOT/'sim/memory/tb_psx_mister_main_adapter_body.sv').read_text()
    start = body.index(NATIVE_START); end = body.index(NATIVE_END) + len(NATIVE_END)
    text = PREFIX + body[:start] + PSRAM + body[end:]
    if args.sync:
        for o, n in (('psx_mister_main_adapter adapter (', 'psx_mister_main_sync adapter ('),
                     ('psx_psram_clients #(.T_INIT(60), .PAIRED(PAIRED)) native (.clk(clk),',
                      'psx_psram_clients #(.T_INIT(60), .PAIRED(PAIRED)) native (.clk(clk3x),')):
            if text.count(o) != 1: raise SystemExit('--sync anchor not found: ' + o)
            text = text.replace(o, n)
    bench = work/'tb.sv'; bench.write_text(text)
    sources = [ROOT/'sim/pocket/psram_behavior.sv', ROOT/'rtl/pocket/psx_psram_pair.sv', ROOT/'rtl/pocket/psx_psram_clients.sv',
               ROOT/'rtl/memory/psx_mem_cdc.sv', ROOT/('rtl/pocket/psx_mister_main_sync.sv' if args.sync else 'rtl/pocket/psx_mister_main_adapter.sv'), bench]
    results = []
    for name, params in (('104MHz', []), ('62MHz', [('MEM_HALF', 8.0)]), ('psx-clock', [('CORE_HALF', 14.763)])):
        binary = work/f'{name}.vvp'
        params = params + ([('PAIRED', 0)] if args.single else [])
        subprocess.run([find_tool('iverilog'), '-g2012', '-s', 'tb_psx_mister_main_adapter',
                        *[f'-Ptb_psx_mister_main_adapter.{k}={v}' for k, v in params], '-o', str(binary), *map(str, sources)],
                       cwd=work, check=True, timeout=120)
        r = subprocess.run([find_tool('vvp'), str(binary)], cwd=work, capture_output=True, text=True, timeout=600)
        out = r.stdout + r.stderr; (work/f'{name}.log').write_text(out)
        ok = r.returncode == 0 and 'FATAL' not in out and out.count('PASS MISTER MAIN ADAPTER') == 1
        results.append(ok); print(name, 'PASS' if ok else 'FAIL'); print(out[-1500:])
    if not all(results): raise SystemExit(1)


if __name__ == '__main__':
    main()
