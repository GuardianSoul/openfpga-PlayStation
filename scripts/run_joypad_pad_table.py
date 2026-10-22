#!/usr/bin/env python3
"""Lockstep rtl/pocket/joypad_pad_table.vhd against rtl/pocket/joypad_pad_dualshock.vhd.

The DualShock pad (itself lockstep-equal to the pinned MiSTer joypad_pad,
make test-joypad-dualshock-lean) is copied as joypad_pad_lean; both run in
sim/pocket/tb_joypad_pad_table.vhd under the SIO's byte timing with
RUMBLE_ECHO off (the Pocket build): three seeds each with pad 2 disabled and
digital, then STICK_DPAD off and ANALOG_LEVEL on. The table pad then replays
the recorded Ape Escape traffic (scripts/run_joypad_replay.py traces) with
the SIO's gap between a byte and actionNext. Planted mutations of the table
pad must be caught.
"""
import gzip
import re
import shutil
import subprocess

from tool_paths import ROOT, find_tool

LEAN = ROOT/'rtl/pocket/joypad_pad_dualshock.vhd'
TABLE = ROOT/'rtl/pocket/joypad_pad_table.vhd'
BENCH = ROOT/'sim/pocket/tb_joypad_pad_table.vhd'
FLAGS = ['--std=08', '-frelaxed']
MUTATIONS = (  # (old, new, generics)
    ('(16#00#, 16#01#, 16#02#, 16#00#, 16#0A#)', '(16#00#, 16#01#, 16#02#, 16#00#, 16#0B#)', ()),
    ('t(ak(S_SETM, K_03)) := w(S_Z + 1,', 't(ak(S_SETM, K_03)) := w(S_Z,', ()),
    ('t(256 + 16#4C#) := std_logic_vector(to_unsigned(K_4C, 8));', 't(256 + 16#4C#) := std_logic_vector(to_unsigned(K_4D, 8));', ()),
    ('receiveValid  <= romWord(7) or not idleReady;', 'receiveValid  <= romWord(7);', ()),
    ('state or ("000000" & (dsAnalogModeSave or dsConfigModeSave))', 'state or ("000000" & dsAnalogModeSave)', ()),
    ('then id := 16#F3#;', 'then id := 16#F1#;', ()),
    ('if (to_integer(joypad.Analog1Y) >=  64) then b(6)', 'if (to_integer(joypad.Analog1Y) >=  65) then b(6)', ()),
    ('dsAnalogMode(portNr)    <= joypad.ToggleDS;', 'dsAnalogMode(portNr)    <= not joypad.ToggleDS;', ('-gANALOG_LEVEL=true',)),
    # port 2's L3+R3+Up combo lands in port 1's state (needs a second DualShock)
    ("joypad.KeyUp = '1') then\n                     dsAnalogMode(portNr) <= '1';", "joypad.KeyUp = '1') then\n                     dsAnalogMode(0) <= '1';", ('-gPAD2=2',)),
)
RUNS = [(('-gPAD2=0', f'-gSEED={s}'), 1) for s in (1, 2, 3)] + [(('-gPAD2=1', f'-gSEED={s}'), 1) for s in (4, 5, 6)] + [
    (('-gSEED=7', '-gSTICK_DPAD=false'), 1), (('-gSEED=8', '-gANALOG_LEVEL=true', '-gCYCLES=1500000'), 1),
    (('-gSEED=9', '-gPAD2=1', '-gANALOG_LEVEL=true', '-gCYCLES=2000000'), 1)] + [
    (('-gPAD2=2', f'-gSEED={s}'), 1) for s in (12, 13, 14)] + [
    (('-gSEED=15', '-gPAD2=2', '-gANALOG_LEVEL=true', '-gCYCLES=2000000'), 1)]


def build(ghdl, folder, table):
    (folder/'work-obj08.cf').unlink(missing_ok=True)
    files = [ROOT/'references/PSX_MiSTer/rtl/pJoypad.vhd', folder/'joypad_pad_lean.vhd', table, BENCH]
    subprocess.run([ghdl, '-a', *FLAGS, *map(str, files)], cwd=folder, check=True, capture_output=True)
    subprocess.run([ghdl, '-e', *FLAGS, 'tb_joypad_pad_table'], cwd=folder, check=True, capture_output=True)


