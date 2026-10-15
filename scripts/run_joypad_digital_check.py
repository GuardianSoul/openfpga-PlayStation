#!/usr/bin/env python3
"""Lockstep rtl/pocket/joypad_pad_digital.vhd against the pinned MiSTer joypad_pad.

The pinned entity is copied as joypad_pad_ref; both run in
sim/pocket/tb_joypad_pad_digital.vhd with one and two digital pads and three
seeds each. Three planted mutations of the lean entity must be caught.
"""
import re
import shutil
import subprocess

from tool_paths import ROOT, find_tool

PINNED = ROOT/'references/PSX_MiSTer/rtl/joypad_pad.vhd'
LEAN = ROOT/'rtl/pocket/joypad_pad_digital.vhd'
BENCH = ROOT/'sim/pocket/tb_joypad_pad_digital.vhd'
MUTATIONS = (
    ('receiveBuffer(7) <= not joypad.KeySquare;', 'receiveBuffer(7) <= not joypad.KeyCross;'),
    ('transmitValue = x"4D") then', 'transmitValue = x"4E") then'),
    ('<= -64) then receiveBuffer(4)', '<= -63) then receiveBuffer(4)'),
)


def run(ghdl, folder, lean, runs):
    for name in ('work-obj08.cf',):
        (folder/name).unlink(missing_ok=True)
    flags = ['--std=08', '-frelaxed']
    files = [ROOT/'references/PSX_MiSTer/rtl/pJoypad.vhd', folder/'joypad_pad_ref.vhd', lean, BENCH]
    subprocess.run([ghdl, '-a', *flags, *map(str, files)], cwd=folder, check=True, capture_output=True)
    subprocess.run([ghdl, '-e', *flags, 'tb_joypad_pad_digital'], cwd=folder, check=True, capture_output=True)
    results = []
    for pad2, seed in runs:
        out = subprocess.run([ghdl, '-r', *flags, 'tb_joypad_pad_digital', f'-gPAD2={pad2}', f'-gSEED={seed}'],
                             cwd=folder, capture_output=True, text=True).stdout
        found = re.search(r'PAD2=\d+ cycles=\d+ receiveValid=(\d+) acks=(\d+) errs=(\d+)', out)
        if not found: raise SystemExit(f'No summary for PAD2={pad2} SEED={seed}:\n{out[-2000:]}')
        results.append(tuple(map(int, found.groups())))
    return results


def main():
    ghdl = find_tool('ghdl')
    if not ghdl: raise SystemExit('GHDL required')
    folder = ROOT/'build/sim/joypad-digital'
    shutil.rmtree(folder, ignore_errors=True); folder.mkdir(parents=True)
    text = PINNED.read_text()
    for old in ('entity joypad_pad is', 'architecture arch of joypad_pad is'):
        if text.count(old) != 1: raise SystemExit(f'Pinned joypad_pad changed: {old}')
    text = text.replace('entity joypad_pad is', 'entity joypad_pad_ref is')
    text = text.replace('architecture arch of joypad_pad is', 'architecture arch of joypad_pad_ref is')
    (folder/'joypad_pad_ref.vhd').write_text(text)
    for valids, acks, errs in run(ghdl, folder, LEAN, [(p, s) for p in (0, 1) for s in (1, 2, 3)]):
        if errs or valids < 1000: raise SystemExit(f'Lockstep failed: receiveValid={valids} errs={errs}')
    print('joypad_pad_digital lockstep: 6 runs x 400000 cycles, 0 mismatches')
    lean = LEAN.read_text()
    for index, (old, new) in enumerate(MUTATIONS):
        if lean.count(old) != 1: raise SystemExit(f'Mutation anchor missing: {old}')
        mutant = folder/f'mutant{index}.vhd'; mutant.write_text(lean.replace(old, new))
        (_, _, errs), = run(ghdl, folder, mutant, [(1, 1)])
        if not errs: raise SystemExit(f'Mutation not caught: {new}')
        print(f'mutation {index} caught: {errs} mismatching cycles')


if __name__ == '__main__':
    main()
