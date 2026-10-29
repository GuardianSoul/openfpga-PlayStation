#!/usr/bin/env python3
"""Bundle the complete corresponding source of a release bitstream.

The release core is built from a generated Quartus project. This collects
every file that project compiles (HDL, IP, ROM images, constraints, QSF/QPF)
into GuardianSoul.PlayStation_<version>_<date>_source.tar.gz (named like the
core zip) with relative paths, so anyone can
open it in Quartus and rebuild the same bitstream. It applies the public
export's name rewrites and refuses the bundle when a file still names the
workspace, or when any input carries an Apache-2.0 or GPL-3 license header
(neither may share a bitstream with the GPL-2.0-only parts).

  python3 scripts/release_source.py <fitted project> --version 0.1.0
"""
import argparse
import datetime
import io
import json
from pathlib import Path
import re
import tarfile

try:                                   # the workspace's public-name rewrites
    from export_release import LEAKS, REWRITES
except ImportError:                    # public tree: nothing to rewrite
    LEAKS, REWRITES = re.compile(r'/(?:home|run/media)/'), ()
from package_release import AUTHOR, REPO
from tool_paths import ROOT

ASSIGNMENT = re.compile(r'^(set_global_assignment -name (?:VERILOG_FILE|SYSTEMVERILOG_FILE|VHDL_FILE|MISC_FILE|SDC_FILE|'
                        r'MIF_FILE|HEX_FILE|QIP_FILE|IP_FILE|TCL_SCRIPT_FILE) )(?:"([^"]+)"|(\S+))(.*)$', re.M)
SOURCED = re.compile(r'^\s*source\s+(?:"([^"]+)"|\{([^}]+)\}|(\S+))', re.M)
ROM = re.compile(r'"([^"\n]+\.(?:hex18|hex|mif))"')
INCOMPATIBLE = re.compile(r'SPDX-License-Identifier:\s*(?:Apache|GPL-3|LGPL-3|AGPL)|Apache License,? Version 2'
                          r'|either version 3 of the License|GNU GENERAL PUBLIC LICENSE\s+Version 3', re.I)
# Apache-2.0 files without a license header (never built into a release core).
INCOMPATIBLE_PATHS = ('repo/references/MIRLO64/rtl/', 'repo/rtl/memory/psx_sdr_phy.sv')
# License texts for workspace-only code (MIRLO64's LiteX-derived PHY).
WORKSPACE_ONLY_LICENSES = ('LiteX-BSD-2-Clause.txt',)
# "Found entity N: name File: path" in the Analysis & Synthesis messages.
FOUND = re.compile(r'Found entity \d+: (\S+) File: (.+?) Line: \d+')
PACKAGE = re.compile(r'^\s*package\s+\w+\s*(?:is\b|;)', re.I | re.M)
README = """Corresponding source of the PlayStation core {version} for Analogue Pocket.

project/ is the Quartus project the release bitstream was built from;
repo/ holds the sources it compiles, at the paths of {repo}.
To rebuild: open project/apf_top.qpf in Quartus Prime Lite 25.1 and compile
(the fitter seed is set in apf_top.qsf), or run quartus_sh --flow compile
apf_top in project/. The Pocket loads the bit-reversed .rbf as
bitstream.rbf_r (see scripts/package_pocket_early.py in the repository).

The core as a whole is GPL-2.0; see LICENSE and THIRD_PARTY_NOTICES.md.
"""


def external_roots(paths, project):
    """The checkout directories of a frozen project's copies of repository files:
    project/external/<the original checkout's absolute path>/<repository path> (the frozen fit's
    snapshot). For each copy, the shortest leading part whose remainder is a file of this repository;
    a copy whose remainder is not here (an uninitialised submodule) is placed by the roots found from
    the others."""
    base = project/'external'
    roots = set()
    for path in paths:
        if not path.is_relative_to(base): continue
        parts = path.relative_to(base).parts
        k = next((k for k in range(1, len(parts)) if ROOT.joinpath(*parts[k:]).is_file()), None)
        if k is not None: roots.add(base.joinpath(*parts[:k]))
    return roots


