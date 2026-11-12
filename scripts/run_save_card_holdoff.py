#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Check prepare_save_card_holdoff.py in the card chain (Icarus; sim/pocket/tb_card_chain.sv case 9).

The four chained modules as core_top wires them (slot -> save device -> psx_vram_x3 -> psx_vram_psram
-> two chip models), case 1 (card load while parked, read-back) and then case 9: bursts of bridge card
reads and writes at 88..96 clk_74a with pauses while the core reset toggles at random (header of
holdoff_case in the bench). Runs:
  before   the slot as it is (park = !reset_n): must LOSE answers or words (shows the exposure)
  after    the slot with the step, HOLD_BITS 10 in the bench (quiet time 1,023 clk_74a, 13.8 us; the
           build has 16): case 9 must PASS (every answer right, no overflow, nothing dropped, card
           equal to the golden copy, park equal to the hold-off's reference model on every clk_74a
           edge); the residual window (a burst right after a reset change, windows quiet before) is
           reported, not judged
  faults   prepare_save_card_holdoff.FAULTS in the new slot: each must FAIL case 9
SOURCES is a directory with psx_memcard_slot.sv (before the step), psx_save_exp1.sv, psx_vram_x3.sv and
psx_vram_psram.sv as the project compiles them (or a frozen project: the qsf's files and the pinned
save device).
Usage: run_save_card_holdoff.py SOURCES [--bursts N] [--seed S] [--only before,after,faults]
"""
import argparse
import json
import os
import re
from pathlib import Path

import prepare_save_card_holdoff as step
import run_card_chain as chain
from tool_paths import ROOT

BENCH_BITS = 10


def sources(path):
    path = path.resolve()
    if (path / 'apf_top.qsf').exists():
        qsf = (path / 'apf_top.qsf').read_text()
        found = {}
        for name in ('psx_memcard_slot.sv', 'psx_vram_x3.sv', 'psx_vram_psram.sv'):
            m = re.findall(r'SYSTEMVERILOG_FILE "([^"]*/' + re.escape(name) + ')"', qsf)
            assert len(m) == 1, name
            found[name] = Path(m[0])
        found['psx_save_exp1.sv'] = path / 'save-exp1-pinned/psx_save_exp1.sv'
        return found
    return {n: path / n for n in ('psx_memcard_slot.sv', 'psx_save_exp1.sv', 'psx_vram_x3.sv', 'psx_vram_psram.sv')}


def one(name, slot, files, plus, define):
    out = chain.OUT
    save, x3, psram = (out / n for n in ('psx_save_exp1.sv', 'psx_vram_x3.sv', 'psx_vram_psram.sv'))
    if define:
        # the bench's SLOT_HOLDOFF define carries the bench's HOLD_BITS; run_card_chain.run compiles,
        # so pass it through the slot file itself
        text = slot.read_text()
        slot = out / f'{name}-psx_memcard_slot.sv'
        slot.write_text(f'`define SLOT_HOLDOFF {BENCH_BITS}\n' + text)
    r = chain.run(name, save, psram, plus, slot, x3)
    log = (ROOT / r['log']).read_text()
    h = re.search(r'^HOLDOFF (\{.*\})$', log, re.M)
    res = re.search(r'^RESIDUAL (\{.*\})$', log, re.M)
    case9 = re.search(r'^CASE 9 (PASS|FAIL)$', log, re.M)
    case1 = re.search(r'^CASE 1 (PASS|FAIL)', log, re.M)
    return dict(name=name, case1=case1 and case1.group(1), case9=case9 and case9.group(1),
                holdoff=json.loads(h.group(1)) if h else None, residual=json.loads(res.group(1)) if res else None,
                first_errors=[l for l in log.splitlines() if l.startswith('ERROR')][:4], wall_s=r['wall_s'], log=r['log'])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('sources', type=Path)
    ap.add_argument('--bursts', type=int, default=40)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--only', default='before,after,faults')
    a = ap.parse_args()
    only = a.only.split(',')
    os.environ['PATH'] = os.pathsep.join([str(ROOT / 'build/tools/iverilog/bin'), os.environ['PATH']])
    chain.OUT = ROOT / 'build/sim/card-holdoff'
    out = chain.OUT
    out.mkdir(parents=True, exist_ok=True)
    src = sources(a.sources)
    for n, p in src.items():
        if n != 'psx_memcard_slot.sv':
            (out / n).write_text(p.read_text())
    old = src['psx_memcard_slot.sv'].read_text()
    if step.TAG in old:
        new, old = old, step.restore(old)
    else:
        new = step.slot_text(old)
    assert step.restore(new) == old
    (out / 'old-psx_memcard_slot.sv').write_text(old)
    (out / 'new-psx_memcard_slot.sv').write_text(new)
    print(f'slot: old sha256 {step.sha(old)[:16]}, new {step.sha(new)[:16]}; undoing the step gives the old text', flush=True)
    # case 1 with a short card (smoke size), then case 9
    plus = dict(SEED=a.seed, CARD_N=2048, HOLDOFF=a.bursts)
    report, failures = dict(bursts=a.bursts, seed=a.seed, bench_hold_bits=BENCH_BITS, old_sha256=step.sha(old), new_sha256=step.sha(new)), []
    if 'before' in only:
        r = one('before', out / 'old-psx_memcard_slot.sv', src, plus, False)
        h = r['holdoff'] or {}
        lost = h.get('reads_wrong', 0) + h.get('dropped', 0) + h.get('overflow', 0) + h.get('card_words_wrong', 0)
        print(f"before: case 9 {r['case9']} (must lose words) {json.dumps(h)} {r['wall_s']} s", flush=True)
        if r['case1'] != 'PASS' or lost == 0:
            failures.append('before: no loss shown')
        report['before'] = r
    if 'after' in only:
        r = one('after', out / 'new-psx_memcard_slot.sv', src, plus, True)
        print(f"after: case 9 {r['case9']} {json.dumps(r['holdoff'])} {r['wall_s']} s", flush=True)
        print(f"after: residual (not gating) {json.dumps(r['residual'])}", flush=True)
        for e in r['first_errors']:
            print('  ' + e)
        if r['case1'] != 'PASS' or r['case9'] != 'PASS':
            failures.append('after')
        report['after'] = r
    if 'faults' in only:
        report['faults'] = {}
        for name in step.FAULTS:
            (out / f'fault-{name}-src.sv').write_text(step.fault_text(new, name))
            r = one(f'fault-{name}', out / f'fault-{name}-src.sv', src, plus, True)
            caught = r['case9'] != 'PASS'
            print(f"fault {name}: {'caught' if caught else 'NOT DETECTED'} | {(r['first_errors'] or ['no error'])[0][:150]}", flush=True)
            report['faults'][name] = dict(caught=caught, first_error=(r['first_errors'] or [''])[0], holdoff=r['holdoff'])
            if not caught:
                failures.append('fault ' + name)
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    if failures:
        raise SystemExit('card hold-off check failed: ' + ', '.join(failures))
    print(f'PASS card hold-off: {", ".join(only)}' + (f' ({len(step.FAULTS)} planted faults rejected)' if 'faults' in only else ''))


if __name__ == '__main__':
    main()
