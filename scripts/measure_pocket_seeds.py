#!/usr/bin/env python3
"""Paired physical-top seed measurements from frozen captured inputs.

No variant is selected and no bitstream is made. The supplied replacement
must match one source basename; every other compiled byte/settings stays fixed
except copied path names and SEED. Capacity failures are valid measurements.
The selected source must have a quoted absolute QSF assignment and contain no
embedded captured-project/input paths. Other inputs may use internal relative paths.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import tempfile

from measure_spu_scalar_full_top import FILE_ASSIGNMENT, inputs as assigned_inputs
from prepare_resource_targets import digest
from run_resource_fit import fit_target
from tool_paths import ROOT, find_tool
from pocket_project_copy import project_copy_ignore


TEXT_SUFFIXES = {'.sv', '.v', '.vhd', '.hv', '.vh', '.svh', '.inl', '.qsf', '.qpf', '.sdc'}
SEARCH_PATH = re.compile(r'^set_global_assignment -name SEARCH_PATH (?:"([^"]+)"|(\S+))', re.M)
SDC_SOURCE = re.compile(
    r'^\s*source\s+(?:-encoding\s+[A-Za-z0-9_-]+\s+)?'
    r'(?:"([^"\n]+)"|\{([^}\n]+)\}|([^\s;]+))\s*(?:#.*)?$')


def sdc_dependencies(directory, roots):
    """Capture literal Tcl source closure, including dependencies outside QSF.

    Relative source paths resolve in Quartus's project working directory,
    including when a sourced file is in another directory. Dynamic Tcl must
    be resolved explicitly before freezing; silently missing it is unsafe.
    """
    result = {}; active = set(); visited = set()
    def visit(path):
        path = path.resolve()
        if path in active: raise ValueError('Cyclic SDC source dependency: '+str(path))
        if path in visited: return
        if not path.is_file(): raise ValueError('Missing SDC source dependency: '+str(path))
        active.add(path)
        for line in path.read_text().splitlines():
            if line.lstrip().startswith('#'): continue
            if not re.search(r'(?:^|[;{\[])\s*source(?:\s|$)', line): continue
            match = SDC_SOURCE.fullmatch(line)
            if not match: raise ValueError('Unsupported Tcl source command in '+str(path)+': '+line)
            name = next(group for group in match.groups() if group is not None)
            if any(token in name for token in ('$', '[', ']', ';', '\\')):
                raise ValueError('Dynamic SDC source path must be resolved before capture: '+name)
            child = Path(name)
            child = (child if child.is_absolute() else directory/child).resolve()
            if not child.is_file(): raise ValueError('Missing SDC source dependency: '+str(child))
            result[str(child)] = dict(path=str(child), sha256=digest(child), assignment='SDC_SOURCE', library='work')
            visit(child)
        active.remove(path); visited.add(path)
    for path in roots: visit(Path(path))
    return list(result.values())


def search_directories(directory):
    result = []
    for match in SEARCH_PATH.finditer((directory/'apf_top.qsf').read_text()):
        path = Path(match[1] or match[2])
        path = (path if path.is_absolute() else directory/path).resolve()
        if not path.is_dir(): raise ValueError('Missing include directory: '+str(path))
        result.append(path)
    return result


def inputs(directory):
    # Freeze all include-search files too, retaining directory structure. QSF
    # MISC_FILE entries alone do not redirect bare Verilog `include lookups.
    result = {s['path']: s for s in assigned_inputs(directory)}
    for folder in search_directories(directory):
        for path in sorted(folder.rglob('*')):
            if path.is_file() and str(path.resolve()) not in result:
                path = path.resolve()
                result[str(path)] = dict(path=str(path), sha256=digest(path), assignment='INCLUDE_SEARCH')
    roots = [s['path'] for s in result.values() if
             s['assignment'] in ('SDC_FILE', 'PROJECT') and Path(s['path']).suffix=='.sdc']
    for source in sdc_dependencies(directory, roots):
        result.setdefault(source['path'], source)
    return list(result.values())



def clone(source, destination):
    shutil.copytree(source, destination, ignore=project_copy_ignore(source))
    for p in destination.rglob('*'):
        if p.is_file() and p.suffix in TEXT_SUFFIXES:
            text = p.read_text(); changed = text.replace(str(source), str(destination))
            if text != changed: p.write_text(changed)


def measurement(result):
    report = Path(result['directory'])/'output_files/apf_top.fit.rpt'
    if report.is_file():
        text = report.read_text(errors='replace')
        required = re.search(r'Fitter requires (\d+) LABs.*only (\d+) LABs', text)
        used = re.search(r'Total LABs:\s+partially or completely used\s*;\s*([\d,]+) /', text)
        result['required_LABs'] = int(required[1]) if required else int(used[1].replace(',', '')) if used else None
        result['fit_report'] = dict(path=str(report), sha256=digest(report))
    steps = result['steps']
    capacity_failure = len(steps) == 2 and steps[0] == dict(tool='quartus_map', exit_code=0) and (
        steps[1]['tool'] == 'quartus_fit' and steps[1]['exit_code'] not in (None, 0)
        and (result.get('required_LABs') or 0) > 1848
        and any('Error (170012)' in e for e in result['tool_error_messages'])
        and all(e.startswith(('Error (170012)', 'Error (11802)', 'Error:')) for e in result['tool_error_messages']))
    result['measurement_valid'] = result['source_identity_verified'] and result['project_identity_verified'] and (
        result['fit_verified'] or capacity_failure)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('captured_run', type=Path)
    parser.add_argument('--seeds', nargs='+', type=int, default=[1, 3])
    parser.add_argument('--source', required=True, help='Exactly one source basename to replace')
    parser.add_argument('--expected-original-sha256', required=True)
    parser.add_argument('--replacement', type=Path, required=True)
    parser.add_argument('--label', required=True, help='Human-readable captured configuration and its omissions')
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args()
    if len(set(args.seeds)) != len(args.seeds) or any(s < 1 for s in args.seeds):
        parser.error('Seeds must be distinct positive integers')
    captured = args.captured_run.resolve(); replacement = args.replacement.resolve()
    original = inputs(captured)
    matches = [s for s in original if Path(s['path']).name == args.source]
    if len(matches) != 1 or matches[0]['sha256'] != args.expected_original_sha256:
        raise ValueError('Captured source does not match the expected baseline')
    assignments=[]
    for m in FILE_ASSIGNMENT.finditer((captured/'apf_top.qsf').read_text()):
        path=Path(m[2] or m[3]); path=path if path.is_absolute() else captured/path
        if str(path.resolve())==matches[0]['path']:assignments.append(m)
    if len(assignments)!=1 or assignments[0][2]!=matches[0]['path']:
        raise ValueError('Selected source needs one quoted absolute QSF assignment')
    selected_text=Path(matches[0]['path']).read_text()
    if str(captured) in selected_text or any(s['path'] in selected_text for s in original):
        raise ValueError('Selected source embeds project/input paths; normalize those in a private captured copy first')
    parent = ROOT/'build/resources/pocket-seeds/runs'; parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=parent))
    print(work, flush=True)
    archives = {}
    def archive(paths):
        for name in paths:
            p = Path(name).resolve(); name = str(p); sha = digest(p)
            if name in archives:
                if archives[name]['sha256'] != sha: raise ValueError('Input changed between targets: '+name)
                continue
            copy = work/'input-archive'/str(len(archives))/p.name
            copy.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(p, copy)
            if digest(copy) != sha: raise ValueError('Input changed during archive: '+name)
            archives[name] = dict(path=str(copy), sha256=sha)
    tools = [Path(__file__), *[ROOT/'scripts'/name for name in (
        'measure_spu_scalar_full_top.py', 'run_resource_fit.py', 'resource_report.py',
        'prepare_resource_targets.py', 'tool_paths.py', 'pocket_project_copy.py')]]
    archive([s['path'] for s in original]+[replacement]+tools)
    # Freeze external RTL as well as the copied project. Another agent may
    # continue editing the shared checkout while all paired fits consume this
    # same snapshot. Preserve raw originals in input-archive before path edits.
    baseline = work/'frozen-baseline'; clone(captured, baseline)
    path_map = {str(captured): str(baseline)}
    for index, source in enumerate(original):
        p = Path(source['path'])
        copy = baseline/p.relative_to(captured) if p.is_relative_to(captured) else baseline/'external'/p.relative_to(p.anchor)
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archives[str(p)]['path'], copy)
        path_map[str(p)] = str(copy)
    for folder in search_directories(captured):
        path_map[str(folder)] = str(baseline/folder.relative_to(captured) if folder.is_relative_to(captured) else baseline/'external'/folder.relative_to(folder.anchor))
    rewrite = re.compile('|'.join(re.escape(old) for old in sorted(path_map, key=len, reverse=True)))
    for p in baseline.rglob('*'):
        if p.is_file() and p.suffix in TEXT_SUFFIXES:
            text = p.read_text()
            text = rewrite.sub(lambda match: path_map[match[0]], text)
            p.write_text(text)
    frozen = inputs(baseline); archive(s['path'] for s in frozen)
    for s in frozen:
        if not Path(s['path']).is_relative_to(baseline):
            raise ValueError('Compile input escaped frozen snapshot: '+s['path'])
    frozen_replacement = work/'replacement'/args.source; frozen_replacement.parent.mkdir()
    shutil.copyfile(archives[str(replacement)]['path'], frozen_replacement)
    archive([frozen_replacement])
    report = dict(captured_run=str(captured), configuration=args.label, seeds=args.seeds,
        original_source=matches[0], replacement=dict(path=str(replacement), sha256=digest(frozen_replacement)),
        original_input_sha256={s['path']: s['sha256'] for s in original},
        input_archives=archives, results={}, paired_LABs={}, passed=False,
        production_selected=False, board_timing_verified=False, hardware_verified=False,
        scope='Same frozen physical top, paired seeds; source replacement only, no feature/settings changes')
    protected = {s['path']: s['sha256'] for s in frozen}
    protected[str(frozen_replacement)] = digest(frozen_replacement)
    protected.update({str(p): digest(p) for p in tools})
    report['protected_sha256'] = protected
    for seed in args.seeds:
        for name in ('control', 'candidate'):
            directory = work/(name+'-s'+str(seed)); clone(baseline, directory)
            qsf = directory/'apf_top.qsf'; text = qsf.read_text()
            if len(re.findall(r'^set_global_assignment -name SEED \d+$', text, re.M)) != 1:
                raise ValueError('Expected exactly one compiler seed assignment')
            text = re.sub(r'(^set_global_assignment -name SEED )\d+$', rf'\g<1>{seed}', text, flags=re.M)
            if name == 'candidate':
                selected = [s for s in inputs(directory) if Path(s['path']).name == args.source]
                if len(selected) != 1 or digest(Path(selected[0]['path'])) != args.expected_original_sha256:
                    raise ValueError('Copied original source differs from proven baseline')
                candidate = directory/'candidate'/args.source; candidate.parent.mkdir()
                shutil.copyfile(frozen_replacement, candidate)
                old = selected[0]['path']
                if text.count('"'+old+'"') != 1: raise ValueError('Expected one source assignment')
                text = text.replace('"'+old+'"', '"'+str(candidate)+'"')
            qsf.write_text(text)
            sources = inputs(directory); archive(s['path'] for s in sources)
            target = dict(top='apf_top', directory=str(directory), quartus_sources=sources,
                project_sha256={'.qsf': digest(qsf), '.qpf': digest(directory/'apf_top.qpf')},
                fixed_ports={}, physical_clock_ports=['clk_74a', 'clk_74b'])
            (directory/'area-inputs.json').write_text(json.dumps(target, indent=2)+'\n')
            result = measurement(fit_target(Path(find_tool('quartus_sh')).parent, target, '5CEBA4F23C8', args.timeout))
            report['results'][name+'-s'+str(seed)] = result
            if len(report['results']) % 2 == 0:
                pair = [report['results'][n+'-s'+str(seed)].get('required_LABs') for n in ('control', 'candidate')]
                if all(v is not None for v in pair):
                    report['paired_LABs'][str(seed)] = dict(control=pair[0], candidate=pair[1], delta=pair[1]-pair[0])
            identity = all(digest(Path(p)) == h for p, h in protected.items())
            report['source_identity_verified'] = identity
            report['passed'] = identity and len(report['results']) == len(args.seeds)*2 and all(
                r['measurement_valid'] for r in report['results'].values())
            (work/'report.json').write_text(json.dumps(report, indent=2)+'\n')
            print(seed, name, 'LABs=', result.get('required_LABs'), 'fit=', result['fit_verified'],
                  'measurement_valid=', result['measurement_valid'], flush=True)
            if not identity or not result['measurement_valid']: raise SystemExit(1)
    print(work/'report.json', flush=True)
    if not report['passed']: raise SystemExit(1)


if __name__ == '__main__': main()
