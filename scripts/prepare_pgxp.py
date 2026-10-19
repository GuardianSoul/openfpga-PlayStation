#!/usr/bin/env python3
"""Integrate source-bound PGXP L3 into a fresh, closed Pocket project copy.

This is an explicit experimental postprocessor, not a production default or
fit claim. Native guest arithmetic stays in place; metadata and F4 polygon
coordinates share its actual CPU/GTE/memory/DMA/GPU paths. The native input
project, constraints, features and seed are preserved. See docs/pgxp-integration.md.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from measure_pocket_seeds import TEXT_SUFFIXES, clone, inputs, search_directories
from prepare_resource_targets import digest
ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'rtl/pocket/pgxp_hooks'
PROJECT_TOKEN = '@PGXP_PROJECT@'


def normalized_text(path, project):
    # A selected core source embeds the audio coefficient filename. Only the
    # enclosing project directory may vary; the filename and contents stay bound.
    return path.read_text().replace(str(project), PROJECT_TOKEN)


def text_digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def input_identity(project):
    rows = []
    for row in inputs(project):
        path = Path(row['path'])
        sha = (text_digest(normalized_text(path, project))
               if path.suffix in TEXT_SUFFIXES else digest(path))
        rows.append(dict(name=path.name, assignment=row['assignment'],
                         library=row.get('library', 'work'), sha256=sha))
    return sorted(rows, key=lambda row: tuple(row.values()))


def selected(directory, name):
    matches = {Path(row['path']) for row in inputs(directory)
               if row['assignment'].endswith('_FILE')
               and Path(row['path']).name == name}
    if len(matches) != 1:
        raise ValueError(f'Expected one selected {name}, found {len(matches)}')
    return matches.pop()


def freeze(source, output):
    """Close sources, include directories, ROMs and sourced SDC dependencies."""
    before = inputs(source)
    clone(source, output)
    mapping = {str(source): str(output)}
    for row in before:
        path = Path(row['path'])
        target = output / (path.relative_to(source) if path.is_relative_to(source)
                           else Path('external') / path.relative_to(path.anchor))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        if digest(target) != row['sha256']:
            raise ValueError('Input changed while copying: ' + str(path))
        mapping[str(path)] = str(target)
    for folder in search_directories(source):
        mapping[str(folder)] = str(output / (
            folder.relative_to(source) if folder.is_relative_to(source)
            else Path('external') / folder.relative_to(folder.anchor)))
    rewrite = re.compile('|'.join(re.escape(p) for p in sorted(mapping, key=len, reverse=True)))
    for path in output.rglob('*'):
        if path.is_file() and path.suffix in TEXT_SUFFIXES:
            original = path.read_text()
            changed = rewrite.sub(lambda match: mapping[match[0]], original)
            if changed != original:
                path.write_text(changed)
    if before != inputs(source):
        raise ValueError('Source project changed during capture')
    if any(not Path(row['path']).is_relative_to(output) for row in inputs(output)):
        raise ValueError('Project capture left an external dependency')
    return before


def verify_assets(manifest):
    for hook in manifest['hooks']:
        path = ASSETS / hook['patch']
        if digest(path) != hook['patch_sha256']:
            raise ValueError('Hook asset differs: ' + hook['name'])
    for cell in manifest['cells']:
        if digest(ROOT / 'rtl/pocket' / cell['name']) != cell['sha256']:
            raise ValueError('PGXP source differs: ' + cell['name'])


def prepare_project(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(source):
        raise ValueError('Use a fresh output directory outside the input project')
    if not (source / 'apf_top.qsf').is_file():
        raise ValueError('Expected a prepared apf_top project')
    manifest_path = ASSETS / 'manifest.json'
    manifest_hash = digest(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    verify_assets(manifest)
    original = inputs(source)
    if input_identity(source) != manifest['baseline_inputs']:
        raise ValueError('Unqualified input closure: use the captured test4 native project')
    for hook in manifest['hooks']:
        if text_digest(normalized_text(selected(source, hook['name']), source)) != hook['input_sha256']:
            raise ValueError('Unqualified selected source: ' + hook['name'])
    # This transport maps the physically selected 2 MiB RAM and all CPU mirrors.
    # Its qualified controller has dedicated metadata response ownership.
    if "ram8mb => '0'" not in selected(source, 'psx_mister_psx_top_resource.vhd').read_text():
        raise ValueError('PGXP shadow requires the selected 2 MiB RAM configuration')
    margins = [Path(row['path']) for row in original
               if Path(row['path']).name == 'pocket_cpu_hold.sdc']
    if len(margins) != 1 or not re.search(r'\b0\.15\b', margins[0].read_text()):
        raise ValueError('The required 150 ps CPU hold constraint must be active')
    freeze(source, output)
    changes = []
    for hook in manifest['hooks']:
        path = selected(output, hook['name'])
        normalized = normalized_text(path, output)
        if text_digest(normalized) != hook['input_sha256']:
            raise ValueError('Captured source differs: ' + hook['name'])
        before_hash = digest(path)
        path.write_text(normalized)
        # Full before/after hashes prohibit fuzz, partial patches and drift.
        subprocess.run(['patch', '--batch', '--forward', '--fuzz=0', str(path),
                        str(ASSETS / hook['patch'])], cwd=output,
                       check=True, capture_output=True, text=True)
        if digest(path) != hook['output_sha256']:
            raise ValueError('Integrated source differs: ' + hook['name'])
        path.write_text(path.read_text().replace(PROJECT_TOKEN, str(output)))
        changes.append(dict(hook, path=str(path), captured_sha256=before_hash,
                            integrated_sha256=digest(path)))
    assignments = []
    for cell in manifest['cells']:
        target = output / 'pgxp' / cell['name']
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(ROOT / 'rtl/pocket' / cell['name'], target)
        assignments.append(f'set_global_assignment -name {cell["assignment"]} '
                           f'"{target}" -library {cell["library"]}')
    qsf = output / 'apf_top.qsf'
    # Packages precede their VHDL consumers; all cells are added exactly once.
    qsf.write_text('\n'.join(assignments) + '\n' + qsf.read_text())
    verify_assets(manifest)
    if digest(manifest_path) != manifest_hash or original != inputs(source):
        raise ValueError('Preparation inputs changed')
    closure = inputs(output)
    target = dict(top='apf_top', directory=str(output), quartus_sources=closure,
                  project_sha256={suffix: digest(output / ('apf_top' + suffix))
                                  for suffix in ('.qsf', '.qpf')},
                  fixed_ports={}, physical_clock_ports=['clk_74a', 'clk_74b'])
    (output / 'timing-inputs.json').write_text(json.dumps(target, indent=2) + '\n')
    report = dict(kind='PGXP L3 integration preparation', schema=1,
                  source_project=str(source), original_inputs=original,
                  hooks_manifest_sha256=manifest_hash, changes=changes,
                  target=target, runtime_setting='0x50000058', default_enabled=False,
                  simulation_qualified_source=True, fit_verified=False,
                  timing_verified=False, hardware_verified=False,
                  scope=manifest['scope'])
    (output / 'pgxp-preparation.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True,
                        help='Prepared native project matching the qualified hook inputs')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = prepare_project(args.input, args.output)
    print(f'Integrated {len(report["changes"])} source hooks into {args.output}')


if __name__ == '__main__':
    main()
