#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Apply a timing thread's text transform to a frozen snapshot (docs/integration-060.md).

Many 0.6.0 steps are written as `prepare_X.py PROJECT` (post-prepare) around a pure text function, e.g.
prepare_sdram_chain_row.controller_text. This applies such a function to one file of a frozen snapshot
through prepare_save_checksum.prepare (snapshot, only that file changes, manifest rewritten and
reloaded). The checks are the owning thread's (equivalence bench, mutations).
Usage: prepare_frozen_text.py SOURCE --output OUTPUT --module prepare_sdram_chain_row
         --func controller_text --file psx_sdram_ctrl.sv [--seed N]
       (--file may be a path suffix such as mirlo-sdram/psx_sdram_ctrl.sv when the name is not unique)
"""
import argparse
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('source', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--module', required=True)
    p.add_argument('--func', required=True)
    p.add_argument('--file', required=True)
    p.add_argument('--seed', type=int)
    a = p.parse_args()
    fn = getattr(importlib.import_module(a.module), a.func)
    import prepare_save_checksum as cs
    try:
        seed, changed = cs.prepare(a.source, a.output, a.seed, {a.file: fn})
    except ValueError as error:
        raise SystemExit(str(error))
    print(f'{a.module}.{a.func}: snapshot {a.output.resolve()} (seed {seed})')
    for path, (old, new) in changed.items():
        print(f'{a.module}.{a.func}: patched {path} (sha256 {old[:16]} -> {new[:16]})')
