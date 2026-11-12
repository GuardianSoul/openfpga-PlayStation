#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine step "readout-hold" (docs/save-states-finish/readout-hold.md): after a save's FINISH the
routine keeps the game stopped (SPU held, CD frozen, SAVE_MODE on) while the Pocket reads the Memory out
of the save buffer, and returns to the game when the read-out is over. ROM only; chained last.

NEEDS the RTL step prepare_save_readout_hold.py (rd_seen: bit 1 of the SPU_HOLD read, 0x180C, set by a
bridge read of the save window, cleared by a write to SPU_HOLD). On a core without it the bit reads 0 for
ever and every save waits the whole no-read limit.

The routine does the timing: it writes SPU_HOLD = 1 (the value it holds; the write clears rd_seen), waits
one window in a counted loop and reads the bit. Per window:
  a read was seen                the read-out runs; from now on the first quiet window ends the wait
  quiet, a read was seen before  the read-out is over: on to the exit
  quiet, never a read            on to the exit once NOREAD_S have passed (the Pocket's fixed wait may come
                                 before its reads)
  TOTAL_S have passed            on to the exit whatever was seen
A load is not changed, nor a save that ends in an error or a retry (they do not pass this place).

Build-time choices (firmware_text arguments; the defaults are the first hardware build's):
  WINDOW_S 1.5   quiet time that ends the wait, and the poll period
  NOREAD_S 45    limit while no read was ever seen
  TOTAL_S  60    limit in any case
"Hold game while saving: Off" is the routine without this step.

Usage: prepare_save_readout_hold_routine.py --routine SRC.S --output DIR   (the routine with the step, both
           ROM files, report.json with the packed bytes added; SRC.S is the chain before it, with the
           extension loader)
       prepare_save_readout_hold_routine.py --chain --output DIR          (the same on this checkout's chain:
           big build -> mdec-tap -> early-load -> size-mib -> rom-ext loader -> compress)
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

TAG = 'readout-hold'
CLK1X = 33_868_800
ROUND_CYCLES = 2            # clk1x per round of the wait loop (bnez + addiu, cached; measured: readout-hold.md)
WINDOW_S, NOREAD_S, TOTAL_S = 1.5, 45, 60
BENCH_ROUNDS = 1500         # BENCH-ONLY window (run_save_project_cpu.py --routine-step readout-hold)


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG} anchor: expected one match, found {text.count(old)}: {old[:90]!r}')
    return text.replace(old, new)


def numbers(window_s=WINDOW_S, noread_s=NOREAD_S, total_s=TOTAL_S):
    """-> (lui operand of the window's loop count, windows in all, windows left when the no-read limit ends)."""
    hi = round(window_s * CLK1X / ROUND_CYCLES / 65536)
    total, noread = round(total_s / window_s), round(noread_s / window_s)
    if not (0 < hi < 0x8000 and 0 < noread <= total < 64):
        raise ValueError(f'{TAG}: window {window_s} s, limits {noread_s} s / {total_s} s do not fit (at most 63 windows)')
    return hi, total, total - noread


ANCHOR = '''\
        li      $t0, 1
        sb      $t0, FINISH($k0)
        jal     get_page                # the registers back into the staging page
'''
LOAD_LINE = '        lui     $t3, {hi}          # the window: {rounds:,} rounds of {cycles} clk1x\n'
BENCH_LINE = f'        li      $t3, {BENCH_ROUNDS}            # BENCH-ONLY OVERRIDE (the routine has lui $t3, the window)\n'


def hold_text(hi, total, left):
    return f'''\
        # readout-hold: the Pocket now reads the Memory out of the buffer and serves no disc data.
        # The game stays stopped (SPU held, CD frozen) until the read-out is over. rd_seen is bit 1
        # of the SPU_HOLD read: a bridge read of the save window since the last SPU_HOLD write.
        # t1 = windows left. t2: a window without a read ends the wait unless t2 < t1 (unsigned).
        # t2 is {left} (the no-read limit) until a window has a read, then the windows left after
        # that window: the next window without one ends the wait, and so does t1 = 0 (t2 = -1).
rh_hold:
        li      $t1, {total}
        li      $t2, {left}
rh_window:
        sb      $t0, SPU_HOLD($k0)      # (t0 = 1: SPU_HOLD as it is; the write clears rd_seen)
''' + LOAD_LINE.format(hi=f'0x{hi:04X}', rounds=hi << 16, cycles=ROUND_CYCLES) + '''\
rh_wait:
        bnez    $t3, rh_wait
        addiu   $t3, $t3, -1
        lbu     $t0, SPU_HOLD($k0)
        addiu   $t1, $t1, -1
        andi    $t0, $t0, 2
        beqz    $t0, rh_quiet
        sltu    $t4, $t2, $t1           # (no read: go on while t2 < t1)
        addiu   $t2, $t1, -1            # a read
        sltu    $t4, $t2, $t1           # (go on unless the limit in any case is reached)
rh_quiet:
        bnez    $t4, rh_window
        li      $t0, 1
'''


