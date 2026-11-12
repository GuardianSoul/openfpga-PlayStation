#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Exact-voices SPU save port, state format PXS9 (a frozen project step).

The owner's decision of 2026-11-11: the reverb input filter history (reverbL/R_d1-d4, the two
pipeline stages PartA/B and Down, rvLastValid), the reverb work RAM words (port 48-62) and
the DAC output latch (port 7) are no longer saved or restored. After a load the echo may
settle over the game's reverb time; every voice (records, envelope and volume words, the
decoded-sample banks), the pending CD sample, the capture position, the transfer address,
the reverb address and side, the noise generator, the ADSR/repeat write latches, the IRQ
state, ENDX and the current volumes are restored exactly as before.
SPU: the transform of scripts/probe_spu_lean_audio.py --exact-voices (the port the area
probe #1140/#1141 measured): the scalar ring is 8 words at port 32-39.
Routine: the PXS6 routine with its port table reduced to the ring words and a new format
word, "PXS9"; a PXS6 state is refused by the magic check.
Usage: prepare_spu_exact_voices.py SOURCE --output OUT
"""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
MAGIC_OLD = '0x36535850       # "PXS6" (cyclic scalar port, full PXS5 state)'
MAGIC_NEW = '0x39535850       # "PXS9" (exact voices: reverb filter history, work words and DAC latch not kept)'
TABLE_OLD = '.hword  7, 8, 32, 63, 256, 304, 320, 370, 384, 480, 0, 0'
TABLE_NEW = '.hword  32, 40, 256, 304, 320, 370, 384, 480, 0, 0'


def spu_text(text):
    import probe_spu_lean_audio as probe
    probe.EXACT_VOICES = True
    probe.layout()
    if (probe.WORDS, probe.BITS) != (8, 244):
        raise ValueError('exact-voices ring layout changed')
    return probe.lean(text)


def firmware_text(text):
    """text: the PXS6 routine (prepare_spu_ring_paired.firmware_text of staged_handler.S)."""
    from prepare_spu_ring_paired import once
    return once(once(text, MAGIC_OLD, MAGIC_NEW), TABLE_OLD, TABLE_NEW)


def pxs6_firmware():
    from build_staged_save_fw import SRC
    from prepare_spu_ring_paired import firmware_text as pxs6
    return pxs6(SRC.read_text())


def prepare(source, output):
    from prepare_spu_lean_restore import freeze_restore
    source = source.resolve()
    return freeze_restore(source, output, spu_text((source / 'save-states/spu.vhd').read_text()),
                          firmware_text(pxs6_firmware()), 'exact-voices')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    print(prepare(a.source, a.output))
