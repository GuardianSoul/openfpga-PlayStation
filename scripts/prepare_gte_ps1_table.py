#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""0.6.0 timing step "gte-ps1-table" on a frozen snapshot (docs/integration-060.md).

PSX: GTE and MDEC timing, stage 1 (branch claude/gte-mdec-timing-591212, 030d2e7c,
docs/timing-gte-mdec.md). GTE command durations follow the PS1 counts (psx-spx): the snapshot's
GTEMicrocodeStart.sv gets its MicroCodeTiming.inl include inlined, with every row set to
PS1 count - 2 (the adapter adds 2 CPU cycles). That is 23 constants in an existing case ROM.
Values stay bit-identical; only guest-visible command timing changes (intended). This is the same
text as that branch's prepare_gte_adapter.microcode_start_text('ps1'), built here from the
snapshot's own copies of the two upstream files.
Checks (that thread): make test-gte-cycle-timing, lockstep 0 of 17,034 stores, adapter / CPU-GTE /
multicycle / m10k / reference / save checks PASS, planted faults caught.
Usage: prepare_gte_ps1_table.py SOURCE --output OUTPUT [--seed N]
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

TAG = 'gte-ps1-table'
START = 'gte/rtl/GTEMicrocodeStart.sv'
PS1_COUNTS = {0x01: 15, 0x02: 8, 0x06: 8, 0x0c: 6, 0x10: 8, 0x11: 8, 0x12: 8, 0x13: 19, 0x14: 13,
              0x16: 44, 0x1b: 17, 0x1c: 11, 0x1e: 14, 0x20: 30, 0x28: 5, 0x29: 8, 0x2a: 17,
              0x2d: 5, 0x2e: 6, 0x30: 23, 0x3d: 5, 0x3e: 5, 0x3f: 39}
ADAPTER_OVERHEAD = 2


def timing_table(upstream_text):
    out, seen = [], set()
    for line in upstream_text.splitlines():
        m = re.match(r"6'h([0-9a-f]+) : retCount = 6'd(\d+);(.*)$", line)
        if not m:
            out.append(line)
            continue
        op = int(m.group(1), 16)
        seen.add(op)
        out.append(f"6'h{m.group(1)} : retCount = 6'd{PS1_COUNTS[op] - ADAPTER_OVERHEAD};{m.group(3)}")
    if seen != set(PS1_COUNTS):
        raise ValueError(f'{TAG}: GTE timing table rows changed')
    return '\n'.join(out) + '\n'


def start_text(text, inl_text):
    marker = '`include "MicroCodeTiming.inl"'
    if text.count(marker) != 1:
        raise ValueError(f'{TAG}: GTE timing include changed')
    return ('// Generated (gte-ps1-table): MicroCodeTiming.inl inlined with PS1 command counts; original license follows.\n'
            + text.replace(marker, timing_table(inl_text)))


def prepare(source, output, seed=None):
    import prepare_save_checksum as cs
    manifest = json.loads((Path(source) / 'fit-inputs.json').read_text())
    inl = [Path(r['path']) for r in manifest['quartus_sources'] if r['path'].endswith('gte/rtl/MicroCodeTiming.inl')]
    if len(inl) != 1:
        raise ValueError(f'{TAG}: {len(inl)} MicroCodeTiming.inl in {source}')
    inl_text = inl[0].read_text()
    return cs.prepare(source, output, seed, {START: lambda t: start_text(t, inl_text)})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', type=int)
    args = parser.parse_args()
    try:
        seed, changed = prepare(args.source, args.output, args.seed)
    except ValueError as error:
        raise SystemExit(str(error))
    print(f'{TAG}: snapshot {args.output.resolve()} (seed {seed})')
    for path, (old, new) in changed.items():
        print(f'{TAG}: patched {path} (sha256 {old[:16]} -> {new[:16]})')
