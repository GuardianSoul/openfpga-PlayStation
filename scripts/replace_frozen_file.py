#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Replace the one assigned source with a given basename in a frozen project.
--expect SHA256 guards the file being replaced (e.g. the new file's parent).
Usage: replace_frozen_file.py NAME NEW_SOURCE [--expect SHA256] PROJECT
"""
import argparse
import hashlib
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument('name')
ap.add_argument('source', type=Path)
ap.add_argument('--expect')
ap.add_argument('project', type=Path)
args = ap.parse_args()
hits = {line.split('"')[1] for line in (args.project / 'apf_top.qsf').read_text().splitlines()
        if f'/{args.name}"' in line}
if len(hits) != 1:
    raise SystemExit(f'expected one assigned {args.name}, found {sorted(hits)}')
target = Path(hits.pop())
if args.expect and hashlib.sha256(target.read_bytes()).hexdigest() != args.expect:
    raise SystemExit(f'{target}: not the expected original')
target.write_bytes(args.source.read_bytes())
print(target)
