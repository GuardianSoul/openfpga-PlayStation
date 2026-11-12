#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check prepare_save_sp_far_edge.py on the real save device.

sim/pocket/tb_save_exp1.sv instantiates rtl/pocket/psx_save_exp1.sv and
drives its SPU RAM page commands: both directions, every halfword through
the device's own sp_tgl / sp_done1 handshake, an SPU RAM port model with a
random latency of 1..12 clk1x cycles and done pulses of the SPU's own
accesses with random data in between. It compares the pages word by word.

Runs, all under Icarus:
  control    the repository device, the bench as it is
  far-edge   the step's output, the bench plus a checker: at every rising
             clk2x edge the far-edge copy must equal sp_q1 as that edge sees
             it (what the old readers took). The checker counts the edges
             and the sp_q1 changes it saw.
  faults     prepare_save_sp_far_edge.FAULTS, each must be rejected;
             copy_rising is also run without the checker to show whether
             the page comparison alone would have caught it.
With PROJECT: the same transform on the project's pinned copy must change
exactly the same three places, and the SPU RAM engine lines of both files
are compared.
Usage: run_save_sp_far_edge.py [PROJECT]
"""
import difflib
import json
import random
import re
import subprocess
import sys
from pathlib import Path

from prepare_save_sp_far_edge import FAULTS, fault_text, sha, transform
from tool_paths import ROOT, find_tool

OUT = ROOT / 'build/sim/save-sp-far-edge'
RTL = ROOT / 'rtl/pocket/psx_save_exp1.sv'
TB = ROOT / 'sim/pocket/tb_save_exp1.sv'
MODELS = ROOT / 'sim/reference/mister_memory_models.sv'
CHECKER = '''
    // run_save_sp_far_edge.py: the far-edge copy against what the old readers took
    integer far_edges = 0, far_changes = 0; reg [15:0] far_prev = 0;
    always @(posedge clk2x) begin
        far_edges = far_edges + 1;
        if (dut.sp_q1 !== far_prev) far_changes = far_changes + 1;
        far_prev = dut.sp_q1;
        if (dut.sp_qf !== dut.sp_q1) fail("far-edge copy differs from sp_q1 at a rising clk2x edge");
    end
    final $display("FAR edges=%0d sp_q1_changes=%0d", far_edges, far_changes);
endmodule
'''


def run(name, rtl_text, checker):
    rtl = OUT / f'{name}.sv'; rtl.write_text(rtl_text)
    tb = TB.read_text()
    if checker:
        at = tb.rindex('endmodule')
        tb = tb[:at] + CHECKER.lstrip('\n') + tb[at + len('endmodule'):]
    bench = OUT / f'{name}-tb.sv'; bench.write_text(tb)
    vvp = OUT / f'{name}.vvp'
    subprocess.run([find_tool('iverilog'), '-g2012', '-s', 'tb_save_exp1', '-o', str(vvp), str(bench), str(MODELS), str(rtl)], check=True)
    r = subprocess.run(['nice', '-n', '10', find_tool('vvp'), '-n', vvp.name], cwd=OUT, capture_output=True, text=True, timeout=1800)
    (OUT / f'{name}.log').write_text(r.stdout + r.stderr)
    return r.stdout


def engine(text):
    """The halfword hand-over lines both files must share (every line naming sp_q1, sp_lo, sp_hi,
    the toggle/done chain or sp_store, other than the page write multiplexer)."""
    return [l.strip() for l in text.splitlines() if re.search(r'\bsp_(q1|qf|lo|hi|tgl|seen1|pend|done[123]?|store)\b', l)
            and 'cp_wd' not in l and 'cp_we' not in l and 'cp_busy <= 1' not in l]


def hunks(old, new):
    return [(l[0], l[1:].strip()) for l in difflib.unified_diff(old.splitlines(), new.splitlines(), n=0, lineterm='')
            if l[:1] in '+-' and l[:3] not in ('+++', '---')]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(0x5A7E)
    (OUT / 'tb_save_exp1_rom.hex').write_text(''.join(f'{rng.randrange(256):02x}\n' for _ in range(4096)))
    (OUT / 'tb_save_exp1_rom2.hex').write_text(''.join(f'{rng.randrange(256):02x}\n' for _ in range(1024)))
    from build_save_fw import rom40_from_byte_hex
    (OUT / 'tb_save_exp1_rom40.hex').write_text(rom40_from_byte_hex(OUT / 'tb_save_exp1_rom.hex', OUT / 'tb_save_exp1_rom2.hex'))
    old = RTL.read_text(); new = transform(old)
    print(f'device sha256 {sha(old)[:16]} -> {sha(new)[:16]}', flush=True)
    failures = []

    out = run('control', old, False)
    ok = 'PASS save exp1' in out
    print('control:', 'PASS' if ok else 'FAIL', (re.search(r'aux accesses.*', out) or [''])[0], flush=True)
    if not ok: failures.append('control')
    out = run('far-edge', new, True)
    far = re.search(r'FAR edges=(\d+) sp_q1_changes=(\d+)', out)
    ok = 'PASS save exp1' in out and 'ERROR' not in out and bool(far) and int(far[2]) > 1000
    print('far-edge:', 'PASS' if ok else 'FAIL', far[0] if far else out[-400:], (re.search(r'aux accesses.*', out) or [''])[0], flush=True)
    if not ok: failures.append('far-edge')
    for name in FAULTS:
        out = run('fault-' + name, fault_text(new, name), True)
        first = (re.search(r'ERROR[^\n]*', out) or ['no error'])[0]
        caught = 'PASS save exp1' not in out and 'ERROR' in out
        print(f'fault {name}:', 'caught' if caught else 'NOT DETECTED', '|', first[:110], flush=True)
        if not caught: failures.append('fault ' + name)
    out = run('fault-copy_rising-plain', fault_text(new, 'copy_rising'), False)
    print('fault copy_rising without the checker:', 'page comparison alone passes (the checker is what catches it)'
          if 'PASS save exp1' in out else 'page comparison catches it too', flush=True)

    if len(sys.argv) > 1:
        project = Path(sys.argv[1]).resolve()
        rows = [Path(r['path']) for r in json.loads((project / 'fit-inputs.json').read_text())['quartus_sources']
                if Path(r['path']).name == 'psx_save_exp1.sv']
        assert len(rows) == 1
        pinned = rows[0].read_text(); pinned_new = transform(pinned)
        a, b = hunks(old, new), hunks(pinned, pinned_new)
        same_adds = [x for x in a if x[0] == '+' and 'cp_wd' not in x[1]] == [x for x in b if x[0] == '+' and 'cp_wd' not in x[1]]
        same_engine = engine(old) == engine(pinned)
        print(f'pinned copy {rows[0]} sha256 {sha(pinned)[:16]} -> {sha(pinned_new)[:16]}: '
              f'{len(b)} changed lines ({len(a)} in the repository file), same added lines: {same_adds}, '
              f'same SPU RAM engine lines ({len(engine(old))}): {same_engine}', flush=True)
        if not (same_adds and same_engine and len(a) == len(b)): failures.append('pinned copy differs')
    if failures:
        raise SystemExit('save sp far-edge check failed: ' + ', '.join(failures))
    print(f'PASS save sp far-edge: control, candidate with per-edge checker, {len(FAULTS)} planted faults rejected')


if __name__ == '__main__':
    main()
