#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Queued CPU writes and the DMA FIFO head reuse an already open main-RAM row.

psx_sdram_ctrl classifies a CPU write that had to wait (ch2_adr_q) and the DMA
FIFO head (stf) with a bank-only check, so an open matching row counts as a
miss and is closed and reopened (19aed673 chose that for a ba[] setup path that
later rewrites removed). This step reuses the existing `row_state` compare on
those held addresses (Codex's b301b3fc, docs/pocket-sdram-write-hits.md); a
true row conflict still precharges. The lookahead rig's controller has it, and
without it the release controller loses Peip SWFIFO6S..9S (same-address stores)
in the rig (docs/timing-cpu-mem.md). Guest-visible on purpose.
Usage: prepare_sdram_write_hits.py PROJECT
(post-prepare step; psx_sdram_ctrl.sv moves to PROJECT/sdram-write-hits/)
"""
import argparse
import re
from pathlib import Path

TAG = 'sdram-write-hits'
EDITS = (
    ('fifo head', 'stf <= bank_state(head_adr);', 'stf <= row_state(head_adr);'),
    ('fifo load', 'stf <= bank_state(dmafifo_adr);', f'stf <= row_state(dmafifo_adr); // {TAG}'),
    ('comment', '// A write that has to wait only checks its bank (no row compare).',
     f'// {TAG}: a queued write stays eligible for the row it actually addresses.'),
    ('ch2', 'row_state(ch2_addr) : bank_state(ch2_adr_q);', 'row_state(ch2_addr) : row_state(ch2_adr_q);'),
)


def controller_text(text):
    if TAG in text:
        raise ValueError('controller already carries ' + TAG)
    for what, old, new in EDITS:
        if text.count(old) != 1:
            raise ValueError(f'{what}: expected exactly one match, found {text.count(old)}')
        text = text.replace(old, new)
    return text


def apply(project):
    project = Path(project).resolve()
    qsf = project / 'apf_top.qsf'
    text = qsf.read_text()
    found = re.findall(r'SYSTEMVERILOG_FILE "([^"]*/psx_sdram_ctrl\.sv)"', text)
    if len(found) != 1:
        raise SystemExit(f'{TAG}: expected one psx_sdram_ctrl.sv, found {found}')
    out = project / TAG
    out.mkdir(exist_ok=False)
    new = out / 'psx_sdram_ctrl.sv'
    new.write_text(controller_text(Path(found[0]).read_text()))
    qsf.write_text(text.replace(found[0], str(new)))
    return new


if __name__ == '__main__':
    ap = argparse.ArgumentParser(usage=__doc__.split('Usage: ')[1])
    ap.add_argument('project')
    print('patched', apply(ap.parse_args().project))
