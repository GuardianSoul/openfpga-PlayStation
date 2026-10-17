#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Test rtl/pocket/psx_memcard_slot.sv (clean-room memory card slot).

Runs sim/pocket/tb_memcard_slot.sv under Icarus: APF loads of a known image
in both bridge byte orders, guest writes, full 131,072-byte readback at the
APF bridge's fastest documented rate (one transaction per 88 clk_74a), the
four format cases plus two cards with the magic, reset in the middle of a
load and of a format, slow aux back-pressure, an overflow self-check, a
latency sweep that measures the largest aux latency the read and write paths
tolerate, and six negative controls (broken variants that must be rejected
for the expected reason). Prints one JSON summary (also written to
build/sim/memcard-slot/summary.json) with the boot, format and read timings.
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from tool_paths import ROOT, find_tool

SOURCES = ['rtl/pocket/psx_memcard_slot.sv', 'rtl/pocket/psx_memcard_blank.sv', 'sim/pocket/tb_memcard_slot.sv']

# name, build (LITTLE, FAULT), plusargs
POSITIVE = [
    ('load-real-le', (1, 0), dict(SCEN='load', IMAGE='real', SEED=1, PHASE=0)),
    ('load-real-be', (0, 0), dict(SCEN='load', IMAGE='real', SEED=2, PHASE=3100)),
    ('load-mc-zero', (1, 0), dict(SCEN='load', IMAGE='mc_zero', SEED=3, PHASE=7000)),
    ('format-nofile', (1, 0), dict(SCEN='load', IMAGE='nofile', SEED=4, PHASE=1200)),
    ('format-zeros', (1, 0), dict(SCEN='load', IMAGE='zeros', SEED=5, PHASE=9000)),
    ('format-ff', (1, 0), dict(SCEN='load', IMAGE='ff', SEED=6, PHASE=11000)),
    ('format-junk', (1, 0), dict(SCEN='load', IMAGE='junk', SEED=7, PHASE=13000)),
    ('format-junk-be', (0, 0), dict(SCEN='load', IMAGE='junk', SEED=8, PHASE=500)),
    ('reset-mid-load-long', (1, 0), dict(SCEN='reset_load', IMAGE='real', SEED=9, RST_AT=5000, RST_LEN=400)),
    ('reset-mid-load-short', (1, 0), dict(SCEN='reset_load', IMAGE='junk', SEED=10, RST_AT=100, RST_LEN=1, PHASE=4000)),
    ('reset-mid-format', (1, 0), dict(SCEN='reset_fmt', IMAGE='ff', SEED=11, RST_AT=700, RST_LEN=2, PHASE=6000)),
    ('slow-aux', (1, 0), dict(SCEN='load', IMAGE='real', SEED=12, LMIN=1, LMAX=60, PHASE=2500)),
    ('slow-aux-format', (1, 0), dict(SCEN='load', IMAGE='junk', SEED=13, LMIN=30, LMAX=60, PHASE=8000)),
    # aux contract test: 4-40 clk2x, 5 % of accesses stall 50-68 (the per-access
    # bound the read path needs), guest writes during the readback
    ('stall-aux', (1, 0), dict(SCEN='load', IMAGE='junk', SEED=16, LMIN=4, LMAX=40, STALL_PCT=5,
                               SMIN=50, SMAX=68, PHASE=5000)),
    ('fast-aux', (1, 0), dict(SCEN='load', IMAGE='real', SEED=14, LMIN=1, LMAX=1, PHASE=10000)),
    ('overflow-flag', (1, 0), dict(SCEN='overflow', IMAGE='real', SEED=15, WPER=12, LMIN=60, LMAX=60)),
]
# Each broken variant must fail, with this message among its errors.
NEGATIVE = [
    ('fault-byte-order', (1, 1), dict(SCEN='load', IMAGE='real', SEED=21), 'image when busy fell'),
    ('fault-busy-early', (1, 2), dict(SCEN='load', IMAGE='junk', SEED=22), 'image when busy fell'),
    ('fault-format-magic-card', (1, 3), dict(SCEN='load', IMAGE='real', SEED=23), 'image when busy fell'),
    ('fault-magic-first', (1, 4), dict(SCEN='reset_fmt', IMAGE='ff', SEED=24, RST_AT=700, RST_LEN=2), 'image when busy fell'),
    ('fault-half-format', (1, 5), dict(SCEN='load', IMAGE='nofile', SEED=25), 'image when busy fell'),
    ('fault-read-ack-early', (1, 6), dict(SCEN='load', IMAGE='real', SEED=26), 'readback'),
]
# Integration with the real VRAM controller (rtl/memory/psx_vram_psram.sv) and
# two psx_cram_model chips. All gating: load parked (park = APF reset), guest
# DDR card writes after unpark, readback with the core running and readback
# parked again (APF quit), every word exact. PARK_LOAD=0 loads and formats in
# synchronous mode (back-to-back format writes on the burst path). GPU_BURST
# runs GPU-style VRAM traffic during the running-core readback (GPU_GAP idle
# clk2x between bursts; GPU_WR=1 alternates 128-word reads and write
# streams); the controller lets each aux access in between two burst words.
INTEGRATION = [
    ('int-real', dict(IMAGE='real', SEED=1, GPU_BURST=0)),
    ('int-format-ff', dict(IMAGE='ff', SEED=6, GPU_BURST=0, PHASE=5000)),
    ('int-format-ff-sync-load', dict(IMAGE='ff', SEED=7, GPU_BURST=0, PARK_LOAD=0, PHASE=9000)),
    ('int-gpu16', dict(IMAGE='real', SEED=2, GPU_BURST=16, GPU_GAP=100)),
    ('int-scanout128', dict(IMAGE='real', SEED=3, GPU_BURST=128, GPU_GAP=1600)),
    ('int-gpu128-back-to-back', dict(IMAGE='real', SEED=4, GPU_BURST=128, GPU_GAP=0, PHASE=3000)),
    ('int-gpu128-write-stream', dict(IMAGE='junk', SEED=5, GPU_BURST=128, GPU_GAP=0, GPU_WR=1, PHASE=7000)),
]
# largest aux latency (request to aux_done, clk2x) an integration run accepts
# with the core running: the read path needs each access within ~70 clk2x
INTEGRATION_AUX_MAX = 60
# Bench self-check (negative): guest writes only four reads ahead of the
# readback can land after their words are read behind a 128-word burst.
INTEGRATION_NEGATIVE = [
    ('int-bench-lookahead-too-short', dict(IMAGE='real', SEED=3, GPU_BURST=128, GPU_GAP=0, AHEAD=4),
     'bench: guest write'),
]
INTEGRATION_SOURCES = ['sim/pocket/tb_memcard_slot_vram.sv', 'rtl/pocket/psx_memcard_slot.sv',
                       'rtl/pocket/psx_memcard_blank.sv', 'rtl/memory/psx_vram_psram.sv', 'sim/pocket/psx_cram_model.sv']

