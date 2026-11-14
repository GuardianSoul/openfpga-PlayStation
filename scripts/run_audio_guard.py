#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Bench for psx_audio_guard (#21): sim/pocket/tb_audio_guard.sv plus planted mutations.

Each mutation must make the bench fail with a FATAL line. Run on the Laptop (Icarus).
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_pocket_audio_coeffs import generate
from tool_paths import ROOT, find_tool

GUARD = ROOT / 'rtl/audio/psx_audio_guard.sv'
BENCH = ROOT / 'sim/pocket/tb_audio_guard.sv'
BRIDGE = [ROOT / 'rtl/audio' / n for n in ('psx_audio_sinc.sv', 'psx_audio_rate.sv', 'psx_audio_bridge.sv', 'psx_audio_i2s.sv')]

MUTATIONS = {
    # the lock latch never sets (neither stable lock nor timeout releases the bridge)
    'latch_never_sets': [("if (!lock_dropped && lock_sync) clock_good <= 1'b1;", "if (!lock_dropped && lock_sync) clock_good <= 1'b0;"),
                         ("begin clock_good <= 1'b1; timed_out <= 1'b1; end", "begin clock_good <= 1'b0; timed_out <= 1'b1; end")],
    # the recovery pulse removed: a fatal fault mutes until the next game start
    'no_recovery_pulse': [("ticks <= 4'd0; recovering <= 1'b1; recovered <= 1'b1;", "ticks <= 4'd0; recovered <= 1'b1;")],
    # the old policy: reset follows the synchronized lock (not sticky)
    'lock_not_sticky': [("reset_async <= restart || !clock_good || recovering;", "reset_async <= restart || !lock_sync || recovering;")],
    # no timeout: a lock flag that never rises keeps the bridge silent
    'no_timeout': [("else if (ticks == TIMEOUT_TICKS - 1) begin clock_good <= 1'b1;", "else if (1'b0) begin clock_good <= 1'b1;")],
    # recovery never ends: reset stays high after the first fault
    'recovery_stuck': [("                recovering <= 1'b0;\n", "")],
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
    parent = ROOT / 'build/sim/audio-guard'
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='run-', dir=parent))
    coef = generate(work / 'coefficients')['compact_path']

    def run(name, guard):
        binary = work / f'{name}.vvp'
        subprocess.run([iverilog, '-g2012', '-s', 'tb_audio_guard', '-o', str(binary), f'-DCOEFFICIENT_FILE="{coef}"',
                        *map(str, BRIDGE), str(guard), str(BENCH)], check=True, timeout=120)
        r = subprocess.run([vvp, '-n', str(binary)], capture_output=True, text=True, timeout=900)
        out = r.stdout + r.stderr
        (work / f'{name}.log').write_text(out)
        return r.returncode, out

    code, out = run('base', GUARD)
    passed = code == 0 and 'PASS audio guard' in out
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
