#!/usr/bin/env python3
"""Serve memory card 1 straight from its memory window (no copy engine).

The upstream joypad + joypad_mem answer BIOS-style card read/write sector
commands through the SIO registers against a 128 KiB window model holding a
random card image (sim/reference/tb_memcard_window.vhd), three seeds. This
is the card-side half of the Pocket persistence design: the Pocket's save
slot only has to load and read back that window. Every check also runs with
the memcard-lean joypad_mem (scripts/prepare_memcard_lean.py).
"""
import subprocess

from prepare_memcard_lean import memcard_lean_text
from tool_paths import ROOT, find_tool

UPSTREAM = ROOT / 'references/PSX_MiSTer'


def main():
    for lean in (False, True): check(lean)


def check(lean):
    ghdl = find_tool('ghdl')
    directory = ROOT / ('build/sim/memcard-window-lean' if lean else 'build/sim/memcard-window'); directory.mkdir(parents=True, exist_ok=True)
    print('joypad_mem: ' + ('memcard-lean' if lean else 'upstream'))
    lean_mem = directory / 'joypad_mem_lean.vhd'
    if lean: lean_mem.write_text(memcard_lean_text((UPSTREAM / 'rtl/joypad_mem.vhd').read_text()))
    log = (directory / 'build.log').open('w')
    def call(*args): subprocess.run([ghdl, *args], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
    call('-a', '--std=08', '-frelaxed', str(UPSTREAM / 'sim/system/src/mem/dpram.vhd'))
    for name in ('pJoypad.vhd', 'joypad_pad.vhd', 'joypad_mem.vhd', 'joypad.vhd'):
        call('-a', '--std=08', '-frelaxed', str(lean_mem if lean and name == 'joypad_mem.vhd' else UPSTREAM / 'rtl' / name))
    call('-a', '--std=08', '-frelaxed', str(ROOT / 'sim/reference/tb_memcard_window.vhd'))
    call('-e', '--std=08', '-frelaxed', 'tb_memcard_window')
    passes = []
    for seed in (1, 2, 3):
        r = subprocess.run([ghdl, '-r', '--std=08', '-frelaxed', 'tb_memcard_window', f'-gSEED={seed}',
                            '--assert-level=error', '--ieee-asserts=disable'], cwd=directory, capture_output=True, text=True, timeout=1800)
        out = r.stdout + r.stderr
        line = next((l for l in out.splitlines() if 'PASS memcard window' in l), None)
        print(line[line.index('PASS'):] if line else out[-1500:])
        if r.returncode or not line: raise SystemExit('Memory card window check failed')
    # The blank card (psx_memcard_blank) served through the same protocol.
    import run_memcard_blank; run_memcard_blank.main()
    blank = ROOT / 'build/sim/memcard-blank/blank_words.txt'
    r = subprocess.run([ghdl, '-r', '--std=08', '-frelaxed', 'tb_memcard_window', f'-gIMAGE_FILE={blank}',
                        '--assert-level=error', '--ieee-asserts=disable'], cwd=directory, capture_output=True, text=True, timeout=1800)
    line = next((l for l in (r.stdout + r.stderr).splitlines() if 'PASS memcard window' in l), None)
    if r.returncode or not line: raise SystemExit('Blank card window check failed')
    print('blank card: ' + line[line.index('PASS'):])
    # A window that drops the card's write-back must be caught.
    r = subprocess.run([ghdl, '-r', '--std=08', '-frelaxed', 'tb_memcard_window', '-gDROP_WRITES=true', '-gSECTORS=4',
                        '--assert-level=error', '--ieee-asserts=disable'], cwd=directory, capture_output=True, text=True, timeout=1800)
    if r.returncode == 0: raise SystemExit('Planted dropped-write fault not detected')
    print('Planted dropped-write fault rejected')


if __name__ == '__main__': main()
