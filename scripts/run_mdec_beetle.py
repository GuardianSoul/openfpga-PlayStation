#!/usr/bin/env python3
"""Ground truth for the "mednafen" MDEC model: Beetle PSX mdec.c itself.

Compiles mednafen/psx/mdec.c at e57860d8, unmodified (taken from the commit,
not the working tree), inside sim/reference/mdec_beetle_harness.c, scalar
(-mno-sse2) and with SSE2, feeds it run_mdec_reference.py's vectors and
compares its output, in the RAM order dma.c gives it, word for word with the
model. The model is the bench's expectation for mdec-mednafen, so equal here
means the transform's RTL output equals Beetle's.
Usage: run_mdec_beetle.py BEETLE_CHECKOUT [--bench DIR]  (DIR: out-SEED.txt files to compare too)
"""
import argparse
import os
import subprocess
from pathlib import Path

import run_mdec_reference as reference
from tool_paths import ROOT

COMMIT = 'e57860d8'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('beetle', type=Path)
    ap.add_argument('--bench', type=Path)
    args = ap.parse_args()
    beetle = args.beetle.resolve(); d = ROOT/'build/sim/mdec-beetle'; d.mkdir(parents=True, exist_ok=True)
    (d/'mdec.c').write_text(subprocess.run(['git', '-C', str(beetle), 'show', f'{COMMIT}:mednafen/psx/mdec.c'],
                                           capture_output=True, text=True, check=True).stdout)
    include = [f'-I{d}', f'-I{beetle}/mednafen/psx', f'-I{beetle}/mednafen', f'-I{beetle}/libretro-common/include']
    builds = {'scalar': ['-O1', '-mno-sse2'], 'sse2': ['-O2']}
    for name, flags in builds.items():
        subprocess.run(['gcc', *flags, *include, '-o', str(d/name), str(ROOT/'sim/reference/mdec_beetle_harness.c')], check=True)
    failures = 0
    for seed in range(int(os.environ.get('PSX_MDEC_SEEDS', '3'))):
        lines, expected, labels = reference.vectors(seed, 'mednafen')
        path = d/f'vectors-{seed}.txt'; path.write_text('\n'.join(lines) + '\n')
        for name in builds:
            out = subprocess.run([str(d/name), str(path)], capture_output=True, text=True, check=True).stdout.split('\n')
            placed = sorted((int(l.split()[0]), n, int(l.split()[1], 16)) for n, l in enumerate(out) if l)
            got = [w for _, _, w in placed]
            bad = [i for i, (a, b) in enumerate(zip(got, expected)) if a != b]
            if len(got) != len(expected) or bad:
                failures += 1
                print(f'FAIL seed {seed} beetle-{name}: {len(got)} words, model {len(expected)}, {len(bad)} differ'
                      + (f'; first word {bad[0]} ({labels[bad[0]]}) beetle {got[bad[0]]:08x} model {expected[bad[0]]:08x}' if bad else ''))
            else:
                print(f'PASS seed {seed} beetle-{name}: {len(got)} words equal the mednafen model')
            if args.bench and name == 'scalar':
                bench = [int(w, 16) for w in (args.bench/f'out-{seed}.txt').read_text().split()]
                n = sum(a != b for a, b in zip(bench, got)) + abs(len(bench) - len(got))
                print(f'{"PASS" if n == 0 else "DIFF"} seed {seed} bench {args.bench.name}: {n} of {len(got)} words differ from Beetle')
    raise SystemExit(1 if failures else 0)


if __name__ == '__main__':
    main()
