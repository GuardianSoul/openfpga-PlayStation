#!/usr/bin/env python3
"""Replay recorded game pad traffic into the pinned MiSTer joypad_pad.

tests/fixtures/pad-ape-escape-*.txt.gz are DuckStation pad traces (a private
regtest build logging every byte AnalogController/DigitalController::Transfer
saw: "IN OUT ACK") from 9,000 frames of Ape Escape (SCES-01564) with no input:
boot, title, analog setup and attract demo. "dualshock" is DuckStation's
AnalogController starting in digital mode like a real DualShock (its
ForceAnalogOnReset off); "digital" is its DigitalController.
sim/pocket/tb_joypad_replay.vhd plays each trace into the pinned pad (as
--dualshock or the digital pad) and compares every answer byte and ACK.

One known difference is allowed: the first 0x4D (rumble mapping) command's
last answer byte, 0x00 from DuckStation and 0xFF from MiSTer (the previous
mapping, all 0xFF after reset on hardware per psx-spx).
"""
import gzip
import re
import shutil
import subprocess

from tool_paths import ROOT, find_tool

TRACES = {
    'dualshock': (ROOT/'tests/fixtures/pad-ape-escape-dualshock.txt.gz', 1, [('FF', 'FF', '00')]),
    'digital': (ROOT/'tests/fixtures/pad-ape-escape-digital.txt.gz', 0, []),
}


def main():
    ghdl = find_tool('ghdl')
    folder = ROOT/'build/sim/joypad-replay'
    shutil.rmtree(folder, ignore_errors=True); folder.mkdir(parents=True)
    flags = ['--std=08', '-frelaxed']
    files = [ROOT/'references/PSX_MiSTer/rtl/pJoypad.vhd', ROOT/'references/PSX_MiSTer/rtl/joypad_pad.vhd',
             ROOT/'sim/pocket/tb_joypad_replay.vhd']
    subprocess.run([ghdl, '-a', *flags, *map(str, files)], cwd=folder, check=True, capture_output=True)
    subprocess.run([ghdl, '-e', *flags, 'tb_joypad_replay'], cwd=folder, check=True, capture_output=True)
    for name, (fixture, ds, known) in TRACES.items():
        trace = folder/(name+'.txt'); trace.write_bytes(gzip.decompress(fixture.read_bytes()))
        out = subprocess.run([ghdl, '-r', *flags, 'tb_joypad_replay', f'-gTRACE={trace}', f'-gDUALSHOCK={ds}'],
                             cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout
        summary = re.search(r'REPLAY bytes=(\d+) transactions=(\d+) byte_errs=(\d+) ack_errs=(\d+)', out)
        if not summary: raise SystemExit(f'{name}: no summary\n{out[-2000:]}')
        bytes_, tx, errs, ack_errs = map(int, summary.groups())
        diffs = re.findall(r'in (\w\w): core (\w\w) recorded (\w\w)', out)
        if ack_errs or sorted(diffs) != sorted(known) or errs != len(known):
            raise SystemExit(f'{name}: {errs} byte and {ack_errs} ACK differences, expected {known}\n{out[-3000:]}')
        print(f'{name}: {bytes_} bytes in {tx} transactions match DuckStation ({len(known)} known difference)')
    print('PASS joypad replay')


if __name__ == '__main__':
    main()