def placed(path, project, roots=()):
    """Archive name of a compile input; inputs must come from the project or the repository. A frozen
    copy of a repository file goes to its repository path (its own content: what was compiled)."""
    root = next((r for r in sorted(roots, key=lambda r: -len(str(r))) if path.is_relative_to(r)), None)
    if root is not None: return 'repo/' + path.relative_to(root).as_posix()
    if path.is_relative_to(project): return 'project/' + path.relative_to(project).as_posix()
    if path.is_relative_to(ROOT): return 'repo/' + path.relative_to(ROOT).as_posix()
    raise ValueError(f'compile input outside the project and repository: {path}')


def collect(project):
    qsf = (project/'apf_top.qsf').read_text()
    files = {}
    def add(path):
        path = path.resolve()
        if not path.is_file(): raise ValueError(f'missing compile input {path}')
        files[path] = None   # named below, once the frozen copies' roots are known
        return path
    for m in ASSIGNMENT.finditer(qsf):
        path = Path(m[2] or m[3])
        path = add(path if path.is_absolute() else project/path)
        if path.suffix in ('.v', '.sv', '.vhd'):
            for name in ROM.findall(path.read_text(errors='replace')):
                rom = Path(name)
                for base in ((rom,) if rom.is_absolute() else (project/rom, path.parent/rom)):
                    if base.is_file(): add(base); break
    for name in ('apf_top.qsf', 'apf_top.qpf', 'pocket.sdc'):
        if (project/name).is_file(): add(project/name)
    # Constraint files can pull in others with `source` (the APF constraints do).
    for path in [p for p in files if p.suffix in ('.sdc', '.tcl')]:
        for groups in SOURCED.findall(path.read_text(errors='replace')):
            name = next(g for g in groups if g)
            add(Path(name) if Path(name).is_absolute() else project/name)
    roots = external_roots(files, project)
    for path in files:
        files[path] = placed(path, project, roots)
    clashes = {n for n in files.values() if list(files.values()).count(n) > 1}
    if clashes: raise ValueError(f'compile inputs share an archive name: {sorted(clashes)}')
    return files, roots


def uncommented(path):
    text = re.sub(r'/\*.*?\*/', '', path.read_text(errors='replace'), flags=re.S)
    return re.sub(r'(//|--).*', '', text)


def drop_unused(files, project):
    """Leave out compile inputs that are analysed but not built into the bitstream.

    The generated projects list some files they never instantiate (old memory
    paths, among them Apache-2.0 SDRAM PHY copies). A file is left out when
    none of the entities the map report found in it appears in the synthesized
    hierarchy, it declares no package, and no kept file instantiates it.
    Compiling the bundle and comparing its .rbf proves the result.
    """
    report = project/'output_files/apf_top.map.rpt'
    if not report.is_file(): raise ValueError(f'{report} missing: bundle a fitted project')
    text = report.read_text(errors='replace')
    hierarchy = '\n'.join(l for l in text.splitlines() if 'Found entity' not in l and 'Found design unit' not in l)
    entities = {}
    for name, path in FOUND.findall(text):
        entities.setdefault(Path(path.strip()).resolve(), set()).add(name)
    def used(name): return re.search(rf'\|{re.escape(name)}(?::|\s|\||$)', hierarchy, re.I | re.M)
    dropped = [p for p in files if entities.get(p) and not any(map(used, entities[p]))
               and not PACKAGE.search(p.read_text(errors='replace'))]
    kept = [p for p in files if p not in dropped and p.suffix in ('.v', '.sv', '.vhd')]
    for path in list(dropped):
        for name in entities[path]:
            n = re.escape(name)
            # Instances only: "name #(", "name inst (", VHDL "name port map",
            # "entity work.name" or "component name".
            use = re.compile(rf'\b{n}\s*#\s*\(|\b{n}\s+[A-Za-z_]\w*\s*\(|\b{n}\s+(?:generic|port)\s+map\b'
                             rf'|\bentity\s+\w+\.{n}\b|\bcomponent\s+{n}\b', re.I)
            users = [p.name for p in kept if use.search(uncommented(p))]
            # Named by a kept file but absent from the map hierarchy: an
            # instance in a disabled generate branch (SERV's serv_aligner
            # under ALIGN=0). Keep the file; the .rbf comparison still holds.
            if users and path in dropped: dropped.remove(path)
    for path in dropped: del files[path]
    return dropped


