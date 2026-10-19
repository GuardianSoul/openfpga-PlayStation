#!/usr/bin/env python3
"""Validate a frozen Pocket experiment in one Jenkins executor, retaining 150 ps.

Snapshot preparation may run locally. Physical tools and timing audits require a
Jenkins build. A snapshot copies only the manifest's input closure, never unassigned assets
or an old database. Diagnostic mode reads an already verified fit on a private
copy; it does not refit the candidate.
"""
import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import shutil
import subprocess
import sys

HOLD = '6a0f9b621fb4a05f948671fb95488db90abc68759637045c15441eb14399ee53'
# the reviewed hold scope; the second (48781c9) also accepts the --vram-full-rate
# chip clock general[3] and leaves it out of the margin, the same 9 CPU pairs
HOLD_SCOPE = ('90ae3ebe6be262a6198e2f9200e73a5d0806a8bbaacfdfa1cdf2a7be936636df',
              '9b60631e6477c2b59de3da2e29730675fe99292572f4c0c51db721814536e189')
DEVICE = '5CEBA4F23C8'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n')


def hold_guard(project, rows):
    hold = project / 'cpu-hold-margin/pocket_cpu_hold.sdc'
    require(any(r['assignment'] == 'SDC_FILE' and Path(r['path']) == hold for r in rows),
            'Assigned 150 ps CPU hold constraint required')
    text = hold.read_text().replace(str(project), '<PROJECT>')
    require(hashlib.sha256(text.encode()).hexdigest() == HOLD and
            digest(hold.with_name('scope.tcl')) in HOLD_SCOPE,
            'Exact retained 150 ps constraint and all-nine-pair scope required')
    require((project / 'apf_top.qsf').read_text().count(str(hold)) == 1,
            'One assigned CPU hold SDC required')


def load_source(project, manifest):
    project, manifest = Path(project).resolve(), Path(manifest).resolve()
    envelope = json.loads(manifest.read_text())
    data = envelope.get('target', envelope)
    rows = data.get('quartus_sources')
    require(data.get('top') == 'apf_top' and isinstance(rows, list) and rows,
            'Explicit apf_top fit-input manifest required')
    require(all(digest(r['path']) == r['sha256'] for r in rows), 'Manifest source drift')
    require(all(digest(project / ('apf_top' + suffix)) == sha
                for suffix, sha in data['project_sha256'].items()), 'Manifest project drift')
    require(set(data['project_sha256']) == {'.qsf', '.qpf'}, 'QSF and QPF identities required')
    hold_guard(project, rows)
    require(json.loads((project / 'manifest.json').read_text()).get('reset_only') is True,
            'Reset-only capacity composition required')
    from measure_pocket_seeds import inputs
    require(rows == inputs(project), 'Manifest does not close the live selected inputs')
    return data


