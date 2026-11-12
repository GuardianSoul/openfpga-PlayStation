#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine step "drive-state" (docs/save-states-finish/drive-state.md): how a load restarts the
CD drive (cd_resume, msf). ROM only; chains after any step (it touches only cd_resume and msf).

1. msf without the +150. The tap's LBA (0x181C-0x181E, cd_top currentLBA) is the MSF position in
   sectors, 00:02:00 = 150, as Setloc gives it (seekLBA = minute * 4500 + second * 75 + sector) and
   as the drive asks the Pocket for it (cd_hps_lba). msf added 150 again, so every load of a state
   saved while reading or playing restarted 150 sectors (2 s) after the sector the game waits for
   (drive-state bench: WRONGLBA, first sector = want + 150). One instruction less.
2. A state saved with the drive idle (not reading, not playing) whose position is known (motor on,
   LBA not 0): SeekL to that position, its second response taken. After a wake the drive starts at
   LBA 0; a game that sends ReadN without a Setloc (reading on after a Pause) read from there. The
   seek also uses no Setloc for longer than the command, so a later save is not held off by the
   save-drive-state RTL step (which makes a pending Setloc busy).

Usage: prepare_save_drive_state_routine.py --routine SRC.S --output DIR   (the routine with the step,
           both ROM files, report.json with the packed bytes added; SRC.S is the chain before it)
       prepare_save_drive_state_routine.py --chain --output DIR          (this checkout's chain)
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

TAG = 'drive-state'


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG} anchor: expected one match, found {text.count(old)}: {old[:90]!r}')
    return text.replace(old, new)


MSF_OLD = '''\
msf:
        sll     $t0, $s6, 8
        srl     $t0, $t0, 8
        addiu   $t0, $t0, 150
'''
MSF_NEW = '''\
msf:                                    # drive-state: the tap's LBA is the MSF position (00:02:00 = 150)
        sll     $t0, $s6, 8
        srl     $t0, $t0, 8
'''
RESUME_OLD = '''\
        andi    $t1, $t0, 0x20
        bnez    $t1, 1f
        andi    $t1, $t0, 0x80
        beqz    $t1, 3f
        li      $s3, 0x03               # Play
        b       2f
        nop
1:      addiu   $s6, $s6, 1
        li      $s3, 0x06               # ReadN
2:      jal     msf
        nop
        jal     cd_cmd
        li      $a0, 0x302              # Setloc
        jal     cd_cmd
        move    $a0, $s3
3:      lui     $t9, 0x1F80
'''
RESUME_NEW = '''\
        andi    $t1, $t0, 0x20
        bnez    $t1, 1f
        andi    $t1, $t0, 0x80
        bnez    $t1, 2f
        li      $s3, 0x03               # Play
        andi    $t1, $t0, 0x02          # drive-state: idle, motor on, a position: SeekL there
        sll     $t2, $s6, 8             # (LBA bits 23:0 not 0)
        beqz    $t1, 3f
        li      $s3, 0x15               # SeekL
        bnez    $t2, 2f
        nop
        b       3f
        nop
1:      addiu   $s6, $s6, 1
        li      $s3, 0x06               # ReadN
2:      jal     msf
        nop
        jal     cd_cmd
        li      $a0, 0x302              # Setloc
        jal     cd_cmd
        move    $a0, $s3
        li      $t0, 0x15
        bne     $s3, $t0, 3f
        lui     $a2, 0x0010
        jal     cd_wait                 # SeekL's second response
        nop
3:      lui     $t9, 0x1F80
'''

# Planted faults: (old, new) on the routine with the step.
FAULTS = {
    # the +150 back
    'plus150': [(MSF_NEW, MSF_OLD.replace('msf:\n', 'msf:                                    # FAULT plus150\n'))],
    # an idle drive is left where the wake put it
    'no-seek': [('        bnez    $t2, 2f\n        nop\n        b       3f\n', '        b       3f                      # FAULT no-seek\n        nop\n        b       3f\n')],
    # the SeekL's second response is left for the game
    'no-wait': [('        jal     cd_wait                 # SeekL\'s second response\n', '        nop                             # FAULT no-wait\n')],
    # reading resumes at the saved sector instead of the next one
    'same-sector': [('1:      addiu   $s6, $s6, 1\n', '1:      nop                             # FAULT same-sector\n')],
}


