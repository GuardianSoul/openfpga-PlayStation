#!/usr/bin/env python3
"""Save-state SPU tap checks (prepare_save_states.py spu_tap_text).

1. Unheld, the tapped SPU is cycle-identical to the SPU it transforms
   (the spu-guest-m10k lockstep bench, random register traffic, 2 seeds).
   (Voice register writes then still flag ADSR resets and repeat writes.)
2. sim/reference/tb_spu_save_tap.vhd: the state of one SPU, saved at the
   quiet point the way the save routine does, restored into another,
   gives the same output after a settle time (2 seeds); four planted
   restore faults must keep a mismatch to the end.
The SPU is the build's chain: context RAM, reverb table, guest M10K, static
readback, lean ADSR, CD queue, free voice slot.
"""
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_save_states import spu_tap_text
from prepare_spu_adsr_lean import adsr_lean_text
from prepare_spu_cd_queue import cd_queue_text
from prepare_spu_static_readback import static_readback_text
import run_spu_static_readback
from tool_paths import ROOT, find_tool

FREE_SLOT = ("                  if (voiceCounter >= 23 or useSDRAM = '0') then",
             "                  if (true) then")


def build_chain(text):
    text = cd_queue_text(adsr_lean_text(static_readback_text(text)))
    if text.count(FREE_SLOT[0]) != 1:
        raise ValueError('SPU free-slot anchor changed')
    return text.replace(*FREE_SLOT)


def save_tap(directory, seeds, faults, extra=()):
    """Run tb_spu_save_tap on the tapped build chain; return {(seed, fault): passed}."""
    ghdl = find_tool('ghdl')
    # reuse the lockstep run's prepared libraries and base SPU
    base = directory / 'lockstep' / 'spu_guest.vhd'
    work = directory / 'lockstep'
    tapped = work / 'spu_t.vhd'
    tapped.write_text(re.sub(r'\bspu\b', 'spu_t', spu_tap_text(build_chain(base.read_text()))))
    log = (work / 'build.log').open('w')
    def call(*args): subprocess.run([ghdl, *args], cwd=work, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
    call('-a', '--std=08', '-frelaxed', str(tapped))
    call('-a', '--std=08', '-frelaxed', str(ROOT / 'sim/reference/tb_spu_save_tap.vhd'))
    call('-e', '--std=08', '-frelaxed', 'tb_spu_save_tap')
    results = {}
    for seed in seeds:
        for fault in faults:
            r = subprocess.run([ghdl, '-r', '--std=08', '-frelaxed', 'tb_spu_save_tap', f'-gSEED={seed}', f'-gFAULT={fault}', *extra,
                                '--assert-level=error', '--ieee-asserts=disable'], cwd=work, capture_output=True, text=True, timeout=7200)
            out = r.stdout + r.stderr
            summary = [l.split('): ')[-1] for l in out.splitlines() if 'SPU save tap' in l or 'quiet point' in l]
            results[(seed, fault)] = (r.returncode == 0 and 'PASS SPU save tap' in out, summary)
            print(f'seed {seed} fault {fault}:', 'PASS' if results[(seed, fault)][0] else 'FAIL', *summary[:2], flush=True)
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dir', type=Path, default=ROOT / 'build/sim/spu-save-tap', help='build directory')
    directory = parser.parse_args().dir.resolve()
    failures = []
    for passed, tail in run_spu_static_readback.run(directory / 'lockstep', lambda t: spu_tap_text(t), (1, 2), 3000):
        print('lockstep (unheld):', 'PASS' if passed else 'FAIL', *tail, flush=True)
        if not passed: failures.append('unheld lockstep')
    results = save_tap(directory, (1, 2), (0,))
    results.update(save_tap(directory, (1,), (1, 2, 3, 4)))
    failures += [f'seed {s} fault {f}' for (s, f), (ok, _) in results.items() if not ok]
    if failures: raise SystemExit('SPU save tap check failed: ' + ', '.join(failures))
    print('PASS SPU save tap: unheld lockstep, restored state matches, planted faults caught')


if __name__ == '__main__':
    main()
