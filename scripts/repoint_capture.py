#!/usr/bin/env python3
"""Copy a fit capture to the path its QSF and release-timing.json name, inside this checkout.

Jenkins fits run in a job workspace (``.../psx-fit@N/src/build/fits/<name>``); the
capture under ``~/probecap`` still names that path in apf_top.qsf, the SDC files,
release-timing.json and the generated sources. ``package_release.py`` (timing's
project must be the package's project) and ``release_source.py`` (the QSF must
resolve) need the project where it says it is. This makes
``<checkout>/build/fits/<name>`` with hard links for every file that does not
name the workspace and rewritten copies (workspace checkout -> this checkout)
for those that do. The capture itself is not touched.

  python3 scripts/repoint_capture.py ~/probecap/<capture>   # prints the new project path
"""
import argparse, hashlib, json, os, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--root', type=Path, default=ROOT, help='checkout the copy goes into (default: this one)')
    args = parser.parse_args()
    capture, root = args.capture.resolve(), args.root.resolve()
    timing = json.loads((capture/'release-timing.json').read_text())
    old_project = timing['project'].rstrip('/')
    marker = '/build/fits/'
    if marker not in old_project: sys.exit(f'{old_project}: not a build/fits project path')
    old_root, rel = old_project.split(marker, 1)
    dest = root/'build/fits'/rel
    if dest.exists():
        # A copy made earlier from the same capture is reused (make release runs this every time).
        try: moved = json.loads((dest/'release-timing.json').read_text())
        except (OSError, ValueError): moved = {}
        if moved.get('repointed_from') == {'project': old_project, 'qsf_sha256': timing.get('qsf_sha256')}:
            print(dest); return
        sys.exit(f'{dest} exists and is not a repointed copy of {capture}; remove it first')
    if dest.resolve() == capture: sys.exit('capture is already at its own path')
    old, new = old_root.encode(), str(root).encode()
    rewritten = 0
    for src in sorted(capture.rglob('*')):
        out = dest/src.relative_to(capture)
        if src.is_dir() and not src.is_symlink():
            out.mkdir(parents=True, exist_ok=True); continue
        out.parent.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes() if src.stat().st_size < 64 << 20 else b''
        if old in data and b'\0' not in data[:8192]:
            out.write_bytes(data.replace(old, new)); shutil.copystat(src, out); rewritten += 1
        else:
            try: os.link(src, out)
            except OSError: shutil.copy2(src, out)
    # release-timing.json pins the QSF by hash: carry the pin over only when it held for the capture.
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    if timing.get('qsf_sha256') != sha(capture/'apf_top.qsf'): sys.exit('release-timing.json does not match the capture\'s apf_top.qsf')
    moved = json.loads((dest/'release-timing.json').read_text())
    moved['qsf_sha256'] = sha(dest/'apf_top.qsf'); moved['repointed_from'] = {'project': old_project, 'qsf_sha256': timing['qsf_sha256']}
    (dest/'release-timing.json').unlink(); (dest/'release-timing.json').write_text(json.dumps(moved, indent=2) + '\n')
    assert Path(moved['project']) == dest, (moved['project'], dest)
    print(f'{dest}  ({rewritten} text files rewritten {old_root} -> {root})', file=sys.stderr)
    print(dest)


if __name__ == '__main__':
    main()