# fixed aux latency (clk2x) for every access at 88-clock APF pacing: the
# largest latency the write path and the read path (answer due one bridge
# transaction after its strobe) tolerate
SWEEP = [('fixed', l) for l in (60, 64, 66, 68, 70, 72, 76)]


def run(vvp, binary, args):
    cmd = [vvp, '-n', str(binary)] + [f'+{k}={v}' for k, v in args.items()]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=3600).stdout
    m = re.findall(r'^RESULT (\{.*\})$', out, re.M)
    result = json.loads(m[-1]) if m else None
    passed = out.rstrip().splitlines()[-2:].count('PASS') == 1 and result is not None and result['errors'] == 0
    errors = [l for l in out.splitlines() if l.startswith('ERROR')]
    return passed, result, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick', action='store_true', help='skip the latency sweep')
    parser.add_argument('--no-integration', action='store_true', help='skip the VRAM-controller integration runs')
    parser.add_argument('--jobs', type=int, default=os.cpu_count() or 4)
    opts = parser.parse_args()
    iverilog = find_tool('iverilog'); vvp = str(Path(iverilog).with_name('vvp'))
    directory = ROOT / 'build/sim/memcard-slot'; directory.mkdir(parents=True, exist_ok=True)
    builds = {}
    for little, fault in sorted({c[1] for c in POSITIVE + NEGATIVE} | {(1, 0)}):
        binary = directory / f'tb_l{little}_f{fault}.vvp'
        subprocess.run([iverilog, '-g2012', '-Wall', '-Wno-timescale', '-o', str(binary),
                        '-P', f'tb_memcard_slot.LITTLE={little}', '-P', f'tb_memcard_slot.FAULT={fault}']
                       + [str(ROOT / s) for s in SOURCES], check=True)
        builds[(little, fault)] = binary
    if not opts.no_integration:
        binary = directory / 'tb_integration.vvp'
        subprocess.run([iverilog, '-g2012', '-Wall', '-Wno-timescale', '-Wno-implicit-dimensions', '-I', str(ROOT / 'sim/pocket'),
                        '-s', 'tb_memcard_slot_vram', '-o', str(binary)] + [str(ROOT / s) for s in INTEGRATION_SOURCES],
                       check=True)
        builds['integration'] = binary
    jobs = [(n, b, a, None) for n, b, a in POSITIVE] + [(n, b, a, e) for n, b, a, e in NEGATIVE]
    if not opts.quick:
        jobs += [(f'sweep-{k}-L{l}', (1, 0), dict(SCEN='load', IMAGE='real', SEED=100 + l, LMIN=l, LMAX=l,
                  GUEST=50, GUEST_RB=0), 'sweep') for k, l in SWEEP]
    if not opts.no_integration:
        jobs += [(n, 'integration', a, 'integration') for n, a in INTEGRATION]
        jobs += [(n, 'integration', a, 'integration-negative') for n, a, _ in INTEGRATION_NEGATIVE]
    with concurrent.futures.ThreadPoolExecutor(opts.jobs) as pool:
        futures = {n: pool.submit(run, vvp, builds[b], a) for n, b, a, _ in jobs}
        outcome = {n: f.result() for n, f in futures.items()}

    summary = {'positive': {}, 'negative': {}, 'sweep': {}, 'integration': {}}
    ok = True
    for name, _, _, expect in jobs:
        passed, result, errors = outcome[name]
        if expect is None:
            summary['positive'][name] = dict(passed=passed, result=result, errors=errors[:4])
            ok &= passed
            print(f"{'PASS' if passed else 'FAIL'} {name}")
        elif expect == 'integration':
            good = passed and result['readback_sync_words_wrong'] == 0 and result['readback_parked_words_wrong'] == 0 \
                and result['aux_latency_max_clk2x'] <= INTEGRATION_AUX_MAX
            summary['integration'][name] = dict(gating=True, passed=good, result=result, errors=errors[:4])
            ok &= good
            print(f"{'PASS' if good else 'FAIL'} {name} (words wrong running/parked "
                  f"{result['readback_sync_words_wrong'] if result else '?'}/{result['readback_parked_words_wrong'] if result else '?'},"
                  f" aux latency max {result['aux_latency_max_clk2x'] if result else '?'} clk2x)")
        elif expect == 'integration-negative':
            want = next(e for n, _, e in INTEGRATION_NEGATIVE if n == name)
            rejected = not passed and any(want in e for e in errors)
            summary['negative'][name] = dict(rejected=rejected, errors=errors[:4])
            ok &= rejected
            print(f"{'REJECTED' if rejected else 'NOT REJECTED'} {name}: {errors[0] if errors else 'no error'}")
        elif expect == 'sweep':
            summary['sweep'][name] = dict(passed=passed, result=result, errors=errors[:2])
        else:
            rejected = not passed and any(expect in e for e in errors)
            summary['negative'][name] = dict(rejected=rejected, errors=errors[:4])
            ok &= rejected
            print(f"{'REJECTED' if rejected else 'NOT REJECTED'} {name}: {errors[0] if errors else 'no error'}")
    pos = [v['result'] for v in summary['positive'].values() if v['result'] and 'last_write_to_busy_ns' in v['result']]
    nominal = [r for r in pos if r['lmax'] == 30]
    timing = {
        'apf_read_period_clk74': 88,
        'nominal_aux_latency_clk2x': [4, 30],
        'last_write_to_busy_ns_max_unformatted': max(r['last_write_to_busy_ns'] for r in nominal if not r['formatted'] and r['last_write_to_busy_ns'] >= 0),
        'last_write_to_busy_ns_max_formatted': max(r['last_write_to_busy_ns'] for r in nominal if r['formatted'] and r['last_write_to_busy_ns'] >= 0),
        'format_ns_max': max(r['format_ns'] for r in nominal),
        'read_strobe_to_data_max_clk74': max(r['read_strobe_to_data_max_clk74'] for r in nominal),
        'read_deadline_clk74': nominal[0]['read_deadline_clk74'],
        'read_slack_min_clk74': min(r['read_slack_clk74'] for r in nominal),
        'idle_aux_cycles': sum(r['idle_aux_cycles'] for r in pos),
    }
    if summary['sweep']:
        for kind in ('fixed',):
            runs = {l: summary['sweep'][f'sweep-{kind}-L{l}'] for k, l in SWEEP if k == kind}
            passing = [l for l, v in runs.items() if v['passed']]
            failing = [l for l, v in runs.items() if not v['passed']]
            timing[f'sweep_{kind}_max_passing_aux_latency_clk2x'] = max(passing) if passing else None
            timing[f'sweep_{kind}_min_failing_aux_latency_clk2x'] = min(failing) if failing else None
            timing[f'sweep_{kind}'] = {l: dict(passed=v['passed'], slack_clk74=(v['result'] or {}).get('read_slack_clk74'),
                                              overflow=(v['result'] or {}).get('overflow')) for l, v in runs.items()}
        # the design must keep working well beyond the nominal 30-clock latency
        ok &= (timing['sweep_fixed_max_passing_aux_latency_clk2x'] or 0) >= 64
    ints = [v['result'] for v in summary['integration'].values() if v['result']]
    if ints:
        timing['integration_aux_latency_max_clk2x'] = max(r['aux_latency_max_clk2x'] for r in ints)
        timing['integration_read_slack_min_clk74'] = min(r['read_slack_clk74'] for r in ints)
    summary['timing'] = timing
    summary['passed'] = bool(ok)
    (directory / 'summary.json').write_text(json.dumps(summary, indent=1))
    print('TIMING ' + json.dumps(timing))
    print(('PASS' if ok else 'FAIL') + f' memcard slot: {len(POSITIVE)} positive cases, {len(NEGATIVE)} negative controls'
          + ('' if opts.quick else f', {len(SWEEP)}-point latency sweep'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
