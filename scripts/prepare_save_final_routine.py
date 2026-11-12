#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""The save routines of the 0.5.9 release candidate (docs/save-states-finish/final-routine.md).

  g  candidate E3x (extension ROM), the release routine:
     big build -> mdec-tap -> early-load (c2) -> disc-id -> waits (G_WAITS) -> rom-ext loader -> compress
     (the fit needs the disc identity tap of prepare_save_disc_id.py; on a fit without it the
     identity reads 0 and the game check is as in routine c2)
  f  candidate E3m (no extension ROM), the fallback without compression:
     big build -> mdec-tap -> early-load (c2) -> waits without its fifo-check and scratchpad parts
     (no room); no disc identity (no room, and E3m has no tap)
Neither has the size-mib step: the size register gets the pages of the state as they are
(g: 2 + the stream's pages).

Usage: prepare_save_final_routine.py --variant f|g --output OUT
       (source, psx_save_rom40.hex, g: psx_save_rom_ext.hex, report.json; every planted fault of
        the steps is applied and assembled)
SAVE_FINAL_ROUTINE=f|g in the environment makes the routine-model benches of the earlier steps
(run_save_mdec_tap.py, run_save_early_load.py) run on this chain: tail_text().
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

VARIANTS = ('f', 'g')
CHAIN = {'f': ('big-build', 'mdec-tap', 'early-load', 'waits'),
         'g': ('big-build', 'mdec-tap', 'early-load', 'disc-id', 'waits', 'rom-ext', 'compress')}
# waits parts left out: f has no room for them; g keeps room for the step that follows
# compress (readout hold, 40 packed bytes asked for)
LEFT_OUT = {'f': ('fifo-check', 'scratchpad'), 'g': ('fifo-check',)}


def waits_parts(variant):
    import prepare_save_waits as waits
    return tuple(p for p in waits.PARTS if p not in LEFT_OUT[variant])


def after_early_load(text, variant):
    """Routine c2's text (or a planted-fault copy of it) -> the final routine."""
    import prepare_save_waits as waits
    if variant == 'g':
        import prepare_save_disc_id as disc_id
        text = disc_id.firmware_text(text)
    text = waits.firmware_text(text, waits_parts(variant))
    if variant == 'g':
        import prepare_save_rom_ext as ext
        import prepare_save_compress as compress
        text = compress.firmware_text(ext.firmware_text(text))
    elif variant != 'f':
        raise ValueError('variant: f or g')
    return text


def stages(variant):
    """[(name, text)]: the chain, one entry per step."""
    import prepare_save_early_load as early
    import prepare_save_mdec_tap as tap
    import prepare_save_waits as waits
    rows = [('big-build', tap.big_build_text())]
    rows.append(('mdec-tap', tap.firmware_text(rows[-1][1])))
    rows.append(('early-load', early.firmware_text(rows[-1][1], early.PARTS, early.READY_ROUNDS)))
    if variant == 'g':
        import prepare_save_disc_id as disc_id
        rows.append(('disc-id', disc_id.firmware_text(rows[-1][1])))
    rows.append(('waits', waits.firmware_text(rows[-1][1], waits_parts(variant))))
    if variant == 'g':
        import prepare_save_rom_ext as ext
        import prepare_save_compress as compress
        rows.append(('rom-ext', ext.firmware_text(rows[-1][1])))
        rows.append(('compress', compress.firmware_text(rows[-1][1])))
    if tuple(n for n, _ in rows) != CHAIN[variant]:
        raise ValueError('chain table out of step')
    return rows


def firmware_text(variant):
    return stages(variant)[-1][1]


def tail_text(text, stage):
    """For the routine-model benches of the earlier steps: with SAVE_FINAL_ROUTINE set, the routine
    text after `stage` ('mdec-tap' or 'early-load', possibly with a planted fault of that step) goes
    on through the rest of the chain. Without it the text comes back as it is."""
    variant = os.environ.get('SAVE_FINAL_ROUTINE')
    if not variant:
        return text
    if stage == 'mdec-tap':
        import prepare_save_early_load as early
        text = early.firmware_text(text, early.PARTS, early.READY_ROUNDS)
    elif stage != 'early-load':
        raise ValueError('tail_text: stage')
    return after_early_load(text, variant)


def faults(variant):
    """{step: {fault: text}}: every planted fault of the steps on this routine."""
    import prepare_save_early_load as early
    import prepare_save_waits as waits
    from prepare_save_mdec_tap import FAULTS as tap_faults, fault_text as tap_fault
    text = firmware_text(variant)
    found = {'mdec-tap': {name: tap_fault(text, name) for name in tap_faults if tap_faults[name][0] in text},
             'early-load': {name: early.fault_text(text, name) for name in early.FAULTS},
             'waits': {name: waits.fault_text(text, name) for name in waits.FAULTS if waits.FAULT_PART[name] in waits_parts(variant)}}
    if variant == 'g':
        import prepare_save_compress as compress
        import prepare_save_disc_id as disc_id
        found['disc-id'] = {name: disc_id.fault_text(text, name) for name in disc_id.FAULTS}
        found['compress'] = {name: compress.fault_text(text, name) for name in compress.FAULTS if compress.FAULTS[name][0] in text}
    return found


def build(variant, output_dir):
    import prepare_save_rom_ext as ext
    import prepare_save_waits as waits
    from prepare_save_r1 import stub_bytes
    fw = ext.builder()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
    rows = []
    for name, text in stages(variant):
        source = output_dir / f'staged-{len(rows)}-{name}.S'
        source.write_text(text)
        note = None
        if 'rom_ext' in text:
            image, stage, extension = fw.image_parts_ext(source=source)     # stops if the packed stage 2 does not fit
        else:
            try:
                (image, stage), extension = fw.image_parts(source=source), None
            except SystemExit as error:
                if variant != 'g':
                    raise
                # a step of the E3x chain that does not fit E3m's ROM: sized with the loader (24 more stub bytes)
                note = f'does not fit without the extension ROM ({error}); sized with the rom-ext loader'
                sized = output_dir / f'sized-{len(rows)}-{name}.S'
                sized.write_text(ext.firmware_text(text))
                image, stage, extension = fw.image_parts_ext(source=sized)
        rows.append(dict(step=name, source_sha256=sha(source), stub_bytes=len(stub_bytes(source)), stage2_bytes=len(stage),
                         packed_bytes=len(fw.lz_pack(stage)) + 8, free_bytes=fw.free_bytes(image, extension), **({'note': note} if note else {})))
    source = output_dir / f'staged-final-{variant}.S'
    source.write_text(firmware_text(variant))
    if rows[-1]['source_sha256'] != sha(source):
        raise ValueError('the chain is not reproducible')
    rom = output_dir / 'psx_save_rom40.hex'
    rom.write_text(fw.rom40_hex(fw.rom1(image), fw.rom2(image)))
    report = dict(variant=variant, chain=list(CHAIN[variant]), waits_parts=list(waits_parts(variant)),
                  waits_parts_left_out=[p for p in waits.PARTS if p not in waits_parts(variant)],
                  source=source.name, source_sha256=sha(source), rom40='psx_save_rom40.hex', rom40_sha256=sha(rom),
                  stub_bytes=rows[-1]['stub_bytes'], stage2_bytes=rows[-1]['stage2_bytes'], packed_bytes=rows[-1]['packed_bytes'],
                  free_bytes=rows[-1]['free_bytes'], steps=rows)
    if extension is not None:
        hexfile = output_dir / ext.EXT_NAME
        hexfile.write_text(ext.ext_hex(extension))
        report.update(extension=ext.EXT_NAME, extension_sha256=sha(hexfile), extension_bytes_used=len(extension.rstrip(b'\0')))
    planted = {}
    for step, table in faults(variant).items():
        for name, text in table.items():
            faulty = output_dir / f'fault-{step}-{name}.S'
            faulty.write_text(text)
            (fw.image_parts_ext if 'rom_ext' in text else fw.image_parts)(source=faulty)
            planted.setdefault(step, []).append(name)
    bench = output_dir / 'bench-bound.S'
    bench.write_text(waits.bench_bound(source.read_text()))
    if len((fw.image_parts_ext if variant == 'g' else fw.image_parts)(source=bench)[1]) != report['stage2_bytes']:
        raise ValueError('the bench bound changed the length of stage 2')
    report['faults_assembled'] = planted
    (output_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print('step          stub  stage 2  packed  free ROM bytes')
    for row in rows:
        print(f"{row['step']:12s} {row['stub_bytes']:5d} {row['stage2_bytes']:8d} {row['packed_bytes']:7d} {row['free_bytes']:6d}")
    print(f"source sha256: {report['source_sha256']}\nROM sha256:    {report['rom40_sha256']}")
    if extension is not None:
        print(f"extension:     {report['extension_sha256']} ({report['extension_bytes_used']} bytes used)")
    return report


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--variant', choices=VARIANTS, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    try:
        build(args.variant, args.output)
    except ValueError as error:
        raise SystemExit(str(error))
