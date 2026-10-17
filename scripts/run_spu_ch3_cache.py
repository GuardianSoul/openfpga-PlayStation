#!/usr/bin/env python3
"""Native ch3 SPU cache bench (sim/memory/tb_psx_spu_ch3_cache.sv).

Runs psx_spu_ch3_cache (critical word first, and the slim word-granular
psx_spu_ch3_cache_slim.sv) and the direct ch3 path through psx_sdram_ctrl (CAS3,
SPU_FIRST) and the chip model on the same SPU-like traffic with a busy ch1
client. Passes when every read matches the golden copy, the cache misses no
tick after the cold first one and spends fewer clk1x per tick than the
direct path, and a copy without the posted-write update of a cached line is
caught.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from run_sdram_ctrl import ALTDDIO
from tool_paths import ROOT, find_tool

RESULT = re.compile(r'RESULT slot (\d+) cache (\d+) load (\d+) pitch (\d+) errors (\d+) late_ticks (\d+) mean (\d+) max (\d+) reads (\d+) hits (\d+)')


def run(work, name, cache_src, cache, ticks, pitch, slot=0):
    iverilog, vvp = find_tool('iverilog'), find_tool('vvp')
    sources = [work/'altddio_out.sv', ROOT/'rtl/memory/psx_sdram_ctrl.sv', ROOT/'sim/memory/sdram_chip_model.sv',
               cache_src, ROOT/'sim/memory/tb_psx_spu_ch3_cache.sv']
    out = work/f'{name}.vvp'
    subprocess.run([iverilog, '-g2012', '-o', str(out), '-s', 'tb_psx_spu_ch3_cache',
                    f'-Ptb_psx_spu_ch3_cache.CACHE={cache}', f'-Ptb_psx_spu_ch3_cache.TICKS={ticks}',
                    f'-Ptb_psx_spu_ch3_cache.PITCH={pitch}', f'-Ptb_psx_spu_ch3_cache.SLOT={slot}', *map(str, sources)], check=True)
    r = subprocess.run([vvp, '-n', str(out)], cwd=work, capture_output=True, text=True, timeout=3600)
    (work/f'{name}.log').write_text(r.stdout + r.stderr)
    m = RESULT.search(r.stdout)
    if not m: sys.exit(f'{name}: no result (see {work}/{name}.log)')
    keys = ('slot', 'cache', 'load', 'pitch', 'errors', 'late_ticks', 'mean', 'max', 'reads', 'hits')
    return dict(zip(keys, map(int, m.groups())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticks', type=int, default=120)
    parser.add_argument('--pitch', type=int, default=1, help='samples per tick per voice')
    parser.add_argument('--only', choices=('cwf', 'slim'), help='Run one cache design (direct runs always)')
    args = parser.parse_args()
    if not find_tool('iverilog'): sys.exit('Icarus Verilog not found (scripts/setup_iverilog.sh)')
    parent = ROOT/'build/sim/spu-ch3-cache'; parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='run-', dir=parent))
    (work/'altddio_out.sv').write_text(ALTDDIO)
    # Each cache with a planted fault: no update of a cached line on write.
    variants = {'cwf': (ROOT/'rtl/pocket/psx_spu_ch3_cache.sv', '                if (w_hit) begin\n', "                if (1'b0) begin\n"),
                'slim': (ROOT/'rtl/pocket/psx_spu_ch3_cache_slim.sv', "eng==E_WRITE && !discard && hit;", "eng==E_WRITE && 1'b0;")}
    report = dict(work=str(work))
    checks = {}
    for slot in (0, 24):
        direct = run(work, f'direct-slot{slot}', variants['cwf'][0], 0, args.ticks, args.pitch, slot)
        report[f'direct_slot{slot}'] = direct
        checks[f'direct reads match golden (slot {slot})'] = direct['errors'] == 0
        for key, (source, stale, planted) in variants.items():
            if args.only and key != args.only: continue
            cache = run(work, f'{key}-slot{slot}', source, 1, args.ticks, args.pitch, slot)
            report[f'{key}_slot{slot}'] = cache
            checks[f'{key} reads match golden (slot {slot})'] = cache['errors'] == 0
            if slot == 0:   # the fixed voice slot (no --spu-free-slot) is reported only
                checks[f'{key} late only on the cold first tick'] = cache['late_ticks'] <= 1
                text = source.read_text()
                if text.count(stale) != 1: sys.exit(f'{key}: write-update anchor not found')
                mutant = work/f'{source.stem}_no_update.sv'
                mutant.write_text(text.replace(stale, planted))
                stale_run = run(work, f'{key}-no-update', mutant, 1, args.ticks, args.pitch, slot)
                report[f'{key}_no_update'] = stale_run
                checks[f'{key} missing write update is caught'] = stale_run['errors'] > 0
                checks[f'{key} cheaper per tick than direct without the slot'] = cache['mean'] < direct['mean']
                if key == 'slim':   # 4x pitch (the PSX maximum): a new word every tick
                    fast = run(work, 'slim-pitch4', source, 1, args.ticks, 4, slot)
                    report['slim_pitch4'] = fast
                    checks['slim at 4x pitch: no late tick, cheaper than direct'] = (
                        fast['errors'] == 0 and fast['late_ticks'] == 0 and fast['mean'] < direct['mean'])
    print(json.dumps(report, indent=2))
    for name, ok in checks.items(): print(('PASS ' if ok else 'FAIL ') + name)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == '__main__':
    main()
