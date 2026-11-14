#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""0.6.0 step "ram-wait-set" on a frozen snapshot (docs/integration-060.md).

PSX: CPU and memory timing (branch claude/cpu-mem-timing-077640, 3ba1c4f1, docs/timing-cpu-mem.md):
the release-form RAM-wait steps 1-6 in that thread's order, composed per file. prepare_sdram_chain_row
follows as its own step (it must be last on psx_sdram_ctrl.sv).
  memorymux.vhd       load_window.memorymux_text
  psx_sdram_ctrl.sv   ram_miss_return -> dma_miss_return -> sdram_write_hits -> ram_read_hint ->
                      sdram_early_refresh (controller_text each)
  psx_top.vhd, psx_mister_psx_top_resource.vhd, core_top.sv, psx_core_memory.sv: ram_read_hint
Checks (that thread): golden bench and planted mutants per step; native rig 149 -> 160 of 167 rows.
Usage: prepare_ram_wait_set.py SOURCE --output OUTPUT [--seed N] [--skip dma-return]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

TAG = 'ram-wait-set'


def chain(*fns):
    def run(text):
        for fn in fns:
            text = fn(text)
        return text
    return run


STEPS = ('load-window', 'ram-return', 'dma-return', 'write-hits', 'read-hint', 'early-refresh')


def transforms(skip=()):
    import prepare_dma_miss_return as dmr
    import prepare_load_window as lw
    import prepare_ram_miss_return as rmr
    import prepare_ram_read_hint as hint
    import prepare_sdram_early_refresh as er
    import prepare_sdram_write_hits as wh
    bad = set(skip) - set(STEPS)
    if bad:
        raise ValueError(f'{TAG}: unknown steps {sorted(bad)}')
    ctrl = [f for name, f in (('ram-return', rmr.controller_text), ('dma-return', dmr.controller_text),
                              ('write-hits', wh.controller_text), ('read-hint', hint.controller_text),
                              ('early-refresh', er.controller_text)) if name not in skip]
    out = {'psx_sdram_ctrl.sv': chain(*ctrl)}
    if 'load-window' not in skip:
        out['memorymux.vhd'] = lw.memorymux_text
    if 'read-hint' not in skip:
        out.update({'psx_top.vhd': hint.psx_top_text, 'psx_mister_psx_top_resource.vhd': hint.resource_text,
                    'core_top.sv': hint.core_top_text, 'psx_core_memory.sv': hint.core_memory_text})
    return out


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('source', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--seed', type=int)
    p.add_argument('--skip', default='', help=f'comma list of steps to leave out: {",".join(STEPS)}')
    a = p.parse_args()
    import prepare_save_checksum as cs
    try:
        seed, changed = cs.prepare(a.source, a.output, a.seed, transforms([s for s in a.skip.split(',') if s]))
    except ValueError as error:
        raise SystemExit(str(error))
    print(f'{TAG}: snapshot {a.output.resolve()} (seed {seed})')
    for path, (old, new) in changed.items():
        print(f'{TAG}: patched {path} (sha256 {old[:16]} -> {new[:16]})')