def firmware_text(text, part='all'):
    """A routine chain's text with the step (part 'msf': only part 1; 'seek': only part 2, on a text
    that has part 1 already). Exact anchors."""
    if part == 'seek':
        if '# drive-state: idle' in text:
            raise ValueError(f'{TAG}: the routine already has part 2')
        return once(text, RESUME_OLD, RESUME_NEW)
    if '# drive-state:' in text:
        raise ValueError(f'{TAG}: the routine already has the step')
    text = once(text, MSF_OLD, MSF_NEW)
    return text if part == 'msf' else once(text, RESUME_OLD, RESUME_NEW)


def fault_text(text, name):
    for old, new in FAULTS[name]:
        text = once(text, old, new)
    return text


def chain_text():
    import prepare_save_compress as cz
    return cz.firmware_text(cz.base_text())


def stage2_bytes(path):
    """Unpacked stage 2 bytes of a routine source (assembled and linked as the ROM build does)."""
    import build_staged_save_fw as b
    from tool_paths import find_tool
    mc, ld, objcopy = (find_tool(x) for x in ('llvm-mc', 'ld.lld', 'llvm-objcopy'))
    with tempfile.TemporaryDirectory() as tmp:
        obj, elf, script, out = (Path(tmp) / n for n in ('h.o', 'h.elf', 'h.ld', 's.bin'))
        script.write_text(b.LINK)
        subprocess.run([mc, '-triple=mipsel', '-mcpu=mips1', '-filetype=obj', str(path), '-o', str(obj)], check=True)
        subprocess.run([ld, '-m', 'elf32ltsmip', '-T', str(script), '-e', 'entry', '--no-dynamic-linker', '-o', str(elf), str(obj)], check=True)
        subprocess.run([objcopy, '-O', 'binary', '-j', '.stage2', str(elf), str(out)], check=True)
        return len(out.read_bytes())


def build(text, output_dir, source='chain', part='all'):
    """The routine without and with the step: sizes and the two ROM files; every fault assembles."""
    import prepare_save_rom_ext as ext
    from prepare_save_r1 import stub_bytes
    fw = ext.builder()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = {}
    for name, body in (('before', text), (TAG, firmware_text(text, part))):
        path = output_dir / f'staged-{name}.S'
        path.write_text(body)
        rows[name] = row = dict(source=str(path), source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                stub_bytes=len(stub_bytes(path)), stage2_bytes=stage2_bytes(path))
        try:
            image, stage, extension = fw.image_parts_ext(source=path)
        except SystemExit as error:          # the ROM is too small: how much (packed bytes over)
            m = re.search(r'packed stage 2 is (\d+) bytes, the ROM holds (\d+)', str(error))
            if not m: raise
            row['free_bytes'] = int(m[2]) - int(m[1])
            continue
        row.update(free_bytes=fw.free_bytes(image, extension), extension_bytes_used=len(extension.rstrip(b'\0')))
        if name == TAG:
            (output_dir / 'psx_save_rom40.hex').write_text(fw.rom40_hex(fw.rom1(image), fw.rom2(image)))
            (output_dir / ext.EXT_NAME).write_text(ext.ext_hex(extension))
    for name in FAULTS if part == 'all' else ('plus150',):
        faulty = output_dir / f'staged-fault-{name}.S'
        faulty.write_text(fault_text(firmware_text(text, part), name))
        stage2_bytes(faulty)                 # assembles
    a, b = rows['before'], rows[TAG]
    hexes = {k: hashlib.sha256((output_dir / f).read_bytes()).hexdigest()
             for k, f in (('rom40_sha256', 'psx_save_rom40.hex'), ('ext_sha256', ext.EXT_NAME)) if (output_dir / f).exists()}
    report = dict(rows, chain=source, part=part, stage2_bytes_added=b['stage2_bytes'] - a['stage2_bytes'],
                  packed_bytes_added=a['free_bytes'] - b['free_bytes'], free_bytes=b['free_bytes'],
                  fits=b['free_bytes'] >= 0, **hexes)
    (output_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    for key in ('stage2_bytes_added', 'packed_bytes_added', 'free_bytes', 'fits'):
        print(f'{TAG}: {key} = {report[key]}')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--routine', type=Path, metavar='SRC.S')
    parser.add_argument('--chain', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--part', choices=('all', 'msf'), default='all', help='msf: only part 1 (the +150)')
    args = parser.parse_args()
    try:
        if bool(args.routine) == args.chain:
            parser.error('one of --routine SRC.S and --chain')
        build(args.routine.read_text() if args.routine else chain_text(), args.output, str(args.routine or 'chain'), args.part)
    except ValueError as error:
        raise SystemExit(str(error))