def snapshot(source, manifest, output, seed):
    source, manifest, output = map(lambda p: Path(p).resolve(), (source, manifest, output))
    require(not output.exists(), 'Fresh output directory required')
    require(not output.is_relative_to(source), 'Snapshot must be outside its source project')
    require(0 <= seed <= 2147483647, 'Invalid fitter seed')
    target = load_source(source, manifest)
    manifest_sha = digest(manifest)
    protected = {r['path']: r['sha256'] for r in target['quartus_sources']}
    protected.update({str(source / ('apf_top' + s)): h
                      for s, h in target['project_sha256'].items()})
    protected[str(manifest)] = manifest_sha
    protected[str(source / 'manifest.json')] = digest(source / 'manifest.json')
    from measure_pocket_seeds import inputs, search_directories
    mapping = {str(source): str(output)}
    captured = {}
    for row in target['quartus_sources']:
        path = Path(row['path'])
        copy = output / path.relative_to(source) if path.is_relative_to(source) else (
            output / 'external' / path.relative_to(path.anchor))
        captured[path] = copy
        mapping[str(path)] = str(copy)
    for folder in search_directories(source):
        mapping[str(folder)] = str(output / folder.relative_to(source) if folder.is_relative_to(source)
                                   else output / 'external' / folder.relative_to(folder.anchor))
    # One substitution pass avoids recursively rewriting already relocated paths.
    replacements = {key.encode(): value.encode() for key, value in mapping.items()}
    pattern = re.compile(b'|'.join(re.escape(k) for k in sorted(replacements, key=len, reverse=True)))
    for name in ('apf_top.qpf', 'manifest.json'):
        captured[source / name] = output / name
    contents = {copy: pattern.sub(lambda m: replacements[m[0]], path.read_bytes())
                for path, copy in captured.items()}
    qsf = output / 'apf_top.qsf'
    contents[qsf], count = re.subn(rb'(?m)^(set_global_assignment -name SEED )\d+$',
                                  lambda m: m[1] + str(seed).encode(), contents[qsf])
    require(count == 1, 'One explicit fitter seed required')
    require(all(digest(p) == sha for p, sha in protected.items()), 'Source changed while capturing')
    output.mkdir(parents=True)
    for copy, content in contents.items():
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(content)
    rows = inputs(output)
    require(all(Path(r['path']).is_relative_to(output) for r in rows), 'Unfrozen dependency')
    expected = {(r['assignment'], str(captured[Path(r['path'])]),
                 hashlib.sha256(contents[captured[Path(r['path'])]]).hexdigest())
                for r in target['quartus_sources']}
    observed = {(r['assignment'], r['path'], r['sha256']) for r in rows}
    require(len(rows) == len(target['quartus_sources']) and observed == expected,
            'Snapshot input closure changed')
    hold_guard(output, rows)
    require(all(digest(p) == sha for p, sha in protected.items()), 'Originals changed after capture')
    result = dict(target, directory=str(output), quartus_sources=rows,
                  project_sha256={s: digest(output / ('apf_top' + s)) for s in ('.qsf', '.qpf')})
    write_json(output / 'fit-inputs.json', result)
    write_json(output / 'frozen-snapshot.json', dict(
        source=str(source), manifest=str(manifest), manifest_sha256=manifest_sha,
        source_manifest_document=json.loads(manifest.read_text()),
        protected_original_sha256=protected, source_inputs=target['quartus_sources'],
        captured_inputs=rows, seed=seed, extra_cpu_hold_ns=.15,
        originals_unchanged=True, selected=False))
    return result


def executor_guard():
    require(os.environ.get('BUILD_URL', '').startswith('https://build.session-zero.dev/'),
            'Run physical tools and audits through the psx-fit Jenkins queue')
    active = [line for line in subprocess.check_output(['ps', '-eo', 'comm='], text=True).splitlines()
              if line.strip() in {'quartus_map', 'quartus_fit', 'quartus_sta', 'quartus_asm'}]
    require(not active, 'Legacy Quartus job still active; drain the host before Jenkins')


