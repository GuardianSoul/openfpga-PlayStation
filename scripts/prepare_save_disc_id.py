#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save states: the mounted disc's identity through a read tap, for the game check of a load
that finds no boot name in the running machine yet (BIOS screens, wake).

Design and results: docs/save-states-finish/final-routine.md (disc identity).
The early-load step's game check compares the kernel's boot name (0x8000B8B0) and accepts when
the running side has none yet: a foreign Memory loaded during the BIOS logo was accepted
(hardware, Crash Bandicoot). The disc itself is the fallback:

Hardware (a frozen project step; two files, no RAM, no clock crossing, no SDC line):
  psx_save_exp1.sv  24 registers on clk1x take words 0 and 1 of the track table that
                    psx_cd_stream writes into cd_top at every mount (trackinfo_write):
                    DISC_ID = {track count [3:0], disc end LBA [19:0]}; 0 after configuration.
                    Read at 0x1828 (bits 7:0), 0x1829, 0x182A; 0x182B reads 0, so a word read
                    gives the identity. (0x1838-0x183B alias; 0x182C-0x182F stay the MDEC tap's.)
  core_top.sv       wiring: the three trackinfo signals that already run from psx_cd_stream to psx.
  Cost, counted from the added logic: 24 registers, two 9-bit address compares, and three
  read bytes (a 4-way byte select) merged into the device's read multiplexer.
  cd_top is not changed: it keeps the track count but not word 1 (its totalLBAs is commented
  out), and its GetTD answers the lead-out in minutes and seconds only.

Routine (firmware_text; a step after early-load, before waits):
  save   DISC_ID into page 0 word 198 (no drive access: nothing between the CD quiet point and
         CD_FREEZE is added).
  load   the boot-name check is as it was. One look at the running name's first word decides:
         no "cdro" there (no boot name yet) sets k1 bit 3. After the state's disc is mounted and
         settled, before the drive is touched and before anything of the game is changed, a
         load with that bit needs DISC_ID = word 198; otherwise the menu's disc is mounted
         again and the load is refused (`bad`). With the disc number already right no early
         answer was given, so the refusal is FINISH 2; after a disc switch the early answer has
         been given and the load silently does not happen, as a failed remount.
  A Memory from before the step has 0 in word 198: refused while there is no running name,
  loaded as before otherwise. A fit without the tap reads 0 and stores 0: as before the step.
  The format magic does not change.

Check: scripts/run_save_disc_id.py.
Usage: prepare_save_disc_id.py SOURCE --output OUTPUT [--seed N]   (frozen project snapshot)
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

TAG = 'save-disc-id'
ID_WORD = 198
ID_ADDRESS = 0x1828


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG}: expected one match, found {text.count(old)}: {old.strip()[:90]!r}')
    return text.replace(old, new)


# ---------------------------------------------------------------------------
# hardware
# ---------------------------------------------------------------------------
EXP1_PORT_OLD = '    input  wire [23:0] mdec_q,          // {scale word, quantisation byte}\n'
EXP1_PORT_NEW = (EXP1_PORT_OLD +
                 f'    // {TAG}: the track table as psx_cd_stream writes it into cd_top at a mount (clk1x). Words 0\n'
                 '    // and 1 are kept: DISC_ID = {track count [3:0], disc end LBA [19:0]}, read at 0x1828-0x182A\n'
                 '    // (0x182B reads 0)\n'
                 '    input  wire        trackinfo_write,\n'
                 '    input  wire [8:0]  trackinfo_addr,\n'
                 '    input  wire [19:0] trackinfo_data,\n')
EXP1_READ_ANCHOR = ('    // register reads: one 16-way byte select over {CD state, GPU ranges}\n')
EXP1_CAPTURE = (f'    // {TAG}\n'
                "    reg [23:0] disc_id = 24'd0;\n"
                '    always @(posedge clk1x) if (trackinfo_write) begin\n'
                "        if (trackinfo_addr == 9'd0) disc_id[23:20] <= trackinfo_data[3:0];\n"
                "        if (trackinfo_addr == 9'd1) disc_id[19:0] <= trackinfo_data[19:0];\n"
                '    end\n'
                "    wire [7:0] disc_id_q = bus_addr[1] ? (bus_addr[0] ? 8'd0 : disc_id[23:16]) : bus_addr[0] ? disc_id[15:8] : disc_id[7:0];\n")
EXP1_MUX_OLD = "                                                    bus_addr[4:0] == 5'd2 ? {7'd0, ss_busy} :\n"
EXP1_MUX_NEW = (f"                                                    bus_addr[3] ? disc_id_q :   // {TAG}\n" + EXP1_MUX_OLD)