def run(ghdl, folder, generics):
    out = subprocess.run([ghdl, '-r', *FLAGS, 'tb_joypad_pad_table', *generics],
                         cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout
    found = re.search(r'TABLE PAD2=\d+ cycles=\d+ receiveValid=(\d+) acks=\d+ config=(\d+) analog=(\d+) sequences=(\d+) ff=\d+ errs=(\d+)', out)
    if not found: raise SystemExit(f'No summary for {generics}:\n{out[-2000:]}')
    return tuple(map(int, found.groups()))


def replay(ghdl, folder):
    work = folder/'replay'; shutil.rmtree(work, ignore_errors=True); work.mkdir()
    files = [ROOT/'references/PSX_MiSTer/rtl/pJoypad.vhd', TABLE, ROOT/'sim/pocket/tb_joypad_replay.vhd']
    subprocess.run([ghdl, '-a', *FLAGS, *map(str, files)], cwd=work, check=True, capture_output=True)
    subprocess.run([ghdl, '-e', *FLAGS, 'tb_joypad_replay'], cwd=work, check=True, capture_output=True)
    for name, ds, known in (('dualshock', 1, 1), ('digital', 0, 0)):
        trace = work/(name+'.txt')
        trace.write_bytes(gzip.decompress((ROOT/f'tests/fixtures/pad-ape-escape-{name}.txt.gz').read_bytes()))
        out = subprocess.run([ghdl, '-r', *FLAGS, 'tb_joypad_replay', f'-gTRACE={trace}', f'-gDUALSHOCK={ds}', '-gGAP=4'],
                             cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout
        found = re.search(r'REPLAY bytes=(\d+) transactions=\d+ byte_errs=(\d+) ack_errs=(\d+)', out)
        if not found or int(found.group(2)) != known or int(found.group(3)):
            raise SystemExit(f'Ape Escape replay ({name}) on the table pad:\n{out[-2000:]}')
        print(f'Ape Escape {name} replay on the table pad: {found.group(1)} bytes, {known} known DuckStation difference')


def main():
    ghdl = find_tool('ghdl')
    if not ghdl: raise SystemExit('GHDL required')
    folder = ROOT/'build/sim/joypad-pad-table'
    shutil.rmtree(folder, ignore_errors=True); folder.mkdir(parents=True)
    text = LEAN.read_text()
    for old in ('entity joypad_pad is', 'architecture dualshock of joypad_pad is'):
        if text.count(old) != 1: raise SystemExit(f'joypad_pad_dualshock changed: {old}')
    (folder/'joypad_pad_lean.vhd').write_text(text.replace('entity joypad_pad is', 'entity joypad_pad_lean is')
                                              .replace('architecture dualshock of joypad_pad is', 'architecture dualshock of joypad_pad_lean is'))
    build(ghdl, folder, TABLE)
    for generics, _ in RUNS:
        valids, config, analog, sequences, errs = run(ghdl, folder, generics)
        if errs or valids < 1000 or config < 20 or analog < 20 or sequences < 5:
            raise SystemExit(f'Lockstep failed {generics}: receiveValid={valids} config={config} analog={analog} sequences={sequences} errs={errs}')
        print(f'lockstep {" ".join(generics)}: receiveValid={valids} config-mode bytes={config} analog-mode bytes={analog} errs=0')
    replay(ghdl, folder)
    table = TABLE.read_text()
    for index, (old, new, generics) in enumerate(MUTATIONS):
        if table.count(old) != 1: raise SystemExit(f'Mutation anchor missing: {old}')
        mutant = folder/f'mutant{index}.vhd'; mutant.write_text(table.replace(old, new))
        build(ghdl, folder, mutant)
        errs = run(ghdl, folder, ('-gPAD2=1', '-gSEED=11', '-gCYCLES=1000000', *generics))[-1]
        if not errs: raise SystemExit(f'Mutation not caught: {new}')
        print(f'mutation {index} caught: {errs} mismatching cycles')
    print('PASS joypad pad table')


if __name__ == '__main__':
    main()
