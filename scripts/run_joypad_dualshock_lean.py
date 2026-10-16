#!/usr/bin/env python3
"""Lockstep rtl/pocket/joypad_pad_dualshock.vhd against the pinned MiSTer joypad_pad.

The pinned entity is copied as joypad_pad_ref; both run in
sim/pocket/tb_joypad_pad_dualshock.vhd (pad 1 DualShock/digital at random, pad
2 disabled or digital) with three seeds each, then the lean pad replays the
recorded Ape Escape traffic (scripts/run_joypad_replay.py traces). Planted
mutations of the lean entity must be caught.
"""
import gzip
import re
import shutil
import subprocess

from tool_paths import ROOT, find_tool

PINNED = ROOT/'references/PSX_MiSTer/rtl/joypad_pad.vhd'
LEAN = ROOT/'rtl/pocket/joypad_pad_dualshock.vhd'
BENCH = ROOT/'sim/pocket/tb_joypad_pad_dualshock.vhd'
MUTATIONS = (
    ('receiveBuffer   <= x"73";\n                              else', 'receiveBuffer   <= x"53";\n                              else'),
    ('dsRumbleConfig <= (others => \'0\');', 'dsRumbleConfig <= (others => \'1\');'),
    ("joypad.KeyR3 = '1' and joypad.KeyUp = '1'", "joypad.KeyR3 = '1' and joypad.KeyLeft = '1'"),
    ('newAnalog := newAnalog + newAnalog / 8;', 'newAnalog := newAnalog + newAnalog / 4;'),
)


def run(ghdl, folder, lean, runs):
    (folder/'work-obj08.cf').unlink(missing_ok=True)
    flags = ['--std=08', '-frelaxed']
    files = [ROOT/'references/PSX_MiSTer/rtl/pJoypad.vhd', folder/'joypad_pad_ref.vhd', lean, BENCH]
    subprocess.run([ghdl, '-a', *flags, *map(str, files)], cwd=folder, check=True, capture_output=True)
    subprocess.run([ghdl, '-e', *flags, 'tb_joypad_pad_dualshock'], cwd=folder, check=True, capture_output=True)
    results = []
    for pad2, seed in runs:
        out = subprocess.run([ghdl, '-r', *flags, 'tb_joypad_pad_dualshock', f'-gPAD2={pad2}', f'-gSEED={seed}'],
                             cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout
        found = re.search(r'PAD2=\d+ cycles=\d+ receiveValid=(\d+) acks=(\d+) config=(\d+) analog=(\d+) errs=(\d+)', out)
        if not found: raise SystemExit(f'No summary for PAD2={pad2} SEED={seed}:\n{out[-2000:]}')
        results.append(tuple(map(int, found.groups())))
    return results


def replay(ghdl, folder):
    flags = ['--std=08', '-frelaxed']
    work = folder/'replay'; shutil.rmtree(work, ignore_errors=True); work.mkdir()
    files = [ROOT/'references/PSX_MiSTer/rtl/pJoypad.vhd', LEAN, ROOT/'sim/pocket/tb_joypad_replay.vhd']
    subprocess.run([ghdl, '-a', *flags, *map(str, files)], cwd=work, check=True, capture_output=True)
    subprocess.run([ghdl, '-e', *flags, 'tb_joypad_replay'], cwd=work, check=True, capture_output=True)
    for name, ds, known in (('dualshock', 1, 1), ('digital', 0, 0)):
        trace = work/(name+'.txt')
        trace.write_bytes(gzip.decompress((ROOT/f'tests/fixtures/pad-ape-escape-{name}.txt.gz').read_bytes()))
        out = subprocess.run([ghdl, '-r', *flags, 'tb_joypad_replay', f'-gTRACE={trace}', f'-gDUALSHOCK={ds}'],
                             cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout
        found = re.search(r'REPLAY bytes=(\d+) transactions=\d+ byte_errs=(\d+) ack_errs=(\d+)', out)
        if not found or int(found.group(2)) != known or int(found.group(3)):
            raise SystemExit(f'Ape Escape replay ({name}) on the lean pad:\n{out[-2000:]}')
        print(f'Ape Escape {name} replay on the lean pad: {found.group(1)} bytes, {known} known DuckStation difference')


def main():
    ghdl = find_tool('ghdl')
    if not ghdl: raise SystemExit('GHDL required')
    folder = ROOT/'build/sim/joypad-dualshock-lean'
    shutil.rmtree(folder, ignore_errors=True); folder.mkdir(parents=True)
    text = PINNED.read_text()
    for old in ('entity joypad_pad is', 'architecture arch of joypad_pad is'):
        if text.count(old) != 1: raise SystemExit(f'Pinned joypad_pad changed: {old}')
    text = text.replace('entity joypad_pad is', 'entity joypad_pad_ref is')
    text = text.replace('architecture arch of joypad_pad is', 'architecture arch of joypad_pad_ref is')
    (folder/'joypad_pad_ref.vhd').write_text(text)
    for valids, acks, config, analog, errs in run(ghdl, folder, LEAN, [(p, s) for p in (0, 1) for s in (1, 2, 3)]):
        if errs or valids < 1000 or config < 20 or analog < 20:
            raise SystemExit(f'Lockstep failed: receiveValid={valids} config={config} analog={analog} errs={errs}')
        print(f'lockstep: receiveValid={valids} config-mode bytes={config} analog-mode bytes={analog} errs=0')
    print('joypad_pad_dualshock lockstep: 6 runs x 400000 cycles, 0 mismatches')
    replay(ghdl, folder)
    lean = LEAN.read_text()
    for index, (old, new) in enumerate(MUTATIONS):
        if lean.count(old) != 1: raise SystemExit(f'Mutation anchor missing: {old}')
        mutant = folder/f'mutant{index}.vhd'; mutant.write_text(lean.replace(old, new))
        (_, _, _, _, errs), = run(ghdl, folder, mutant, [(1, 1)])
        if not errs: raise SystemExit(f'Mutation not caught: {new}')
        print(f'mutation {index} caught: {errs} mismatching cycles')
    print('PASS joypad dualshock lean')


if __name__ == '__main__':
    main()