def exp1(text):
    if TAG in text or 'disc_id' in text:
        raise ValueError(f'{TAG}: psx_save_exp1.sv already has the tap')
    if 'save-mdec-tap' not in text:
        raise ValueError(f'{TAG}: psx_save_exp1.sv needs the MDEC tap (its read line is the anchor)')
    text = once(text, EXP1_PORT_OLD, EXP1_PORT_NEW)
    text = once(text, EXP1_READ_ANCHOR, EXP1_CAPTURE + EXP1_READ_ANCHOR)
    text = once(text, EXP1_MUX_OLD, EXP1_MUX_NEW)
    return text


CORE_OLD = '        .mdec_tap(save_mdec_tap), .mdec_q(save_mdec_q),\n'
CORE_NEW = (CORE_OLD + '        .trackinfo_write(trackinfo_write), .trackinfo_addr(trackinfo_addr), .trackinfo_data(trackinfo_data[19:0]),   // '
            + TAG + '\n')


def core_top(text):
    if TAG in text:
        raise ValueError(f'{TAG}: core_top.sv already has the wiring')
    for wire in ('wire [31:0] trackinfo_data;', 'wire [8:0] trackinfo_addr;', 'wire trackinfo_write;'):
        if text.count(wire) != 1 or text.index(wire) > text.index(CORE_OLD):
            raise ValueError(f'{TAG}: core_top.sv: {wire} not declared before the save device')
    return once(text, CORE_OLD, CORE_NEW)


# Planted hardware faults for the device bench (never a fitted input): name -> (old, new) on the step's text
HW_FAULTS = {
    # only the low address bits are decoded: every track's end LBA lands in the identity
    'addr-low-bits': ("        if (trackinfo_addr == 9'd1) disc_id[19:0]", "        if (trackinfo_addr[1:0] == 2'd1) disc_id[19:0]"),
    # the track count is never taken
    'no-count': ("        if (trackinfo_addr == 9'd0) disc_id[23:20] <= trackinfo_data[3:0];\n", ''),
    # the read bytes come out in the wrong order
    'bytes-swapped': ('bus_addr[0] ? disc_id[15:8] : disc_id[7:0];', 'bus_addr[0] ? disc_id[7:0] : disc_id[15:8];'),
    # the read sits at 0x182C, under the MDEC tap's
    'read-address': ("bus_addr[3] ? disc_id_q :", "bus_addr[3] && bus_addr[2] ? disc_id_q :"),
}
TRANSFORMS = {'exp1': exp1, 'core_top': core_top}
FILES = (('exp1', 'psx_save_exp1.sv', 'SYSTEMVERILOG_FILE'), ('core_top', 'core_top.sv', 'SYSTEMVERILOG_FILE'))


def hw_fault(text, name):
    return once(text, *HW_FAULTS[name])


def compiled(project, name, kind):
    qsf = (project / 'apf_top.qsf').read_text()
    hits = sorted({l.split('"')[1] for l in qsf.splitlines() if kind in l and l.count('"') >= 2 and l.split('"')[1].endswith('/' + name)})
    if len(hits) != 1 or not Path(hits[0]).is_relative_to(project):
        raise ValueError(f'{TAG}: expected one compiled {name} inside the snapshot, found {hits}')
    return Path(hits[0])


