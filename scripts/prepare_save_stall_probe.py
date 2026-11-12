#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""EXPERIMENT routine "stall-probe" (docs/save-states-finish/stall-probe.md): never a release routine.
On routine c2 (big build -> mdec-tap -> early-load, not compressed, no size-mib), ROM only.

Question: does the Pocket's "State saved" banner stall about 20 s when its first 0x00A0 poll finds
the save still busy? The first five SAVES after the core starts are no-op saves that stay busy for
a set time, so the banner can be timed with a stopwatch against the busy time:

  save 1   busy as short as the routine can be (stub + cache flushes, about 1 ms)
  save 2-5 the same after a busy wait of DELAYS[1..4] x 2^PROBE_SHIFT loop rounds
           (about 50 ms, 150 ms, 400 ms, 1.5 s; the game stands still as in a real save)
  save 6+  the real save, unchanged
A no-op save: no quiet point, no copy; page 0 of the buffer gets the staging page (the game's
registers) with word 0 (the magic) zero, so the Memory can never load (the load's magic compare
refuses it); size 2,048 pages (4 MiB); FINISH 1. Loads are untouched.

The count is a word of stage 2's own image in SDRAM (probe_n, behind the SPU tables): the stub
unpacks stage 2 from the ROM whenever the staging page has no marker, which is the case after every
FPGA configuration (core start, wake), and the image holds 0 there. Only a no-op save writes it
(+1); the stub compares the stamp word alone, so the changed word does not cause another unpack.
A reset without configuration keeps the marker and the count.

Usage: prepare_save_stall_probe.py --routine OUT
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

TAG = 'save-stall-probe'
CLK1X = 33_868_800
PROBE_SHIFT = 17
DELAYS = (0, 0, 6, 19, 52, 194)       # x 2^17 rounds; ROUND_CYCLES clk1x a round
ROUND_CYCLES = 2                      # measured on the CPU RTL (docs/save-states-finish/stall-probe.md)
FIXED_CYCLES = 9574                   # measured: request to FINISH of a no-op save without a wait, stage 2 in place
UNPACK_CYCLES = 752652                # save 1 also unpacks stage 2 (routine E's cold - warm save; not measured on this one)
PROBES = len(DELAYS)


def rounds(save, shift=PROBE_SHIFT):
    """Loop rounds of save n (1-based); None for a real save."""
    return (DELAYS[save - 1] << shift) + 1 if save <= PROBES else None


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG} anchor: expected one match, found {text.count(old)}: {old[:90]!r}')
    return text.replace(old, new)


EQU_OLD = '        .equ    SIZE_HI, 0x1819\n'
EQU_NEW = EQU_OLD + f'        .equ    PROBE_SHIFT, {PROBE_SHIFT}         # stall-probe: delay unit, 2^n loop rounds\n'
QUIET_OLD = '''\
quiet:
        lui     $t1, 0x1F80             # (the stub used t1)
'''
QUIET_NEW = '''\
quiet:
        # stall-probe (EXPERIMENT): the first saves since stage 2 was unpacked are no-op saves
        # that stay busy for probe_tab[n] << PROBE_SHIFT loop rounds; page 0 gets magic 0
        lui     $t0, %hi(probe_n)
        lw      $t2, %lo(probe_n)($t0)
        bnez    $k1, 95f                # a load
        sltiu   $t3, $t2, PROBES
        beqz    $t3, 95f                # the real save
        addu    $t3, $t0, $t2
        lbu     $t3, %lo(probe_tab)($t3)
        addiu   $t2, $t2, 1
        sw      $t2, %lo(probe_n)($t0)
        sll     $t3, $t3, PROBE_SHIFT
94:     bnez    $t3, 94b
        addiu   $t3, $t3, -1
        sw      $zero, PAGE($k0)
        jal     put_page
        move    $a0, $zero
        sb      $zero, SIZE_LO($k0)
        li      $t0, 8
        sb      $t0, SIZE_HI($k0)
        b       answer
        li      $t0, 1
95:
        lui     $t1, 0x1F80             # (the stub used t1)
'''.replace('PROBES', str(PROBES))
TAIL_OLD = '''\
SPU_SAMPLE_SS:
        .hword  128, 256, 0, 0
'''
TAIL_NEW = TAIL_OLD + '''\
probe_n:                                # stall-probe: no-op saves done (0 in the image the stub unpacks)
        .word   0
probe_tab:
        .byte   ''' + ', '.join(str(d) for d in DELAYS) + '\n'

