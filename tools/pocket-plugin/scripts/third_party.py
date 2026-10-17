#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Write THIRD_PARTY_LICENSES.md: every crate built into plugin.wasm, with
the license it is used under and the license texts it ships.

Crates whose package has no license file get the text from licenses/ here
(licenses/<crate>-<file>). Fails when a crate has no license text at all.

  python3 scripts/third_party.py            # from the crate folder
"""
import json
from pathlib import Path
import subprocess
import sys

CRATE = Path(__file__).resolve().parents[1]
NAMES = ('license', 'copying', 'notice', 'unlicense')


def runtime_packages():
    meta = json.loads(subprocess.run(['cargo', 'metadata', '--format-version', '1', '--filter-platform', 'wasm32-wasip1'],
                                     cwd=CRATE, check=True, capture_output=True, text=True).stdout)
    nodes = {n['id']: n for n in meta['resolve']['nodes']}
    root, seen, stack = meta['resolve']['root'], set(), [meta['resolve']['root']]
    while stack:
        node = stack.pop()
        if node in seen: continue
        seen.add(node)
        stack += [d['pkg'] for d in nodes[node]['deps'] if any(k['kind'] is None for k in d['dep_kinds'])]
    return sorted((p for p in meta['packages'] if p['id'] in seen and p['id'] != root), key=lambda p: (p['name'], p['version']))


def texts(package):
    folder = Path(package['manifest_path']).parent
    found = [f for f in sorted(folder.iterdir()) if f.is_file() and f.name.lower().startswith(NAMES)]
    found += sorted((CRATE/'licenses').glob(f"{package['name']}-*")) if not found else []
    return found


def main():
    out, missing = ['# Third-party licenses', '',
                    'plugin.wasm is built from the crates below. Each is listed with its license; where a crate offers',
                    'a choice, any of the listed licenses applies. Their license texts follow.', '',
                    '| Crate | Version | License |', '|---|---|---|'], []
    packages = runtime_packages()
    for p in packages:
        out.append(f"| {p['name']} | {p['version']} | {p['license']} |")
    groups = {}   # identical license texts are printed once, with every crate that ships them
    for p in packages:
        found = texts(p)
        if not found: missing.append(p['name']); continue
        for f in found:
            text = f.read_text(errors='replace').strip()
            groups.setdefault(' '.join(text.split()), [text, []])[1].append(f"{p['name']} {p['version']}")
    for n, (text, crates) in enumerate(groups.values(), 1):
        out += ['', f'## Text {n}', '', 'Used by: ' + ', '.join(sorted(set(crates))), '', '```', text, '```']
    if missing: sys.exit('no license text for: ' + ', '.join(missing))
    (CRATE/'THIRD_PARTY_LICENSES.md').write_text('\n'.join(out) + '\n')
    print(f'{len(packages)} crates')


if __name__ == '__main__':
    main()
