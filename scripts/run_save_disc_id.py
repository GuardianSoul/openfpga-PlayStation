#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Device bench of the disc identity tap (scripts/prepare_save_disc_id.py), Icarus.

run_save_disc_id.py PROJECT [--output DIR]
  PROJECT: a frozen project whose psx_save_exp1.sv has the MDEC tap (E3m, E3x, E4).
  1. the project's device with the step: two mounts' track tables are written as psx_cd_stream
     writes them (400 words each, word index on trackinfo_addr), with random track words whose
     index has the low bits of words 0 and 1; DISC_ID is read byte by byte at 0x1828-0x182B and
     at the alias 0x1838; the MDEC tap's reads (0x1824/5, 0x182C/D), SS busy (0x1822) and DISC_SET
     (0x1823) are as before; reset_2x and the core reset leave the identity (a core reset does
     not remount).
  2. every planted hardware fault must fail (1).
  3. the repository's device bench (tb_save_exp1: page copies, SPU RAM pages, card accesses) on
     the device before and after the step: both pass with the same summary.
  4. core_top.sv of the project takes the wiring.
Prints PASS/FAIL and writes report.json.
"""
import argparse
import json
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_save_disc_id as step
from tool_paths import find_tool

ROOT = Path(__file__).resolve().parents[1]

TB = r'''
`timescale 1ns/1ps
module tb_save_disc_id;
    reg clk2x = 0, clk1x = 0, clk_74a = 0, reset = 1, core_reset = 0;
    always #5 clk2x = !clk2x;
    always @(posedge clk2x) clk1x <= !clk1x;
    always #7 clk_74a = !clk_74a;
    reg [12:0] addr = 0; reg wr = 0; reg [7:0] wdata = 0; wire [7:0] rdata;
    reg ti_write = 0; reg [8:0] ti_addr = 0; reg [31:0] ti_data = 0;
    wire mdec_tap, spu_hold; wire [8:0] ss_adr;
    psx_save_exp1 dut (.clk2x(clk2x), .reset_2x(reset), CORE_RESET .bus_addr(addr), .bus_write(wr), .bus_dataWrite(wdata), .bus_dataRead(rdata),
        .timer_flags(12'd0), .gpu_ranges(63'd0), .clk1x(clk1x), .spu_hold(spu_hold), .spu_quiet(1'b1), .spu_ss_adr(ss_adr), .spu_ss_rdata(32'd0),
        .spuram_rdata(16'd0), .spuram_done(1'b0), .mdec_tap(mdec_tap), .mdec_q(24'hA5C33C),
        .trackinfo_write(ti_write), .trackinfo_addr(ti_addr), .trackinfo_data(ti_data[19:0]),
        .aux_dout(32'd0), .aux_done(1'b0), .up_req(1'b0), .up_we(1'b0), .up_addr(21'd0), .up_din(32'd0),
        .clk_74a(clk_74a), .savestate_start(1'b0), .savestate_load(1'b0), .disc(3'd0), .disc_settled(1'b1), .disc_wrong(1'b0),
        .core_held(1'b0), .cd_state(64'h1122334455667788));
    integer errors = 0, checks = 0, i, seed;
    task bus_write(input [12:0] a, input [7:0] d); begin
        @(posedge clk1x); #1 addr = a; wdata = d; wr = 1; @(posedge clk1x); #1 wr = 0; @(posedge clk1x);
    end endtask
    task bus_read(input [12:0] a, output [7:0] d); begin
        @(posedge clk1x); #1 addr = a; @(posedge clk1x); @(posedge clk1x); #1 d = rdata;
    end endtask
    task check_eq(input [7:0] got, input [7:0] want, input [255:0] what); begin
        checks = checks + 1;
        if (got !== want) begin errors = errors + 1; if (errors < 10) $display("ERROR %0s: got %02x want %02x", what, got, want); end
    end endtask
    // a mount as psx_cd_stream writes it: the word index for one clock before the write, one clock low between words
    task mount(input [7:0] count, input [19:0] ending); begin
        for (i = 0; i < 400; i = i + 1) begin
            @(posedge clk1x); #1 ti_addr = i;
            ti_data = i == 0 ? {16'd0, count >= 10 ? count + 8'd6 : count, count} : i == 1 ? {12'd0, ending} : $random(seed);
            ti_write = 1;
            @(posedge clk1x); #1 ti_write = 0;
        end
        // data and address move on while write is low: nothing may be taken
        @(posedge clk1x); #1 ti_addr = 9'd1; ti_data = 32'hFFFFFFFF; @(posedge clk1x); #1 ti_addr = 9'd0; @(posedge clk1x);
    end endtask
    task check_id(input [23:0] want, input [255:0] what); reg [7:0] q; begin
        bus_read(13'h1828, q); check_eq(q, want[7:0], what);
        bus_read(13'h1829, q); check_eq(q, want[15:8], what);
        bus_read(13'h182A, q); check_eq(q, want[23:16], what);
        bus_read(13'h182B, q); check_eq(q, 8'd0, what);
        bus_read(13'h1838, q); check_eq(q, want[7:0], "alias 0x1838");
    end endtask
    reg [7:0] q;
    initial begin
        seed = 20261112;
        repeat (8) @(posedge clk1x); reset = 0; repeat (4) @(posedge clk1x);
        check_id(24'd0, "identity after configuration");
        mount(8'd1, 20'd233507);
        check_id({4'd1, 20'd233507}, "identity after the first mount");
        bus_read(13'h1822, q); check_eq(q, 8'd0, "SS busy read");
        bus_read(13'h1823, q); check_eq(q, 8'd1, "DISC_SET read");
        bus_read(13'h1824, q); check_eq(q, 8'h3C, "MDEC tap quant");
        bus_read(13'h182C, q); check_eq(q, 8'h3C, "MDEC tap quant alias 0x182C");
        bus_read(13'h182D, q); check_eq(q, 8'hC3, "MDEC tap scale alias 0x182D");
        bus_read(13'h1818, q); check_eq(q, 8'h88, "CD state byte 0");
        bus_read(13'h181F, q); check_eq(q, 8'h11, "CD state byte 7");
        mount(8'd27, 20'hFEDCB);
        check_id({4'd11, 20'hFEDCB}, "identity after the second mount");
        CORE_EDGE
        reset = 1; repeat (4) @(posedge clk1x); reset = 0; repeat (4) @(posedge clk1x);
        check_id({4'd11, 20'hFEDCB}, "identity after reset_2x (no remount)");
        if (errors == 0) $display("PASS save disc id device checks=%0d", checks);
        else $display("FAIL save disc id device errors=%0d checks=%0d", errors, checks);
        $finish;
    end
endmodule
'''


def project_file(project, name):
    hits = [p for p in step.FILES if p[1] == name]
    return step.compiled(project, name, hits[0][2])


def device(text, out, name):
    d = out / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    tb = TB
    if 'core_reset' in text:
        tb = tb.replace('CORE_RESET', '.core_reset(core_reset),').replace(
            'CORE_EDGE', 'core_reset = 1; repeat (4) @(posedge clk1x); core_reset = 0; repeat (4) @(posedge clk1x);\n'
            '        check_id({4\'d11, 20\'hFEDCB}, "identity after the core reset (no remount)");')
    else:
        tb = tb.replace('CORE_RESET', '').replace('CORE_EDGE', '')
    (d / 'psx_save_exp1.sv').write_text(text); (d / 'tb.sv').write_text(tb)
    models = ROOT / 'sim/reference/mister_memory_models.sv'
    subprocess.run([find_tool('iverilog'), '-g2012', '-s', 'tb_save_disc_id', '-o', str(d / 'tb.vvp'), str(d / 'tb.sv'), str(models), str(d / 'psx_save_exp1.sv')], check=True)
    r = subprocess.run([find_tool('vvp'), '-n', 'tb.vvp'], cwd=d, capture_output=True, text=True, timeout=600)
    (d / 'run.log').write_text(r.stdout + r.stderr)
    line = (re.search(r'(PASS|FAIL) save disc id device.*', r.stdout) or ['no result: ' + (r.stdout + r.stderr)[-200:]])[0]
    return 'PASS save disc id device' in r.stdout, dict(result=line, first_error=(re.search(r'ERROR[^\n]*', r.stdout) or [''])[0])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('project', type=Path)
    ap.add_argument('--output', type=Path, default=ROOT / 'build/sim/save-disc-id')
    args = ap.parse_args()
    project = args.project.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    old = project_file(project, 'psx_save_exp1.sv').read_text()
    new = step.exp1(old)
    report = {'project': str(project), 'device_before_sha256': step.sha(old), 'device_after_sha256': step.sha(new)}
    ok, report['device'] = device(new, args.output, 'device')
    print('device:', report['device'])
    report['faults'] = {}
    for fault in step.HW_FAULTS:
        passed, info = device(step.hw_fault(new, fault), args.output, 'fault-' + fault)
        report['faults'][fault] = dict(caught=not passed, **info)
        print(f"fault {fault}: {'caught' if not passed else 'NOT caught'}: {info['first_error'] or info['result']}")
        ok = ok and not passed
    # the repository's device bench on the device before and after
    import run_save_sp_far_edge as far_edge
    from build_save_fw import rom40_from_byte_hex
    far_edge.OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(0x5A7E)
    (far_edge.OUT / 'tb_save_exp1_rom.hex').write_text(''.join(f'{rng.randrange(256):02x}\n' for _ in range(4096)))
    (far_edge.OUT / 'tb_save_exp1_rom2.hex').write_text(''.join(f'{rng.randrange(256):02x}\n' for _ in range(1024)))
    (far_edge.OUT / 'tb_save_exp1_rom40.hex').write_text(rom40_from_byte_hex(far_edge.OUT / 'tb_save_exp1_rom.hex', far_edge.OUT / 'tb_save_exp1_rom2.hex'))
    for label, text in (('before', old), ('after', new)):
        log = far_edge.run(f'disc-id-{label}', text, False)
        report[f'tb_save_exp1_{label}'] = (re.search(r'PASS save exp1.*', log) or ['FAIL'])[0] + ' | ' + (re.search(r'aux accesses.*', log) or [''])[0]
        print(f'tb_save_exp1 {label}:', report[f'tb_save_exp1_{label}'])
        ok = ok and 'PASS save exp1' in log
    ok = ok and report['tb_save_exp1_before'] == report['tb_save_exp1_after']
    core = step.core_top(project_file(project, 'core_top.sv').read_text())
    report['core_top_wired'] = core.count(step.TAG) == 1
    ok = ok and report['core_top_wired']
    report['passed'] = ok
    (args.output / 'report.json').write_text(json.dumps(report, indent=1) + '\n')
    print('PASS save disc id' if ok else 'FAIL save disc id')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