FAULTS = {
    # the delay table is read one entry further on (save 1 waits save 2's time, save 5 reads behind the table)
    'table-off-by-one': ('        lbu     $t3, %lo(probe_tab)($t3)\n', '        lbu     $t3, %lo(probe_tab+1)($t3)      # FAULT table-off-by-one\n'),
    # the no-op save leaves the magic as it was in the staging page
    'magic-kept': ('        sw      $zero, PAGE($k0)\n        jal     put_page\n', '        nop                             # FAULT magic-kept\n        jal     put_page\n'),
}


def firmware_text(text, shift=None):
    """Routine c2 with the probe. shift: a bench's PROBE_SHIFT (the ROM has 17)."""
    if 'stall-probe' in text:
        raise ValueError(f'{TAG}: the routine already has the probe')
    for label in ("94", "95"):
        import re
        if re.search(rf'^{label}:|[ ,]{label}[fb]\b', text, flags=re.M):
            raise ValueError(f'{TAG}: numeric label {label} is in use')
    if 'answer:' not in text or 'size-mib' in text:
        raise ValueError(f'{TAG}: the base is routine c2 (early-load, no size-mib)')
    text = once(text, EQU_OLD, EQU_NEW if shift is None else EQU_NEW.replace(f'PROBE_SHIFT, {PROBE_SHIFT} ', f'PROBE_SHIFT, {shift} '))
    text = once(text, QUIET_OLD, QUIET_NEW)
    return once(text, TAIL_OLD, TAIL_NEW)


def fault_text(text, name):
    old, new = FAULTS[name]
    return once(text, old, new)


def base_text():
    from prepare_save_early_load import base_text as before, firmware_text as early
    return early(before())


def table(round_cycles=ROUND_CYCLES, fixed_cycles=FIXED_CYCLES):
    rows = []
    for save in range(1, PROBES + 1):
        n = rounds(save)
        rows.append(dict(save=save, kind='no-op', table_byte=DELAYS[save - 1], rounds=n,
                         delay_ms=round(1000 * n * round_cycles / CLK1X, 1),
                         busy_ms=round(1000 * (n * round_cycles + fixed_cycles + (UNPACK_CYCLES if save == 1 else 0)) / CLK1X, 1)))
    rows.append(dict(save=f'{PROBES + 1}+', kind='real save'))
    return rows


def build(output_dir):
    from build_staged_save_fw import image_parts, rom1, rom2, rom40_hex, free_bytes
    from prepare_save_r1 import stub_bytes, packed_bytes
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = {}
    for name, text in (('early-load', base_text()), ('stall-probe', firmware_text(base_text()))):
        source = output_dir / f'staged-{name}.S'
        source.write_text(text)
        image, stage = image_parts(source=source)           # stops if the packed stage 2 does not fit
        stub = stub_bytes(source)
        rows[name] = dict(source=str(source), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), stub_bytes=len(stub),
                          stage2_bytes=len(stage), packed_bytes=packed_bytes(image, len(stub)), free_bytes=free_bytes(image))
    for name in FAULTS:
        faulty = output_dir / f'staged-stall-probe-fault-{name}.S'
        faulty.write_text(fault_text(firmware_text(base_text()), name))
        image_parts(source=faulty)
    rom = output_dir / 'psx_save_rom40.hex'
    image, _ = image_parts(source=output_dir / 'staged-stall-probe.S')
    rom.write_text(rom40_hex(rom1(image), rom2(image)))
    a, b = rows['early-load'], rows['stall-probe']
    if a['stub_bytes'] != b['stub_bytes']:
        raise ValueError(f'{TAG}: the stub changed')
    report = dict(rows, rom=str(rom), rom_sha256=hashlib.sha256(rom.read_bytes()).hexdigest(),
                  packed_bytes_added=b['packed_bytes'] - a['packed_bytes'], free_bytes_before=a['free_bytes'], free_bytes=b['free_bytes'],
                  experiment=True, probe_shift=PROBE_SHIFT, round_cycles=ROUND_CYCLES, saves=table())
    (output_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    for name, row in rows.items():
        print(f"{name:12} stage 2 {row['stage2_bytes']} bytes, packed {row['packed_bytes']}, free {row['free_bytes']}")
    for row in report['saves']:
        print(row)
    print(f"source sha256: {b['source_sha256']}\nROM sha256:    {report['rom_sha256']}")
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--routine', type=Path, metavar='OUT', required=True)
    args = parser.parse_args()
    try:
        build(args.routine)
    except ValueError as error:
        raise SystemExit(str(error))
