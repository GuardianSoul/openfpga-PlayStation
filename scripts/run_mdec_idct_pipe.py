#!/usr/bin/env python3
"""Cycle-lockstep the mdec-idct-pipe variant against the upstream MDEC.

Random quantization and scale tables and random run-length macroblocks with
AC coefficients in all four output depths, signed and unsigned, run through
both entities; every port output must match on every 2x cycle. A planted
one-cycle swap delay and a planted operand mix-up must be rejected.
"""
from pathlib import Path
import random
import re
import subprocess

from prepare_mdec_idct_pipe import idct_pipe_text
from tool_paths import ROOT, find_tool

UPSTREAM = ROOT / 'references/PSX_MiSTer'


def block(rng):
    halfwords = [(rng.randrange(1, 64) << 10) | rng.randrange(1024)]
    index = 0
    while True:
        run = rng.choice((0, 0, 0, 1, 2, 5, 9))
        if index + run + 1 > 63 or rng.random() < 0.08: break
        index += run + 1
        halfwords.append((run << 10) | rng.randrange(1024))
    return halfwords + [0xFE00]


def vectors(path, seed):
    rng = random.Random(seed); lines = ['B 04 60000000']  # enable both DMA requests
    def command(word, data): lines.append(f'B 00 {word:08x}'); lines.extend(f'D 00 {w:08x}' for w in data)
    command(0x40000001, [rng.getrandbits(32) & 0x7F7F7F7F | 0x01010101 for _ in range(32)])
    command(0x60000000, [rng.getrandbits(32) for _ in range(32)])
    for depth, words_per_macroblock in ((2, 192), (3, 128), (1, 16), (0, 8), (2, 192)):
        signed = rng.randrange(2); bit15 = rng.randrange(2)
        count = 2
        blocks = 6 if depth >= 2 else 1
        halfwords = [h for _ in range(count * blocks) for h in block(rng)]
        if len(halfwords) % 2: halfwords.append(0xFE00)
        data = [halfwords[i] | (halfwords[i + 1] << 16) for i in range(0, len(halfwords), 2)]
        command(0x20000000 | (depth << 27) | (signed << 26) | (bit15 << 25) | len(data), data)
        lines.append(f'R 00 {count * words_per_macroblock:08x}')
    # The final 24-bit macroblock's last words leave only when more input
    # follows, so feed one more (unread) macroblock as a flush.
    halfwords = [h for _ in range(6) for h in block(rng)]
    if len(halfwords) % 2: halfwords.append(0xFE00)
    data = [halfwords[i] | (halfwords[i + 1] << 16) for i in range(0, len(halfwords), 2)]
    command(0x30000000 | len(data), data)
    path.write_text('\n'.join(lines) + '\n')


def run(directory, variant_text, seeds):
    ghdl = find_tool('ghdl'); directory.mkdir(parents=True, exist_ok=True)
    log = (directory / 'build.log').open('w')
    def call(*args): subprocess.run([ghdl, *args], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
    for library in ('mem', 'work'):
        call('-a', '--std=08', '-frelaxed', f'--work={library}', str(UPSTREAM / 'sim/system/src/mem/dpram.vhd'))
    for name in ('RamMLAB.vhd', 'SyncFifo.vhd', 'SyncFifoFallThrough.vhd'):
        source = UPSTREAM / 'sim/system/src/mem' / name
        call('-a', '--std=08', '-frelaxed', '--work=mem', str(source if source.exists() else UPSTREAM / 'rtl' / name))
    call('-a', '--std=08', '-frelaxed', str(UPSTREAM / 'rtl/mdec.vhd'))
    variant = directory / 'mdec_pipe.vhd'
    variant.write_text(re.sub(r'\bmdec\b', 'mdec_pipe', variant_text))
    call('-a', '--std=08', '-frelaxed', str(variant))
    call('-a', '--std=08', '-frelaxed', str(ROOT / 'sim/reference/tb_mdec_idct_pipe.vhd'))
    vectors(directory / 'mdec-random.txt', 0)  # default generic, opened at elaboration
    call('-e', '--std=08', '-frelaxed', 'tb_mdec_idct_pipe')
    results = []
    for seed in seeds:
        path = directory / f'mdec-random-{seed}.txt'; vectors(path, seed)
        result = subprocess.run([ghdl, '-r', '--std=08', '-frelaxed', 'tb_mdec_idct_pipe', f'-gVECTOR_FILE={path}',
                                 '--assert-level=error', '--ieee-asserts=disable-at-0'], cwd=directory,
                                capture_output=True, text=True, timeout=1200)
        results.append((result.returncode == 0 and 'PASS MDEC IDCT pipeline lockstep' in result.stdout + result.stderr,
                        (result.stdout + result.stderr).strip().splitlines()[-1:]))
    return results


def main():
    original = (UPSTREAM / 'rtl/mdec.vhd').read_text()
    directory = ROOT / 'build/sim/mdec-idct-pipe'
    results = run(directory / 'variant', idct_pipe_text(original), (1, 2, 3))
    for passed, tail in results: print('PASS' if passed else 'FAIL', *tail)
    failures = [r for r in results if not r[0]]
    faults = {
        'target': lambda t: t.replace('idct_calc1_target2 <= idct_calcP_target2;', 'idct_calc1_target2 <= idct_calc0_target2;'),
        'operand': lambda t: t.replace('idct_calcP_mul21    <= idct_calc0_mul21;', 'idct_calcP_mul21    <= idct_calc0_mul11;'),
    }
    for name, fault in faults.items():
        text = idct_pipe_text(original); planted = fault(text)
        if planted == text: raise SystemExit('Fault site missing: ' + name)
        caught = not run(directory / ('fault-' + name), planted, (1,))[0][0]
        print(f'fault {name}: {"rejected" if caught else "NOT DETECTED"}')
        if not caught: failures.append(name)
    if failures: raise SystemExit('MDEC IDCT pipeline check failed')
    print('MDEC IDCT pipeline is cycle-identical at the ports; planted faults rejected')


if __name__ == '__main__': main()