def firmware_text(text, window_s=WINDOW_S, noread_s=NOREAD_S, total_s=TOTAL_S):
    """A routine chain's text (any step's output) with the wait after a save's FINISH. Exact anchor."""
    if 'rh_window' in text:
        raise ValueError(f'{TAG}: the routine already has the step')
    if 'mdec-tap' not in text:
        raise ValueError(f'{TAG}: applies after mdec-tap (SPU_HOLD bit 1 is its tap)')
    head = '        li      $t0, 1\n        sb      $t0, FINISH($k0)\n'
    return once(text, ANCHOR, head + hold_text(*numbers(window_s, noread_s, total_s)) + ANCHOR[len(head):])


def bench_window(text):
    """BENCH-ONLY: the window is BENCH_ROUNDS rounds (same instruction count)."""
    hi, _, _ = numbers()
    return once(text, LOAD_LINE.format(hi=f'0x{hi:04X}', rounds=hi << 16, cycles=ROUND_CYCLES), BENCH_LINE)


# Planted faults: (old, new) on the routine with the step.
FAULTS = {
    # no limit: a Pocket that never stops reading (or never reads) holds the game for ever
    'no-cap': [('        sltu    $t4, $t2, $t1           # (no read: go on while t2 < t1)\n', '        li      $t4, 1                  # FAULT no-cap\n'),
               ('        sltu    $t4, $t2, $t1           # (go on unless the limit in any case is reached)\n', '        li      $t4, 1                  # FAULT no-cap\n')],
    # a load's end runs into the wait too
    'hold-on-load': [('        jal     cd_resume               # Setloc + ReadN / Play, IRQENA back\n        nop\n        b       answer\n        li      $t0, 1\n',
                      '        jal     cd_resume               # Setloc + ReadN / Play, IRQENA back\n        nop\n        li      $t0, 1\n'
                      '        sb      $t0, FINISH($k0)        # FAULT hold-on-load\n        b       rh_hold\n        nop\n')],
    # the exit does not wait for quiet: it leaves at the first window with a read
    'leave-on-read': [('        addiu   $t2, $t1, -1            # a read\n', '        move    $t2, $t1                # FAULT leave-on-read\n')],
    # the clearing write has bit 1: the MDEC table tap is switched on while the game is held
    'tap-on': [('        bnez    $t4, rh_window\n        li      $t0, 1\n', '        bnez    $t4, rh_window\n        li      $t0, 3                  # FAULT tap-on\n')],
}


def fault_text(text, name):
    for old, new in FAULTS[name]:
        text = once(text, old, new)
    return text


def chain_text():
    import prepare_save_compress as cz
    return cz.firmware_text(cz.base_text())


def build(text, output_dir, source='chain'):
    """The routine without and with the step: sizes and the two ROM files; every fault assembles."""
    import prepare_save_rom_ext as ext
    from prepare_save_r1 import stub_bytes
    fw = ext.builder()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = {}
    for name, body in (('before', text), ('readout-hold', firmware_text(text))):
        path = output_dir / f'staged-{name}.S'
        path.write_text(body)
        image, stage, extension = fw.image_parts_ext(source=path)
        rows[name] = dict(source=str(path), source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                          stub_bytes=len(stub_bytes(path)), stage2_bytes=len(stage), free_bytes=fw.free_bytes(image, extension),
                          extension_bytes_used=len(extension.rstrip(b'\0')))
        if name == 'readout-hold':
            (output_dir / 'psx_save_rom40.hex').write_text(fw.rom40_hex(fw.rom1(image), fw.rom2(image)))
            (output_dir / ext.EXT_NAME).write_text(ext.ext_hex(extension))
    for name in FAULTS:
        faulty = output_dir / f'staged-fault-{name}.S'
        faulty.write_text(fault_text(bench_window(firmware_text(text)), name))
        fw.image_parts_ext(source=faulty)
    a, b = rows['before'], rows['readout-hold']
    hi, total, left = numbers()
    report = dict(rows, chain=source, stage2_bytes_added=b['stage2_bytes'] - a['stage2_bytes'],
                  packed_bytes_added=a['free_bytes'] - b['free_bytes'], free_bytes=b['free_bytes'],
                  window_rounds=hi << 16, window_s=(hi << 16) * ROUND_CYCLES / CLK1X, windows_total=total,
                  windows_no_read=total - left,
                  rom40_sha256=hashlib.sha256((output_dir / 'psx_save_rom40.hex').read_bytes()).hexdigest(),
                  ext_sha256=hashlib.sha256((output_dir / ext.EXT_NAME).read_bytes()).hexdigest())
    (output_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    for key in ('stage2_bytes_added', 'packed_bytes_added', 'free_bytes', 'window_s', 'windows_total', 'windows_no_read'):
        print(f'{TAG}: {key} = {report[key]}')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--routine', type=Path, metavar='SRC.S')
    parser.add_argument('--chain', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if bool(args.routine) == args.chain:
            parser.error('one of --routine SRC.S and --chain')
        build(args.routine.read_text() if args.routine else chain_text(), args.output, str(args.routine or 'chain'))
    except ValueError as error:
        raise SystemExit(str(error))
