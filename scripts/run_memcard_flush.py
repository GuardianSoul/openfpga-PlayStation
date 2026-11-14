#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Bench for psx_memcard_flush (#18): sim/pocket/tb_memcard_flush.sv plus planted mutations.

Each mutation must make the bench fail with a FATAL line. Run on the Laptop (Icarus).
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tool_paths import ROOT, find_tool

GUARD = ROOT / 'rtl/pocket/psx_memcard_flush.sv'
BENCH = ROOT / 'sim/pocket/tb_memcard_flush.sv'
BRIDGE = []

MUTATIONS = {
    # flush at once after a write (no quiet time)
    'no_quiet': [("if (dirty && !wevent && &quiet && en_s[1]", "if (dirty && !wevent && en_s[1]")],
    # a cue command during the flush is dropped
    'drop_cue': [("if (!idle && c_pulse) begin", "if (1'b0) begin")],
    # the cue side sees the flush's done as its own
    'no_mask': [("assign c_done = idle ? t_done : 1'b0;", "assign c_done = t_done;")],
    # a save state does not hold the flush back
    'ignore_hold': [("en_s[1] && !hold_s[1] && !c_busy", "en_s[1] && !c_busy")],
    # the flush starts while a cue command is in flight
    'ignore_busy': [("!hold_s[1] && !c_busy && !c_pulse", "!hold_s[1] && !c_pulse")],
    # dirty is not cleared when the flush starts (flushes repeat)
    'dirty_kept': [("dirty <= 1'b0; state <= S_ISSUE;", "state <= S_ISSUE;")],
    # a reload keeps an old dirty flag
    'no_reload_clear': [("        else if (!en_s[1]) dirty <= 1'b0;\n", "")],
}


def mutate(text, pairs):
    for old, new in pairs:
        if text.count(old) != 1:
            raise SystemExit(f'mutation anchor: {old}')
        text = text.replace(old, new)
    return text


def main():
    iverilog, vvp = find_tool('iverilog'), find_tool('vvp')
    if not iverilog or not vvp:
        raise SystemExit('Icarus required')
    parent = ROOT / 'build/sim/memcard-flush'
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='run-', dir=parent))

    def run(name, guard):
        binary = work / f'{name}.vvp'
        subprocess.run([iverilog, '-g2012', '-s', 'tb_memcard_flush', '-o', str(binary),
                        *map(str, BRIDGE), str(guard), str(BENCH)], check=True, timeout=120)
        r = subprocess.run([vvp, '-n', str(binary)], capture_output=True, text=True, timeout=900)
        out = r.stdout + r.stderr
        (work / f'{name}.log').write_text(out)
        return r.returncode, out

    code, out = run('base', GUARD)
    passed = code == 0 and 'PASS memcard flush' in out
    print('base:', 'PASS' if passed else 'FAIL', out.strip().splitlines()[-2] if out.strip() else '')
    results = {}
    for name, pairs in MUTATIONS.items():
        path = work / f'{name}.sv'
        path.write_text(mutate(GUARD.read_text(), pairs))
        mcode, mout = run(name, path)
        caught = mcode != 0 and 'FATAL' in mout
        line = next((l for l in mout.splitlines() if 'FATAL' in l), mout.strip().splitlines()[-1:] or [''])
        results[name] = {'caught': caught, 'line': line if isinstance(line, str) else line[0]}
        print(f'{name}: {"caught" if caught else "NOT CAUGHT"} ({results[name]["line"].strip()})')
    report = {'passed': passed and all(r['caught'] for r in results.values()), 'base': passed, 'mutations': results}
    (work / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(work)
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