def without(qsf, dropped, project):
    """The QSF without the assignment lines of dropped inputs."""
    def keep(line):
        m = ASSIGNMENT.match(line)
        if not m: return True
        path = Path(m[2] or m[3])
        return (path if path.is_absolute() else project/path).resolve() not in dropped
    return '\n'.join(filter(keep, qsf.split('\n')))


def public(text, project, roots=()):
    """Relative paths and public names; the QSF's absolute paths become paths from project/. roots: the
    frozen copies' checkout directories (external_root), whose files are under ../repo/ in the bundle."""
    for root in sorted(roots, key=lambda r: -len(str(r))):
        text = text.replace(str(root) + '/', '../repo/').replace(root.relative_to(project).as_posix() + '/', '../repo/')
    text = text.replace(str(project) + '/', '').replace(str(project), '.')
    text = text.replace(str(ROOT) + '/', '../repo/')
    for pattern, replacement in REWRITES:
        text = re.sub(pattern, replacement.format(url=REPO, author=AUTHOR), text)
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('project', type=Path)
    parser.add_argument('--version', required=True)
    parser.add_argument('--date', default=datetime.date.today().isoformat(), help='Release date, as in the core zip name')
    parser.add_argument('--output', type=Path, default=ROOT/'build/release/source')
    args = parser.parse_args()
    project = args.project.resolve()
    files, roots = collect(project)
    dropped = drop_unused(files, project)
    problems, entries = [], {}
    for path, name in sorted(files.items(), key=lambda item: item[1]):
        if name.startswith(INCOMPATIBLE_PATHS): problems.append(f'{name}: Apache-2.0 (workspace-only file)')
        data = path.read_bytes()
        try: text = data.decode()
        except UnicodeDecodeError: entries[name] = data; continue
        if INCOMPATIBLE.search(text[:4000]): problems.append(f'{name}: Apache-2.0 or GPL-3 license header')
        if name == 'project/apf_top.qsf':
            text = without(text, dropped, project)
        text = public(text, project, roots)
        for number, line in enumerate(text.splitlines(), 1):
            if LEAKS.search(line): problems.append(f'{name}:{number}: {line.strip()[:120]}')
        entries[name] = text.encode()
    entries['LICENSE'] = (ROOT/'LICENSE').read_bytes()
    # No pocket_sotn file is in a release from 0.5.3, so neither is its row.
    entries['THIRD_PARTY_NOTICES.md'] = ''.join(l for l in (ROOT/'THIRD_PARTY_NOTICES.md').read_text().splitlines(keepends=True)
                                                if 'pocket_sotn' not in l).encode()
    for path in sorted((ROOT/'licenses').iterdir()):
        if path.name in WORKSPACE_ONLY_LICENSES: continue   # its code is never in a release bitstream
        entries['licenses/' + path.name] = path.read_bytes()
    entries['README.txt'] = README.format(version=args.version, repo=REPO).encode()
    # Archive names are public too (0.5.6's bundle had project/external/<checkout path>/... names).
    for name in sorted(entries):
        if LEAKS.search(name): problems.append(f'{name}: archive name')
    # From 0.5.3 the public release carries no pocket_sotn code, paths or
    # mentions (2026-10-17); the bundle stops on any remaining text.
    for name, data in sorted(entries.items()):
        for number, line in enumerate(data.decode(errors='replace').splitlines(), 1):
            if 'pocket_sotn' in line.lower(): problems.append(f'{name}:{number}: names pocket_sotn: {line.strip()[:100]}')
    if problems:
        raise SystemExit('Source bundle stopped:\n  ' + '\n  '.join(problems[:60]))
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    top = f'{AUTHOR}.PlayStation_{args.version}_{args.date}_source'
    archive = out/f'{top}.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name, data in sorted(entries.items()):
            info = tarfile.TarInfo(f'{top}/{name}'); info.size = len(data); info.mtime = 0; info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    (out/'source-report.json').write_text(json.dumps(dict(project=str(project), files=len(entries),
        dropped_unused=[str(p) for p in dropped]), indent=2)+'\n')
    print(archive, len(entries), 'files')


if __name__ == '__main__': main()
