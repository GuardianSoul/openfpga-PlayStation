#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Check prepare_save_readout_end.py on a project's save device (psx_save_exp1 half).

The device after save-readout-hold (old) and after save-readout-end (new) side by side under the
stimulus of sim/pocket/tb_save_readout_hold.sv (the bench is derived from it here): every output equal on
every clk2x edge; the read data equal but bits 3:2 at 0x180C; rd_seen as before; rd_last against a
reference (a slot read with address bit 20 whose page + 1 = SIZE; cleared by the SPU_HOLD write, set
wins); bit 3 = cue_busy two clk2x edges late (cue_busy toggles at random on clk_74a). An added sequence
sets SIZE = 4 pages and reads pages 2 and 3: only page 3 sets rd_last. Planted faults
(prepare_save_readout_end.FAULTS 'ex-*') must fail. The psx_cue_mount half is run_cue_mount.py
stream-hold (CUE_MOUNT_SV with the step).
Usage: run_save_readout_end.py PROJECT [--output DIR]   (PROJECT: a frozen project with save-readout-hold)
"""
import argparse
import json
import random
import re
import subprocess
from pathlib import Path

import prepare_save_readout_end as step
from prepare_save_rom_ext import ext_hex
from tool_paths import ROOT, find_tool

TB = ROOT / 'sim/pocket/tb_save_readout_hold.sv'
MODELS = ROOT / 'sim/reference/mister_memory_models.sv'


def _once(t, a, b):
    if t.count(a) != 1: raise SystemExit(f'derive: {t.count(a)} matches of {a[:70]!r}')
    return t.replace(a, b)


def derive(t):
    t = t.replace('tb_save_readout_hold', 'tb_save_readout_end')
    t = _once(t, '    reg [63:0] cd_state = 64\'h0102_0304_C805_0118;\n',
              '    reg [63:0] cd_state = 64\'h0102_0304_C805_0118;\n'
              '    reg cue_busy = 0;   // save-readout-end: the cue reader\'s command in flight (clk_74a)\n'
              '    always @(posedge clk_74a) if ($urandom % 97 == 0) cue_busy <= !cue_busy;\n')
    t = _once(t, '.core_held(core_held), .cd_state(cd_state)); \\', '.core_held(core_held), .cd_state(cd_state), .cue_busy(cue_busy)); \\')
    t = _once(t, "        if (d[0] !== spu_quiet || d[7:2] !== 6'd0) fail(\"0x180C read: bit 0 or bits 7:2\");",
              "        if (d[0] !== spu_quiet || d[7:4] !== 4'd0) fail(\"0x180C read: bit 0 or bits 7:4\");")
    t = _once(t, '    reg seen_ref = 0; reg wr_d = 0;', '    reg seen_ref = 0; reg wr_d = 0; reg last_ref = 0; reg [1:0] bsh = 0; integer last_sets = 0;')
    t = _once(t, '        if (dut.rd_seen !== seen_ref) fail("rd_seen differs from the reference");\n',
              '        if (dut.rd_seen !== seen_ref) fail("rd_seen differs from the reference");\n'
              '        if (dut.rd_last !== last_ref) fail("rd_last differs from the reference");\n')
    t = _once(t, '''            if ({old_q[7:2], old_q[0]} !== {dut_q[7:2], dut_q[0]}) fail("0x180C: a bit other than bit 1 differs");
            if (dut_q[1] !== seen_ref) fail("0x180C bit 1 is not rd_seen");
            if (old_q[1] !== 1'b0) fail("old device: 0x180C bit 1 not 0");''',
              '''            if ({old_q[7:4], old_q[1:0]} !== {dut_q[7:4], dut_q[1:0]}) fail("0x180C: a bit other than bits 3:2 differs");
            if (dut_q[2] !== last_ref) fail("0x180C bit 2 is not rd_last");
            if (dut_q[3] !== bsh[1]) fail("0x180C bit 3 is not cue_busy two clocks late");
            if (old_q[3:2] !== 2'b00) fail("old device: 0x180C bits 3:2 not 0");''')
    t = _once(t, '''            if (bus_write && !wr_d && at_hold) begin if (seen_ref) clears = clears + 1; seen_ref = 0; end
            if (old.up_go && !up_we && up_addr[20]) begin if (!seen_ref) sets = sets + 1; seen_ref = 1; end''',
              '''            if (bus_write && !wr_d && at_hold) begin if (seen_ref) clears = clears + 1; seen_ref = 0; last_ref = 0; end
            if (old.up_go && !up_we && up_addr[20]) begin if (!seen_ref) sets = sets + 1; seen_ref = 1; end
            if (old.up_go && !up_we && up_addr[20] && {1'b0, up_addr[19:9]} + 12'd1 == old.size_pages) begin
                if (!last_ref) last_sets = last_sets + 1; last_ref = 1; end''')
    t = _once(t, '        wr_d = bus_write;\n', '        wr_d = bus_write;\n        bsh = {bsh[0], cue_busy};\n')
    t = _once(t, '        if (sets < 500 || clears < 500) fail("too few sets or clears");\n',
              '''        // save-readout-end: SIZE 4 pages; a read of page 2 does not set rd_last, one of page 3 does
        bus_wr(13'h1818, 8'h04); bus_wr(13'h1819, 8'h00); bus_wr(13'h180C, 8'h01);
        slot(0, {1'b1, 11'd2, 9'd17}); slot(0, {1'b1, 11'd2, 9'd511});
        repeat (4) @(posedge clk2x);
        if (dut.rd_last !== 1'b0) fail("rd_last set by a page that is not the last");
        slot(1, {1'b1, 11'd3, 9'd5});
        repeat (4) @(posedge clk2x);
        if (dut.rd_last !== 1'b0) fail("rd_last set by a write of the last page");
        slot(0, {1'b1, 11'd3, 9'd5});
        repeat (4) @(posedge clk2x);
        if (dut.rd_last !== 1'b1) fail("rd_last not set by a read of the last page");
        slot(0, {1'b0, 11'd3, 9'd5}); bus_wr(13'h180C, 8'h01); repeat (4) @(posedge clk2x);
        if (dut.rd_last !== 1'b0) fail("rd_last set by a card access or not cleared");
        if (last_sets < 1) fail("rd_last never set");
        if (sets < 500 || clears < 500) fail("too few sets or clears");
''')
    t = _once(t, '        $display("READOUT edges=', '        $display("READOUT last_sets=%0d", last_sets);\n        $display("READOUT edges=')
    t = t.replace('PASS save readout hold', 'PASS save readout end').replace('FAIL save readout hold', 'FAIL save readout end')
    return t


def sim(out, name, bench, sources):
    vvp = out / f'{name}.vvp'
    subprocess.run([find_tool('iverilog'), '-g2012', '-s', 'tb_save_readout_end', '-o', str(vvp), str(bench), str(MODELS), *map(str, sources)], check=True)
    r = subprocess.run(['nice', '-n', '10', find_tool('vvp'), '-n', vvp.name], cwd=out, capture_output=True, text=True, timeout=7200)
    (out / f'{name}.log').write_text(r.stdout + r.stderr)
    return r.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('project', type=Path)
    ap.add_argument('--output', type=Path, default=ROOT / 'build/sim/save-readout-end')
    args = ap.parse_args()
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    rows = [Path(r['path']) for r in json.loads((args.project / 'fit-inputs.json').read_text())['quartus_sources']
            if Path(r['path']).name == 'psx_save_exp1.sv']
    text = rows[0].read_text()
    old, new = (step.restore(text, step.EX), text) if step.TAG in text else (text, step.ex_text(text))
    assert step.ex_text(old) == new
    renamed = old.replace('module psx_save_exp1 #(', 'module psx_save_exp1_old #(', 1)
    renamed = _once(renamed, "    output reg         cd_freeze = 1'b0,\n", "    output reg         cd_freeze = 1'b0,\n    input  wire        cue_busy,   // (bench: unused)\n")
    cut = renamed.index('// save-rom-ext: the routine ROM\'s extension, one M10K') if 'module psx_save_rom_ext' in renamed else None
    (out / 'old-renamed.sv').write_text(renamed[:cut] + '`default_nettype wire\n' if cut else renamed)
    (out / 'new.sv').write_text(new)
    (out / 'tb.sv').write_text(derive(TB.read_text()))
    rng = random.Random(0x5A7E)
    rom1 = bytes(rng.randrange(256) for _ in range(4096)); rom2 = bytes(rng.randrange(256) for _ in range(1024))
    ext = bytes(rng.randrange(256) for _ in range(1024))
    (out / 'tb_save_rom_ext.hex').write_text(ext_hex(ext))
    from build_staged_save_fw import rom40_hex
    (out / 'tb_save_exp1_rom40.hex').write_text(rom40_hex(rom1, rom2))
    report, ok = {}, True
    log = sim(out, 'end', out / 'tb.sv', [out / 'old-renamed.sv', out / 'new.sv'])
    good = 'PASS save readout end' in log and 'ERROR' not in log
    report['step'] = dict(lines=re.findall(r'READOUT [^\n]*', log), ok=good); ok &= good
    print('step:', 'PASS' if good else 'FAIL', re.findall(r'READOUT [^\n]*', log), flush=True)
    if not good: print(log[-1500:])
    for name, (kind, _, _) in step.FAULTS.items():
        if kind != 'ex': continue
        (out / f'fault-{name}.sv').write_text(step.fault_text(new, name))
        log = sim(out, f'fault-{name}', out / 'tb.sv', [out / 'old-renamed.sv', out / f'fault-{name}.sv'])
        caught = 'PASS save readout end' not in log
        first = (re.search(r'ERROR[^\n]*', log) or ['no error'])[0]
        report[name] = dict(caught=caught, first=first); ok &= caught
        print(f'fault {name}:', f'caught ({first})' if caught else 'NOT CAUGHT', flush=True)
    (out / 'report.json').write_text(json.dumps(report, indent=1) + '\n')
    print('PASS save-readout-end device' if ok else 'FAIL save-readout-end device')
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