def prepare(source, output, seed=None):
    from measure_pocket_seeds import inputs
    from run_frozen_pocket_fit import hold_guard, load_source, snapshot
    source, output = Path(source).resolve(), Path(output).resolve()
    load_source(source, source / 'fit-inputs.json')
    if seed is None:
        seed = int(re.search(r'(?m)^set_global_assignment -name SEED (\d+)$', (source / 'apf_top.qsf').read_text())[1])
    manifest = snapshot(source, source / 'fit-inputs.json', output, seed)
    before = inputs(output)
    changed = {}
    for kind, name, assignment in FILES:
        path = compiled(output, name, assignment)
        old = path.read_text()
        path.write_text(TRANSFORMS[kind](old))
        changed[str(path)] = (sha(old), sha(path.read_text()))
    current = inputs(output)
    a, b = ({r['path']: r for r in rows} for rows in (current, before))
    if a.keys() != b.keys() or {p for p in b if a[p] != b[p]} != set(changed):
        raise ValueError(f'{TAG}: unexpected input changes')
    hold_guard(output, current)
    manifest['quartus_sources'] = current
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    report = dict(source=str(source), seed=seed, changed={p: dict(before=o, after=n) for p, (o, n) in sorted(changed.items())},
                  firmware_integrated=False,
                  note='the routine ROM is not part of this step: build it with prepare_save_final_routine.py --variant g')
    (output / f'{TAG}.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


# ---------------------------------------------------------------------------
# routine
# ---------------------------------------------------------------------------
EQU_ANCHOR = '        .equ    CD_STATE, 0x1818\n'
EQU = f'''\
        # disc-id (scripts/prepare_save_disc_id.py)
        .equ    DISC_ID, 0x{ID_ADDRESS:04X}         # read: the mounted disc's identity, {{track count [3:0], end LBA [19:0]}}
        .equ    ID_WORD, PAGE+4*{ID_WORD}      # page 0 word {ID_WORD}: DISC_ID at the save (0: a state from before the step)
'''
SAVE_OLD = '''\
        # early-load game-id: the kernel's boot name ("cdrom:\\\\SCUS_941.63;1") into words 193-197
'''
SAVE_NEW = '''\
        lw      $t1, DISC_ID($k0)       # disc-id: the disc's identity into word 198 (below)
''' + SAVE_OLD
STORE_OLD = '''\
        bne     $t6, $t5, 68b
        nop
'''
STORE_NEW = '''\
        bne     $t6, $t5, 68b
        nop
        sw      $t1, ID_WORD($k0)       # disc-id
'''
FLAG_OLD = '''\
        lw      $t0, 0($t7)
        lw      $t1, 0($t6)
        bne     $t0, $t4, 75f
        lbu     $t2, 11($t7)
'''
FLAG_NEW = '''\
        lw      $t0, 0($t7)
        lw      $t1, 0($t6)
        ori     $k1, $k1, 8             # disc-id: k1 bit 3 = no boot name in the running machine (yet):
        bne     $t0, $t4, 75f           # the disc's identity decides, after the mount
        lbu     $t2, 11($t7)
        xori    $k1, $k1, 8             # disc-id: a running name: the name check alone, as before
'''
MOUNT_OLD = '''\
        jal     disc_mount
        move    $a0, $s6
        beqz    $v0, 72f
        nop
        jal     disc_mount              # not mounted (wrong disc or no answer in time): the menu's
        move    $a0, $s5                # disc again, so DISC names the disc in the drive, and no load
        b       bad
        nop
72:
'''
MOUNT_NEW = '''\
        jal     disc_mount
        move    $a0, $s6
        bnez    $v0, 70f
        andi    $t0, $k1, 8             # disc-id: without a running name the mounted disc must be
        beqz    $t0, 72f                # the state's (word 198; 0 in a state from before the step)
        lw      $t0, DISC_ID($k0)
        lw      $t1, ID_WORD($k0)
        nop
        beq     $t0, $t1, 72f           # disc-id: the state's disc
        nop
70:     jal     disc_mount              # not mounted (wrong disc or no answer in time) or not the state's
        move    $a0, $s5                # disc: the menu's disc again, so DISC names the disc in the drive,
        b       bad                     # and no load
        nop
72:
'''

# Planted routine faults (never a fitted input)
FAULTS = {
    # the identity is not compared: a foreign Memory loads during the BIOS screens (the hardware defect)
    'id-compare-removed': ('        beq     $t0, $t1, 72f           # disc-id: the state\'s disc\n', '        b       72f                     # FAULT: identity not compared\n'),
    # the save stores another register (the CD state word) as the identity
    'id-wrong-register': ('        lw      $t1, DISC_ID($k0)       # disc-id: the disc\'s identity into word 198 (below)\n',
                          '        lw      $t1, CD_STATE($k0)      # FAULT: not the identity register\n'),
    # the flag is cleared although there is no running name: today's routine
    'flag-never-set': ('        ori     $k1, $k1, 8             # disc-id: k1 bit 3', '        nop                             # FAULT: k1 bit 3'),
}


def firmware_text(text):
    """Routine c2 (big build -> mdec-tap -> early-load) with the step. Exact anchors; a miss raises."""
    if 'disc-id' in text:
        raise ValueError(f'{TAG}: the routine already has the step')
    if 'early-load game-id' not in text or 'early-load ready' not in text:
        raise ValueError(f'{TAG}: the routine needs the early-load step (game-id, ready)')
    if 'wait_dead' in text or 'cz_save' in text:
        raise ValueError(f'{TAG}: the step applies before waits and compress')
    if re.search(r'^70:|[ ,]70[fb]\b', text, flags=re.M):
        raise ValueError(f'{TAG}: numeric label 70 is in use')
    text = once(text, EQU_ANCHOR, EQU_ANCHOR + EQU)
    text = once(text, SAVE_OLD, SAVE_NEW)
    text = once(text, STORE_OLD, STORE_NEW)
    text = once(text, FLAG_OLD, FLAG_NEW)
    text = once(text, MOUNT_OLD, MOUNT_NEW)
    return text


def fault_text(text, name):
    return once(text, *FAULTS[name])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', type=int)
    args = parser.parse_args()
    try:
        result = prepare(args.source, args.output, args.seed)
    except ValueError as error:
        raise SystemExit(str(error))
    for path, hashes in result['changed'].items():
        print(f"{TAG}: patched {path} (sha256 {hashes['before'][:16]} -> {hashes['after'][:16]})")
    print(f'{TAG}: 24 registers, no RAM, no SDC change')
