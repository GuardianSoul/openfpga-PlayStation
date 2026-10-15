#!/usr/bin/env python3
"""Audit the pinned pgate Pocket source and local build tools without building."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

try:
    from .tool_paths import ROOT, find_tool
except ImportError:
    from tool_paths import ROOT, find_tool

REFERENCE = ROOT / 'references/PlayStation_on_FPGA'
REQUIRED_MODULES = ('apf_top', 'core_bridge_cmd', 'synch_3',
                    'main_pll_0002', 'sdram128_burstn_ctrl_100')


def verify_support_manifest(path=ROOT / 'support/pocket_sources.json', workspace=ROOT):
    workspace = workspace.resolve()
    manifest = json.loads(path.read_text())
    pins = {}
    for relative, expected in manifest['pins'].items():
        repository = (workspace / relative).resolve()
        if not repository.is_relative_to(workspace):
            pins[relative] = False
            continue
        result = subprocess.run(['git', '-C', str(repository), 'rev-parse', 'HEAD'],
                                text=True, capture_output=True)
        pins[relative] = result.returncode == 0 and result.stdout.strip() == expected
    files = []
    for entry in manifest['files']:
        source = (workspace / entry['path']).resolve()
        verified = (source.is_relative_to(workspace) and source.is_file() and
                    hashlib.sha256(source.read_bytes()).hexdigest() == entry['sha256'])
        files.append({'path': entry['path'], 'role': entry['role'], 'verified': verified})
    return {'verified': all(pins.values()) and all(item['verified'] for item in files),
            'pins': pins, 'files': files, 'limits': manifest['limits']}


def strip_comments(text):
    # Preserve quoted filenames and line numbers while removing C/SFL comments.
    pattern = r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*'
    return re.sub(pattern, lambda m: m[0] if m[0].startswith('"')
                  else ''.join('\n' if ch == '\n' else ' ' for ch in m[0]),
                  text, flags=re.DOTALL)


def scan_dependencies(root, entry):
    """Conservative SFL include graph, including conditional references."""
    root = root.resolve()
    seen, missing = set(), []
    pending = [root / entry]
    while pending:
        source = pending.pop().resolve()
        if source in seen:
            continue
        seen.add(source)
        text = strip_comments(source.read_text(errors='replace'))
        for match in re.finditer(r'^[ \t]*%i[ \t]+"([^"]+)"', text, re.MULTILINE):
            include = match[1]
            header = (source.parent / include).resolve()
            generated_from = header.with_suffix('.sflp')
            if not header.is_relative_to(root):
                reason = 'outside pinned repository'
            elif header.is_file():
                continue
            elif header.suffix == '.h' and generated_from.is_file():
                pending.append(generated_from)
                continue
            else:
                reason = 'header and generating source are absent'
            missing.append({'source': str(source.relative_to(root)),
                            'line': text.count('\n', 0, match.start()) + 1,
                            'include': include, 'reason': reason})
    return {'sources': sorted(str(p.relative_to(root)) for p in seen),
            'missing': sorted(missing, key=lambda m: (m['source'], m['include']))}


def audit(reference=REFERENCE):
    graph = scan_dependencies(reference, Path('Pocket/core.sflp'))
    makefile = reference.joinpath('Makefile').read_text().replace('\\\n', ' ')
    listed = re.search(r'^SFLP\s*=\s*(.+)$', makefile, re.MULTILINE)
    missing_make = sorted(name for name in listed[1].split()
                          if not reference.joinpath(name).is_file())
    modules = set()
    for source in reference.rglob('*.v'):
        modules.update(re.findall(r'\bmodule\s+(\w+)',
                                 strip_comments(source.read_text(errors='replace'))))
    missing_modules = [name for name in REQUIRED_MODULES if name not in modules]
    projects = sorted(str(p.relative_to(reference))
                      for p in reference.joinpath('Pocket').glob('*.qsf'))
    tools = {name: find_tool(name) for name in
             ('sflp', 'sfl2vl', 'quartus_sh', 'iverilog', 'vvp')}
    missing_tools = [name for name in ('sflp', 'sfl2vl', 'quartus_sh') if not tools[name]]
    commit = subprocess.check_output(['git', '-C', str(reference),
                                      'rev-parse', 'HEAD'], text=True).strip()
    support = verify_support_manifest()
    return {
        'reference_commit': commit,
        'baseline_ready': not (graph['missing'] or missing_make or
                               missing_modules or not projects or missing_tools or not support['verified']),
        'support_sources': support,
        'sfl_dependencies': graph,
        'legacy_makefile_missing': missing_make,
        'missing_wrapper_modules': missing_modules,
        'pocket_quartus_projects': projects,
        'tools': tools,
        'missing_baseline_tools': missing_tools,
        'limits': 'Conservative static audit, including conditional SFL includes. '
                  'Not compilation, port validation, synthesis or timing closure.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--strict', action='store_true',
                        help='Return failure when reference build prerequisites are absent.')
    args = parser.parse_args()
    report = audit()
    print(json.dumps(report, indent=2))
    return 1 if args.strict and not report['baseline_ready'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