def job_context():
    from tool_paths import ROOT, find_tool
    shell = find_tool('quartus_sh')
    require(shell, 'Configured Quartus required')
    return dict(build_url=os.environ.get('BUILD_URL'), build_number=os.environ.get('BUILD_NUMBER'),
                job_name=os.environ.get('JOB_NAME'), node=os.environ.get('NODE_NAME'),
                queue_id=os.environ.get('QUEUE_ID'), host=platform.node(),
                requested_git_ref=os.environ.get('GIT_REF'),
                runner_git_commit=subprocess.check_output(
                    ['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
                compiler_version=subprocess.check_output([shell, '--version'], text=True).strip())


def protected_fit(project, target):
    protected = {r['path']: r['sha256'] for r in target['quartus_sources']}
    protected.update({str(project / ('apf_top' + s)): h for s, h in target['project_sha256'].items()})
    for name in ('db', 'incremental_db'):
        protected.update({str(p): digest(p) for p in (project / name).rglob('*') if p.is_file()})
    return protected


def private_sta(project, target, folder, runs):
    """Run auditors only on a copied DB, preserving the original compiled fit."""
    from tool_paths import ROOT, find_tool
    executor_guard()
    folder.mkdir(parents=True)
    analysis = folder.parent.with_name(folder.parent.name + '-analysis')
    require(not analysis.exists() and not analysis.is_relative_to(project), 'Fresh sibling analysis copy required')
    protected = protected_fit(project, target)
    shutil.copytree(project, analysis)
    sta = Path(find_tool('quartus_sh')).parent / 'quartus_sta'
    wrapper = ROOT / 'scripts/run_pocket_timing_audit.tcl'
    results = {}
    for label, arguments in runs:
        log = folder / (label + '.log')
        command = [str(sta), '-t', str(wrapper), *map(str, arguments)]
        with log.open('w') as stream:
            rc = subprocess.run(command, cwd=analysis, stdout=stream,
                                stderr=subprocess.STDOUT, timeout=900).returncode
        require(all(digest(p) == h for p, h in protected.items()), 'Original fit changed during audit')
        results[label] = dict(exit_code=rc, command=command, log_sha256=digest(log))
        if rc:
            break
    write_json(folder / 'audit.json', dict(runs=results, protected_original_sha256=protected,
                                          original_source_project_db_unchanged=True))
    return results


DIAGNOSIS = '''package require ::quartus::project
package require ::quartus::sta
set out [lindex $quartus(args) 0]
file mkdir $out
project_open apf_top
try {
 create_timing_netlist
 read_sdc
 set corner 0
 foreach_in_collection op [get_available_operating_conditions] {
  set_operating_conditions $op
  update_timing_netlist
  report_timing -setup -npaths 3 -detail full_path -file [file join $out corner${corner}-setup.txt]
  report_timing -hold -npaths 12 -detail full_path -file [file join $out corner${corner}-hold.txt]
  incr corner
 }
 if {$corner!=4} {error "Expected four diagnosis corners"}
} finally {
 catch {delete_timing_netlist}
 project_close -dont_export_assignments
}
'''


def diagnose(project, target, folder):
    # A bounded failing-path check, not sign-off or a new fit.
    script = folder.parent / 'frozen-diagnosis.tcl'
    script.write_text(DIAGNOSIS)
    return private_sta(project, target, folder,
                       [('diagnosis', [script, folder / 'reports'])])


def audit(project, target, folder):
    from tool_paths import ROOT
    scripts = ROOT / 'scripts'
    runs = [('cpu-hold', [scripts / 'audit_pocket_cpu_hold_timing.tcl', folder / 'reports/cpu-hold']),
            ('full', [scripts / 'audit_pocket_full_timing.tcl', folder / 'reports/full', '--native-corners']),
            ('release', [scripts / 'release_timing.tcl'])]
    results = private_sta(project, target, folder, runs)
    margin = folder / 'cpu-hold.log'
    passed = len(results) == 3 and all(r['exit_code'] == 0 for r in results.values()) and (
        'PASS_CPU_HOLD_MARGIN all9_pairs corners4 actual_requirement_delta_0.15' in margin.read_text())
    if passed:
        command = [sys.executable, str(scripts / 'release_timing.py'), str(project),
                   '--log', str(folder / 'release.log'), '--output', str(project / 'retained-timing.json')]
        with (folder / 'policy.log').open('w') as stream:
            passed = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT).returncode == 0
    return dict(passed=passed, extra_cpu_hold_ns=.15, cpu_hold_waivers_accepted=False,
                original_hold_SDC_retained=True, runs=results, board_phy_verified=False)


def run(args):
    output = args.output.resolve()
    if args.mode == 'snapshot':
        snapshot(args.source, args.manifest, output, args.seed)
        return True
    executor_guard()
    provenance = job_context()
    if args.mode == 'diagnose':
        target = load_source(args.source, args.manifest)
        fitted = json.loads((args.source / 'physical-result.json').read_text())
        require(fitted['fit_verified'] and fitted['measurement_valid'] and
                fitted['source_manifest'] == target['quartus_sources'] and
                fitted['project_sha256'] == target['project_sha256'], 'Verified existing fit required')
        require(not output.exists(), 'Fresh diagnostic output required')
        output.mkdir(parents=True)
        write_json(output / 'fit-inputs.json', target)
        write_json(output / 'frozen-snapshot.json', dict(mode='diagnose', source=str(args.source),
                   manifest_sha256=digest(args.manifest), extra_cpu_hold_ns=.15, selected=False))
        result = diagnose(args.source, target, output / 'audit')
        write_json(output / 'frozen-fit-result.json', dict(mode='diagnose', runs=result, jenkins=provenance, selected=False))
        summary = f'RESULT {output.name}: diagnostic only, retained150ps, no new fit or assembly'
        print(summary, flush=True)
        if args.result_file:
            args.result_file.write_text(summary + '\n')
        return all(r['exit_code'] == 0 for r in result.values())
    from tool_paths import find_tool
    from run_resource_fit import fit_target
    from measure_pocket_seeds import measurement
    target = snapshot(args.source, args.manifest, output, args.seed)
    if args.mode == 'map':
        command = [str(Path(find_tool('quartus_sh')).parent / 'quartus_map'), 'apf_top']
        with (output / 'map.log').open('w') as stream:
            rc = subprocess.run(command, cwd=output, stdout=stream, stderr=subprocess.STDOUT,
                                timeout=900).returncode
        require(load_source(output, output / 'fit-inputs.json') == target, 'Map source drift')
        counts = {}
        if rc == 0:
            text = (output / 'output_files/apf_top.map.rpt').read_text(errors='replace')
            root = [[v.strip() for v in line.split(';')[1:-1]] for line in text.splitlines()
                    if line.startswith('; |apf_top ')]
            require(len(root) == 1 and len(root[0]) == 10, 'Missing map hierarchy totals')
            counts = {name: int(root[0][i].split()[0].replace(',', '')) for i, name in
                      [(1, 'aluts'), (2, 'registers'), (3, 'memory_bits'), (4, 'dsp')]}
        write_json(output / 'frozen-fit-result.json', dict(mode='map', exit_code=rc, counts=counts, jenkins=provenance,
                   source_inputs=target['quartus_sources'], fit_verified=False, selected=False))
        summary = f'RESULT {output.name}: map-only rc={rc}, {counts}; no physical-fit claim'
        print(summary, flush=True)
        if args.result_file:
            args.result_file.write_text(summary + '\n')
        return rc == 0
    result = measurement(fit_target(Path(find_tool('quartus_sh')).parent, target, DEVICE, 2400))
    write_json(output / 'physical-result.json', result)
    timing = result.get('reported_timing', {})
    initial_passed = bool(result['measurement_valid'] and result['fit_verified'] and len(result['steps']) == 3 and
                          all(s['exit_code'] == 0 for s in result['steps']) and
                          timing.get('worst_hold_slack_ns') is not None and
                          timing['worst_hold_slack_ns'] >= 0 and
                          not any('general[2]' in r['type'] and 'Setup ' in r['type']
                                  for r in timing.get('negative_slacks', [])))
    report = dict(initial_gate_passed=initial_passed, extra_cpu_hold_ns=.15, jenkins=provenance,
                  selected=False, fit=result)
    if initial_passed:
        report['audit'] = audit(output, target, output / 'audit')
        passed = report['audit']['passed']
        if passed and args.assemble:
            executor_guard()
            log = output / 'asm.log'
            with log.open('w') as stream:
                rc = subprocess.run([str(Path(find_tool('quartus_sh')).parent / 'quartus_asm'), 'apf_top'],
                                    cwd=output, stdout=stream, stderr=subprocess.STDOUT, timeout=600).returncode
            require(load_source(output, output / 'fit-inputs.json') == target, 'Assembly source drift')
            report['assembly_exit_code'] = rc
            passed = rc == 0 and (output / 'output_files/apf_top.rbf').is_file()
    else:
        passed = False
        if result['fit_verified']:
            report['diagnosis'] = diagnose(output, target, output / 'audit')
    report['passed'] = passed
    write_json(output / 'frozen-fit-result.json', report)
    capacity = (f"fits {result.get('required_LABs')} / 1848" if result['fit_verified'] else
                f"no fit; requires {result.get('required_LABs')} LABs")
    summary = (f"RESULT {output.name}: {capacity}, "
               f"retained150ps {'PASS' if passed else 'FAIL'}, "
               f"initial_hold {timing.get('worst_hold_slack_ns')} ns")
    print(summary, flush=True)
    if args.result_file:
        args.result_file.write_text(summary + '\n')
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=4)
    parser.add_argument('--mode', choices=('snapshot', 'map', 'fit', 'diagnose'), default='snapshot')
    parser.add_argument('--assemble', action='store_true')
    parser.add_argument('--result-file', type=Path)
    args = parser.parse_args()
    args.source, args.manifest = args.source.resolve(), args.manifest.resolve()
    sys.exit(0 if run(args) else 1)


if __name__ == '__main__':
    main()
