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
              '9b60631e6477c2b59de3da2e29730675fe99292572f4c0c51db721814536e189',
              '23f4cd8c9a3c94cf1fe30e845b52244517b7982d3c333168fab0c568b2ca5987')   # + --vram-x3 chip clock at the clk3x period
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
    active = []
    for line in subprocess.check_output(['ps', '-eo', 'pid=,comm='], text=True).splitlines():
        pid, _, comm = line.strip().partition(' ')
        if comm.strip() in {'quartus_map', 'quartus_fit', 'quartus_sta', 'quartus_asm'} and not other_job(pid):
            active.append(line)
    require(not active, 'Legacy Quartus job still active; drain the host before Jenkins')


def other_job(pid):
    """True for a Quartus process that is not a PSX fit we must wait for: one in another Jenkins
    workspace on this host (nds-fit, or a second psx-fit build such as psx-fit@2, since every build
    fits in its own workspace), or one outside the PSX trees (e.g. the NDS pre-queue check under
    ~/Git/NDS_Pocket). A process in this build's own workspace, a hand-run PSX fit (a directory
    with "psx" in it, such as ~/Git/PSX_Pocket or ~/psx-fit) or one whose directory can't be read
    blocks."""
    workspace = os.environ.get('WORKSPACE')
    if not workspace:
        return False
    try:
        cwd = Path(os.readlink(f'/proc/{pid}/cwd'))
    except OSError:
        return False
    try:
        rel = cwd.relative_to(Path(workspace).parent)
        return bool(rel.parts) and rel.parts[0] != Path(workspace).name
    except ValueError:
        return not any('psx' in part.lower() for part in cwd.parts)


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


HOLD_GATE_MARKER = 'POCKET_HOLD_GATE_ENFORCE'


def hold_gate_enforced(project):
    """The retained 150 ps initial hold is enforced for a project whose pocket.sdc carries
    POCKET_HOLD_GATE_ENFORCE (a comment line; no step sets it yet: hold-fit-target does not make
    the gate reachable on every seed, docs/hold-constraints-060.md). Without it the gate is
    reported and a hold-only miss is judged by release_timing."""
    sdc = Path(project) / 'pocket.sdc'
    return sdc.is_file() and HOLD_GATE_MARKER in sdc.read_text(errors='replace')


def hold_only_miss(result):
    """A verified, complete fit whose only negative slacks are hold rows."""
    timing = result.get('reported_timing', {})
    rows = timing.get('negative_slacks', [])
    return bool(result.get('measurement_valid') and result.get('fit_verified') and len(result.get('steps', [])) == 3
                and all(s['exit_code'] == 0 for s in result['steps'])
                and timing.get('worst_hold_slack_ns') is not None and timing['worst_hold_slack_ns'] < 0
                and rows and all(' Hold ' in r['type'] for r in rows))


def release_check(project, output):
    """release_timing on a private copy without the CPU hold SDC (the sign-off's rule); the fit
    itself is left untouched. Returns (passed, failure lines)."""
    from tool_paths import ROOT
    executor_guard()
    copy = project.with_name(project.name + '-release')
    require(not copy.exists(), 'Fresh release-check copy required')
    protected = {str(project / ('apf_top' + s)): digest(project / ('apf_top' + s)) for s in ('.qsf', '.qpf')}
    shutil.copytree(project, copy, ignore=shutil.ignore_patterns('*-analysis'))
    try:
        qsf = copy / 'apf_top.qsf'
        q = qsf.read_text()
        # the copy's QSF names the original's absolute paths; drop only the CPU hold SDC line
        hold = f'set_global_assignment -name SDC_FILE "{project}/cpu-hold-margin/pocket_cpu_hold.sdc"\n'
        require(q.count(hold) == 1, 'One assigned CPU hold SDC required for the release check')
        qsf.write_text(q.replace(hold, ''))
        log = output / 'release-timing.out'
        with log.open('w') as stream:
            rc = subprocess.run([sys.executable, str(ROOT / 'scripts/release_timing.py'), str(copy),
                                 '--output', str(output / 'release-timing.json')],
                                stdout=stream, stderr=subprocess.STDOUT, timeout=3600).returncode
    finally:
        shutil.rmtree(copy, ignore_errors=True)
    require(all(digest(p) == h for p, h in protected.items()), 'Original fit changed during release check')
    failures = [l for l in log.read_text(errors='replace').splitlines() if l.startswith('FAIL ')]
    return rc == 0 and not failures, failures


def fit_verdict(initial_passed, enforced, hold_only, release_passed):
    """(passed, gate word) for a chip fit. Strict gate: the retained 150 ps initial hold decides.
    Report-only gate: a hold-only miss is judged by release_timing, as the sign-off judges it."""
    if initial_passed or enforced or not hold_only:
        return None, 'retained150ps'
    return bool(release_passed), 'release_timing'


def setup_path_script(setup_fail, output):
    """quartus_sta script naming the failing setup paths: the top 10 per failing corner and clock
    pair, same-clock rows and crossings alike (no fit, own copy). A release_timing row reads
    'FAIL <corner> setup <slack> <from clock> -> <to clock>: <reason>'."""
    # hold rows too (psx-fit #1163: one 2 ps hold row whose path nothing named)
    wanted = sorted({(l.split()[1], l.split()[4], l.split()[6].rstrip(':'), l.split()[2]) for l in setup_fail
                     if l.split()[2] in ('setup', 'hold')})
    lines = ['package require ::quartus::project', 'package require ::quartus::sta', 'project_open apf_top',
             'create_timing_netlist', 'read_sdc']
    for corner, launch, latch, kind in wanted:
        cond = corner.replace('_Model', '').replace('Slow_1100mV_', '').replace('Fast_1100mV_', '')
        speed = 'slow' if corner.startswith('Slow') else 'fast'
        name = f'paths-{corner}-{len(lines)}.txt' if kind == 'setup' else f'paths-{kind}-{corner}-{len(lines)}.txt'
        lines += [f'foreach_in_collection op [get_available_operating_conditions] {{',
                  f'  if {{[string match -nocase "*{speed}*{cond}*" [get_operating_conditions_info -display_name $op]]}} {{',
                  '    set_operating_conditions $op; update_timing_netlist',
                  f'    report_timing -{kind} -from_clock [get_clocks {{{launch}}}] -to_clock [get_clocks {{{latch}}}] -npaths 10 -detail summary -file {{{Path(output) / name}}}',
                  '  }', '}']
    lines += ['delete_timing_netlist', 'project_close -dont_export_assignments']
    return '\n'.join(lines) + '\n'


def named_path_script(rows, output):
    """quartus_sta script for the request's 'report_paths': setup and hold of each named
    from/to register pattern at every operating condition, one report file per row, check
    and corner (named-<row>-<kind>-corner<n>.txt). A row may set "npaths" (default 2) and
    "detail" (default "summary"; "full_path" adds logic levels and the cell/interconnect
    split, which parse_named_reports returns). The corner number is bound to its name by a
    NAMED_CORNER line in the log; a pattern that matches nothing only loses its own report."""
    lines = ['package require ::quartus::project', 'package require ::quartus::sta', 'project_open apf_top',
             'create_timing_netlist', 'read_sdc', 'set n 0',
             'foreach_in_collection op [get_available_operating_conditions] {',
             '  set_operating_conditions $op; update_timing_netlist; incr n',
             '  puts "NAMED_CORNER $n [get_operating_conditions_info -display_name $op]"']
    for i, row in enumerate(rows):
        detail, npaths = row.get('detail', 'summary'), int(row.get('npaths', 2))
        require(detail in ('summary', 'path_only', 'full_path') and 1 <= npaths <= 50, 'Unsupported report_paths row')
        for kind in ('setup', 'hold'):
            lines.append(f"  if {{[catch {{report_timing -{kind} -from [get_registers {{{row['from']}}}] "
                         f"-to [get_registers {{{row['to']}}}] -npaths {npaths} -detail {detail} "
                         f"-file [file join {{{output}}} named-{i}-{kind}-corner$n.txt]}} err]}} "
                         f"{{ puts \"NAMED_ERROR {i} {kind} $n $err\" }}")
    lines += ['}', 'delete_timing_netlist', 'project_close -dont_export_assignments']
    return '\n'.join(lines) + '\n'


CDC_REPORTS = (('clock-transfers', 'report_clock_transfers -setup -file {f}'),
               ('clock-transfers-hold', 'report_clock_transfers -hold -file {f}'),
               ('metastability', 'report_metastability -file {f}'),
               ('unconstrained', 'report_ucp -summary -file {f}'),
               ('exceptions', 'report_exceptions -file {f}'),
               ('sdc', 'report_sdc -file {f}'),
               ('cdc-viewer', 'report_cdc_viewer -file {f}'))


def cdc_script(output):
    """quartus_sta script for the request's 'cdc_reports': clock transfers (setup and hold),
    synchroniser chains (report_metastability), unconstrained paths, timing exceptions and the
    read SDC, at the default (slow) operating condition, one file each (cdc-<name>.txt). A
    command this Quartus edition lacks only loses its own file (CDC_ERROR line in the log)."""
    lines = ['package require ::quartus::project', 'package require ::quartus::sta', 'project_open apf_top',
             'create_timing_netlist', 'read_sdc', 'update_timing_netlist']
    for name, command in CDC_REPORTS:
        f = '[file join {%s} cdc-%s.txt]' % (output, name)
        lines.append(f'if {{[catch {{{command.format(f=f)}}} err]}} {{ puts "CDC_ERROR {name} $err" }} '
                     f'else {{ puts "CDC_DONE {name}" }}')
    lines += ['delete_timing_netlist', 'project_close -dont_export_assignments']
    return '\n'.join(lines) + '\n'


def parse_full_paths(text):
    """Per-path facts of a report_timing -detail full_path text: slack, end points, clock skew,
    data delay, logic levels, the data path's interconnect and cell totals, and its elements."""
    cells = lambda line: [c.strip() for c in line.split(';')[1:-1]]
    paths = []
    for block in re.split(r'^Path #\d+: ', text, flags=re.M)[1:]:
        fact = dict(slack_ns=float(re.match(r'\S+ slack is (-?[\d.]+)', block).group(1)), elements=[])
        section = table = None
        for line in block.splitlines():
            c = cells(line) if line.startswith(';') else []
            if len(c) == 1:
                table = c[0]
            elif table == 'Path Summary' and len(c) == 2 and c[0] in ('From Node', 'To Node'):
                fact['from' if c[0] == 'From Node' else 'to'] = c[1]
            elif table == 'Statistics' and len(c) == 7:
                if c[0] in ('Arrival Path', 'Required Path'): section = c[0]
                elif c[0] in ('Clock', 'Data') and section: section = section.split('/')[0] + '/' + c[0]
                elif c[0] == 'Clock Skew': fact['clock_skew_ns'] = float(c[1])
                elif c[0] == 'Data Delay': fact['data_delay_ns'] = float(c[1])
                elif c[0] == 'Setup Relationship' or c[0] == 'Hold Relationship': fact['relationship_ns'] = float(c[1])
                elif c[0] == 'Number of Logic Levels': fact['logic_levels'] = int(c[2])
                elif c[0] in ('IC', 'Cell') and section == 'Arrival Path/Data':
                    fact['data_ic_ns' if c[0] == 'IC' else 'data_cell_ns'] = float(c[3])
            elif table == 'Data Arrival Path' and len(c) == 7 and c[3] in ('IC', 'CELL') and 'data' in fact:
                fact['elements'].append(' '.join((c[1], c[3], c[4], c[5], c[6])))
            elif table == 'Data Arrival Path' and len(c) == 7 and c[6] == 'data path':
                fact['data'] = True
        fact.pop('data', None)
        paths.append(fact)
    return paths


def parse_named_reports(rows, output, log_text=''):
    """(named_paths strings, full-path details) from the named-*.txt files of named_path_script."""
    corners = dict(re.findall(r'NAMED_CORNER (\d+) ([^\r\n]*)', log_text))
    named, details = [], []
    for i, row in enumerate(rows):
        label = row.get('name', i)
        for report in sorted(Path(output).glob(f'named-{i}-*-corner*.txt')):
            kind, n = report.stem.split('-')[2], report.stem.split('corner')[-1]
            model = corners.get(n, '').strip()
            text = report.read_text(errors='replace')
            table = re.split(r'^Path #\d+: ', text, flags=re.M)[0]          # the summary table only
            found = [' '.join(l.split(';')[1:4]).strip() for l in table.splitlines() if re.match(r'^; -?\d', l)]
            named += [f'{label} {kind} {model}: {r}' for r in found] or [f'{label} {kind} {model}: no path found']
            details += [dict(name=label, check=kind, corner=model, **fact) for fact in parse_full_paths(text)]
    named += [f"{rows[int(i)].get('name', i)} {kind} {corners.get(n, '').strip()}: report failed: {err.strip()}"
              for i, kind, n, err in re.findall(r'NAMED_ERROR (\d+) (\w+) (\d+) ([^\r\n]*)', log_text)
              if int(i) < len(rows)]
    return named, details


def tail_request(tail):
    """The request's optional 'tail': slack limits below which every path is reported."""
    require(isinstance(tail, dict) and set(tail) <= {'setup_below_ns', 'hold_below_ns', 'max_paths'},
            'Unsupported tail request')
    setup, hold, most = tail.get('setup_below_ns', 0.3), tail.get('hold_below_ns', 0.1), tail.get('max_paths', 5000)
    require(all(type(v) in (int, float) and -5 <= v <= 5 for v in (setup, hold)) and
            type(most) is int and 1 <= most <= 100000, 'Unsupported tail request')
    return dict(setup_below_ns=float(setup), hold_below_ns=float(hold), max_paths=most)


def tail_script(tail, output):
    """quartus_sta script for the request's 'tail': at every operating condition, one setup and
    one hold summary report of every path of every clock pair below the slack limit
    (tail-<check>-corner<n>.txt; the corner number sits outside braces, so each corner has its
    own file). One row per from/to pair (-pairs_only; all paths if that option is refused).
    TAIL_CORNER binds the corner number to its name, TAIL_COUNT gives the number of rows
    (a count at max_paths means the report was cut), TAIL_PORT names the top-level ports."""
    tail = tail_request(tail)
    lines = ['package require ::quartus::project', 'package require ::quartus::sta',
             'proc tail_report {kind limit npaths n file} {',
             '  foreach extra {-pairs_only {}} {',
             '    if {![catch {set r [report_timing -$kind -less_than_slack $limit -npaths $npaths {*}$extra '
             '-detail summary -file $file]} err]} {',
             '      puts "TAIL_COUNT $kind $n [lindex $r 0] [lindex $r 1] [expr {$extra eq {} ? {all_paths} : {pairs_only}}]"',
             '      return', '    }', '  }', '  puts "TAIL_ERROR $kind $n $err"', '}',
             'project_open apf_top', 'create_timing_netlist', 'read_sdc',
             'catch {foreach_in_collection p [get_ports *] { puts "TAIL_PORT [get_port_info -name $p]" }}',
             'set n 0',
             'foreach_in_collection op [get_available_operating_conditions] {',
             '  set_operating_conditions $op; update_timing_netlist; incr n',
             '  puts "TAIL_CORNER $n [get_operating_conditions_info -display_name $op]"']
    for kind in ('setup', 'hold'):
        lines.append(f"  tail_report {kind} {tail[kind + '_below_ns']:.3f} {tail['max_paths']} $n "
                     f"[file join {{{output}}} tail-{kind}-corner$n.txt]")
    lines += ['}', 'delete_timing_netlist', 'project_close -dont_export_assignments']
    return '\n'.join(lines) + '\n'


def register_bank(name):
    """A register's bank: its hierarchical name without the bit. Strips, from the last name
    part only, ~DUPLICATE copies, array indices, a state-machine value (state.IDLE -> state)
    and the bit numbers of RAM block registers (ram_block1a0~portb_address_reg2 ->
    ram_block1a*~portb_address_reg)."""
    head, bar, leaf = name.strip().rpartition('|')
    ram = re.fullmatch(r'(ram_block\d+a)\d+(~[A-Za-z_]\w*?)?\d*', leaf)
    if ram:
        return head + bar + ram[1] + '*' + (ram[2] or '')
    while True:
        cut = re.sub(r'(~DUPLICATE|~_Duplicate_\d+|\[\d+\]|(?<=\w)\.[A-Za-z_]\w*)$', '', leaf)
        if cut == leaf or not cut:
            return head + bar + leaf
        leaf = cut


def parse_tail_reports(output, log_text, tail, periods=None, waivers=()):
    """(groups, info) from the tail-*.txt reports of tail_script. A group is (launch clock,
    latch clock, check, source bank, destination bank). A path is left out only where
    release_timing.py waives its row: different clocks, negative slack, a waiver for that
    clock pair and check (release_timing.covering, clk1x/clk2x/clk3x by period) and slack not
    below the waiver's floor; those are counted in info['waived_excluded']."""
    from release_timing import covering, names
    tail, periods = tail_request(tail), periods or {}
    corners = {n: name.strip() for n, name in re.findall(r'TAIL_CORNER (\d+) ([^\r\n]*)', log_text)}
    ports = {p.strip() for p in re.findall(r'TAIL_PORT ([^\r\n]*)', log_text)}
    counts = {(kind, n): (int(count), mode) for kind, n, count, mode in
              re.findall(r'TAIL_COUNT (\w+) (\d+) (\d+) \S+ (\w+)', log_text)}
    alias = lambda clock: sorted(names(clock, periods) - {clock})[0] if len(names(clock, periods)) > 1 else clock
    groups, waived, reports = {}, {}, []
    for report in sorted(Path(output).glob('tail-*-corner*.txt')):
        kind, n = report.stem.split('-')[1], report.stem.split('corner')[-1]
        corner = corners.get(n, 'corner' + n)
        rows = [[c.strip() for c in l.split(';')[1:-1]] for l in report.read_text(errors='replace').splitlines()
                if re.match(r'^; *-?\d', l)]
        rows = [r for r in rows if len(r) >= 5]
        count, mode = counts.get((kind, n), (len(rows), 'unknown'))
        cut = max(count, len(rows)) >= tail['max_paths']
        reports.append(dict(check=kind, corner=corner, file=report.name, rows=len(rows), rows_mode=mode,
                            limit_ns=tail[kind + '_below_ns'], truncated=cut,
                            complete_below_ns=float(rows[-1][0]) if cut and rows else tail[kind + '_below_ns']))
        for slack, source, sink, launch, latch in (r[:5] for r in rows):
            slack = float(slack)
            if launch != latch and slack < 0:
                waiver = covering({'from': launch, 'to': latch, 'check': kind}, waivers, periods)
                if waiver and slack >= waiver['floor_ns']:
                    w = waived.setdefault((launch, latch, kind), dict(
                        launch_clock=launch, latch_clock=latch, check=kind, floor_ns=waiver['floor_ns'],
                        paths=0, worst_slack_ns=slack))
                    w['paths'] += 1; w['worst_slack_ns'] = min(w['worst_slack_ns'], slack)
                    continue
            key = (launch, latch, kind, register_bank(source), register_bank(sink))
            g = groups.setdefault(key, dict(
                launch_clock=launch, latch_clock=latch, launch=alias(launch), latch=alias(latch), check=kind,
                from_bank=key[3], to_bank=key[4], paths=set(), worst_slack_ns=None, worst_corner=None,
                worst_from=None, worst_to=None, io=source in ports or sink in ports, corners={}))
            g['paths'].add((source, sink))
            c = g['corners'].setdefault(corner, dict(worst_slack_ns=slack, paths=0))
            c['paths'] += 1; c['worst_slack_ns'] = min(c['worst_slack_ns'], slack)
            if g['worst_slack_ns'] is None or slack < g['worst_slack_ns']:
                g.update(worst_slack_ns=slack, worst_corner=corner, worst_from=source, worst_to=sink)
    for g in groups.values():
        g['paths'] = len(g['paths'])
    ordered = sorted(groups.values(), key=lambda g: (g['worst_slack_ns'], g['check'], g['from_bank'], g['to_bank']))
    errors = [f'{kind} {corners.get(n, n)}: {err.strip()}' for kind, n, err in
              re.findall(r'TAIL_ERROR (\w+) (\d+) ([^\r\n]*)', log_text)]
    info = dict(request=tail, corners=[corners[n] for n in sorted(corners, key=int)], reports=reports, errors=errors,
                complete=len(reports) == 8 and len(corners) == 4 and not errors and not any(r['truncated'] for r in reports),
                waiver_clock_periods_known=bool(periods), waived_excluded=sorted(
                    waived.values(), key=lambda w: (w['check'], w['launch_clock'], w['latch_clock'])),
                excluded_rule='different clocks, slack < 0, a release_timing waiver for the clock pair and check, '
                              'slack >= its floor_ns')
    return ordered, info


def short_corner(name):
    """'Slow 1100mV 85C Model' -> 'slow85C'."""
    found = re.search(r'(slow|fast)\D*\d+mV\D*(\d+C)', name, re.I)
    return (found[1].lower() + found[2]) if found else name


def tail_summary(groups, info):
    """tail-summary.txt: one line per group, worst slack first."""
    corners = info['corners']
    lines = [f"tail: setup < {info['request']['setup_below_ns']} ns, hold < {info['request']['hold_below_ns']} ns, "
             f"max {info['request']['max_paths']} rows per report; {len(groups)} groups; "
             + ('complete' if info['complete'] else 'INCOMPLETE (see reports/errors below)')]
    lines.append(' '.join(['check', ' worst', f"{'corner':8}", 'paths'] + [f'{short_corner(c):>8}' for c in corners]
                          + ['clocks', 'from bank -> to bank']))
    for g in groups:
        per = [f"{g['corners'][c]['worst_slack_ns']:8.3f}" if c in g['corners'] else f"{'-':>8}" for c in corners]
        lines.append(' '.join([f"{g['check']:5}", f"{g['worst_slack_ns']:6.3f}", f"{short_corner(g['worst_corner']):8}",
                               f"{g['paths']:5}"] + per + [f"{g['launch']}->{g['latch']}" + (' [io]' if g['io'] else ''),
                                                           f"{g['from_bank']} -> {g['to_bank']}"]))
    for r in info['reports']:
        if r['truncated']:
            lines.append(f"TRUNCATED {r['check']} {r['corner']}: {r['rows']} rows, complete only below {r['complete_below_ns']} ns")
    lines += ['ERROR ' + e for e in info['errors']]
    for w in info['waived_excluded']:
        lines.append(f"waived, not listed: {w['check']} {w['launch_clock']} -> {w['latch_clock']}: {w['paths']} rows, "
                     f"worst {w['worst_slack_ns']} (floor {w['floor_ns']})")
    return '\n'.join(lines) + '\n'


def extra_sdc_lines(request):
    """The request's optional 'extra_sdc': SDC lines the sign-off appends to its copy's
    pocket.sdc before any analysis (timing only: the fit is not redone and nothing is assembled)."""
    lines = request.get('extra_sdc')
    require(isinstance(lines, list) and lines and all(isinstance(l, str) and l.strip() and '\n' not in l
                                                      for l in lines), 'Unsupported extra_sdc request')
    require(not any(k in request for k in ('cue_firmware', 'save_firmware', 'save_abort_probe',
                                           'test_only_assemble_despite_setup_failure')),
            'extra_sdc is a timing-only analysis: no ROM update or assembly request with it')
    return lines


def video_sync_script(patterns, output):
    """quartus_sta script for a project that carries the video sync constraint: at every
    operating condition the setup and hold summary of every path into the covered first
    stages (video-sync-<check>-corner<n>.txt; Slack, nodes, clocks, Relationship, Clock
    Skew, Data Delay)."""
    to = '[get_registers {' + ' '.join(patterns) + '}]'
    lines = ['package require ::quartus::project', 'package require ::quartus::sta', 'project_open apf_top',
             'create_timing_netlist', 'read_sdc', 'set n 0',
             'foreach_in_collection op [get_available_operating_conditions] {',
             '  set_operating_conditions $op; update_timing_netlist; incr n',
             '  puts "VIDEO_SYNC_CORNER $n [get_operating_conditions_info -display_name $op]"']
    for kind in ('setup', 'hold'):
        lines.append(f"  if {{[catch {{report_timing -{kind} -to {to} -npaths 500 -pairs_only -detail summary "
                     f"-file [file join {{{output}}} video-sync-{kind}-corner$n.txt]}} err]}} "
                     f"{{ puts \"VIDEO_SYNC_ERROR {kind} $n $err\" }}")
    lines += ['}', 'delete_timing_netlist', 'project_close -dont_export_assignments']
    return '\n'.join(lines) + '\n'


def parse_video_sync(output, log_text, bound):
    """The new requirement per corner, by name, and its failures as release_timing-style rows.
    A covered path fails with negative setup or hold slack under the constraint, or with a
    Data Delay above the bound; a corner without rows fails too (the constraint matched nothing)."""
    corners = {n: name.strip() for n, name in re.findall(r'VIDEO_SYNC_CORNER (\d+) ([^\r\n]*)', log_text)}
    report, failures = {}, []
    for n in sorted(corners, key=int):
        corner, tag = corners[n], corners[n].replace(' ', '_')
        rows = {}
        for kind in ('setup', 'hold'):
            file = Path(output) / f'video-sync-{kind}-corner{n}.txt'
            cells = [[c.strip() for c in l.split(';')[1:-1]] for l in
                     (file.read_text(errors='replace').splitlines() if file.is_file() else []) if re.match(r'^; *-?\d', l)]
            rows[kind] = [dict(slack_ns=float(c[0]), launch_clock=c[3], latch_clock=c[4], relationship_ns=float(c[5]),
                               clock_skew_ns=float(c[6]), data_delay_ns=float(c[7]), **{'from': c[1], 'to': c[2]})
                          for c in cells if len(c) >= 8]
        entry = dict(paths=len(rows['setup']), setup=rows['setup'], hold_paths=len(rows['hold']))
        if rows['setup'] and rows['hold']:
            worst, slow = min(rows['setup'], key=lambda r: r['slack_ns']), max(rows['setup'], key=lambda r: r['data_delay_ns'])
            hold = min(rows['hold'], key=lambda r: r['slack_ns'])
            entry.update(worst_setup_slack_ns=worst['slack_ns'], worst_setup_from=worst['from'], worst_setup_to=worst['to'],
                         max_data_delay_ns=slow['data_delay_ns'], max_data_delay_from=slow['from'],
                         max_data_delay_to=slow['to'], worst_hold_slack_ns=hold['slack_ns'],
                         worst_hold_from=hold['from'], worst_hold_to=hold['to'])
            for r in rows['setup']:
                if r['slack_ns'] < 0 or r['data_delay_ns'] > bound:
                    failures.append(f"FAIL {tag} setup {min(r['slack_ns'], bound - r['data_delay_ns']):+.3f} "
                                    f"{r['launch_clock']} -> {r['latch_clock']}: video sync bound {bound} ns "
                                    f"(data delay {r['data_delay_ns']}) at {r['to']}")
            failures += [f"FAIL {tag} hold {r['slack_ns']:+.3f} {r['launch_clock']} -> {r['latch_clock']}: "
                         f"video sync window at {r['to']}" for r in rows['hold'] if r['slack_ns'] < 0]
        else:
            failures.append(f'FAIL {tag} video-sync +0.000 video -> cpu: video sync constraint has no reported paths')
        report[corner] = entry
    if len(corners) != 4:
        failures.append('FAIL all video-sync +0.000 video -> cpu: video sync report did not run at four corners')
    errors = [f'{kind} {corners.get(n, n)}: {err.strip()}' for kind, n, err in
              re.findall(r'VIDEO_SYNC_ERROR (\w+) (\d+) ([^\r\n]*)', log_text)]
    return dict(bound_ns=bound, corners=report, errors=errors, failures=failures, passed=not failures)


OLD_VIDEO_FLOOR_NS = -6.0


def old_waiver_summary(timing, exit_code):
    """What the rule before the constraint says about this build: the release_timing rows from
    the video clock into clk1x / clk2x (setup), analysed without the constraint block."""
    from release_timing import names
    periods = timing.get('clocks', {})
    crossing = lambda r: r['check'] == 'setup' and 'video' in r['from'] and names(r['to'], periods) & {'clk1x', 'clk2x'}
    # The -6.0 ns floor is applied here: the waiver file no longer carries it (hold-constraints-060)
    rows = [dict(corner=r['corner'], slack_ns=r['slack'], launch_clock=r['from'], latch_clock=r['to'],
                 verdict='waived' if r['slack'] >= OLD_VIDEO_FLOOR_NS else 'FAIL')
            for key in ('waived', 'failures') for r in timing.get(key, []) if crossing(r)]
    return dict(rule='release_timing_waivers.json "*video*" -> clk1x / clk2x setup, floor -6.0 ns',
                analysed='the same copy with the POCKET_VIDEO_SYNC block taken out of pocket.sdc',
                rows=sorted(rows, key=lambda r: r['slack_ns']),
                worst_slack_ns=min((r['slack_ns'] for r in rows), default=None),
                would_pass=all(r['verdict'] == 'waived' for r in rows) if timing else None,
                release_timing_exit_code=exit_code, release_timing_passed=timing.get('passed'))


def abort_probe_identity(source, request):
    """A diagnostic ROM update requires explicit test-only intent and every base file."""
    if 'save_abort_finish_early' in request:
        require('save_abort_probe' in request and request['save_abort_finish_early'] is True,
                'Early FINISH requires an explicit entry-probe request and boolean true')
    if 'save_abort_kernel_gprs' in request:
        require('save_abort_probe' in request and request['save_abort_kernel_gprs'] is True,
                'Kernel GPRs require an explicit entry-probe request and boolean true')
    if 'save_trace_diagnostic' in request:
        require(request['save_trace_diagnostic'] is True and request.get('save_abort_kernel_gprs') is True
                and 'save_abort_probe' in request,
                'Save trace requires the explicit paired kernel entry probe and boolean true')
    if 'save_abort_probe' not in request:
        return None
    approval = request.get('test_only_assemble_despite_setup_failure')
    require(isinstance(approval, str) and bool(approval.strip()),
            'Entry probe requires recorded test-only assembly authorization')
    want = request['save_abort_probe']
    require(isinstance(want, dict) and set(want) == {'before_sha256', 'after_sha256'} and
            all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) for value in want.values()),
            'Entry probe requires exact before/after ROM hashes')
    protected = request.get('base_sha256', {})
    require(isinstance(protected, dict) and protected and all(not Path(p).is_absolute() and '..' not in Path(p).parts
                              and (source / p).resolve().is_relative_to(source.resolve()) for p in protected),
            'Entry probe needs a closed base identity')
    files = {str(p.relative_to(source)) for p in source.rglob('*')
             if p.is_file() and p != source / 'signoff-request.json'}
    require(set(protected) == files and any(p.startswith('db/') for p in files) and
            {'apf_top.qsf', 'physical-result.json', 'release-timing.json',
             'save-states/psx_save_rom40.hex'} <= files, 'Entry probe base closure is incomplete')
    require(all(digest(source / p) == h for p, h in protected.items()), 'Entry probe baseline drift')
    require(digest(source / 'save-states/psx_save_rom40.hex') == want['before_sha256'],
            'Entry probe fitted ROM hash mismatch')
    if request.get('save_abort_kernel_gprs', False):
        from prepare_save_kernel_gprs import QUALIFIED_CPU
        if request.get('save_trace_diagnostic', False):
            from prepare_save_trace import TRACE_CPU_SHA256, TRACE_LOGGER_SHA256
            QUALIFIED_CPU = TRACE_CPU_SHA256
        # Captures retain the fitted QSF verbatim. Its old workspace may no
        # longer exist; only the closed protected copy is a valid CPU source.
        old = Path(request.get('repoint_from', str(source)))
        require(old.is_absolute(), 'Kernel-GPR capture needs an absolute original prefix')
        cpus = []
        for quoted, bare in re.findall(
                r'(?m)^set_global_assignment -name VHDL_FILE (?:"([^"]+)"|(\S+))',
                (source / 'apf_top.qsf').read_text()):
            selected = Path(quoted or bare)
            if selected.name != 'cpu.vhd':
                continue
            if not selected.is_absolute():
                captured = source / selected
            elif selected.is_relative_to(source):
                captured = selected
            elif selected.is_relative_to(old):
                captured = source / selected.relative_to(old)
            else:
                raise ValueError('Kernel-GPR selected CPU escapes the protected capture')
            require(captured.resolve().is_relative_to(source.resolve()) and
                    str(captured.relative_to(source)) in protected,
                    'Kernel-GPR selected CPU is outside the closed baseline')
            selected_identity = selected if selected.is_absolute() else old / selected
            cpus.append((str(selected_identity), digest(captured)))
        require(len(cpus) == 1 and cpus[0][1] == QUALIFIED_CPU,
                'Kernel-GPR firmware requires the exact qualified paired CPU')
        fitted_cpus = [row for row in json.loads((source / 'physical-result.json').read_text()).get('source_manifest', [])
                       if row.get('assignment') == 'VHDL_FILE' and Path(row['path']).name == 'cpu.vhd']
        require(len(fitted_cpus) == 1 and fitted_cpus[0]['path'] == cpus[0][0] and
                fitted_cpus[0]['sha256'] == QUALIFIED_CPU,
                'Kernel-GPR fitted CPU identity does not match the protected source')
        if request.get('save_trace_diagnostic', False):
            logger_rows = [row for row in json.loads((source / 'physical-result.json').read_text()).get('source_manifest', [])
                           if row.get('assignment') == 'SYSTEMVERILOG_FILE' and Path(row['path']).name == 'psx_save_trace.sv']
            require(len(logger_rows) == 1 and logger_rows[0]['sha256'] == TRACE_LOGGER_SHA256,
                    'Save trace requires the qualified fitted logger')
            logger_path = Path(logger_rows[0]['path'])
            require(logger_path.is_relative_to(old), 'Save trace logger escapes original closure')
            captured_logger = source / logger_path.relative_to(old)
            require(str(captured_logger.relative_to(source)) in protected and
                    digest(captured_logger) == TRACE_LOGGER_SHA256,
                    'Save trace logger does not match protected capture')
    baseline = json.loads((source / 'release-timing.json').read_text())
    require(type(baseline.get('passed')) is bool, 'Entry probe baseline timing result is untrusted')
    return protected


def signoff(args):
    """Sign off an existing verified fit (no new fit): release_timing.py and quartus_asm on a fresh
    copy, under the per-host psx-fit lock and executor guard, so nobody runs Quartus by hand on a fit
    host (psx-fit #710). Reached through the job's existing FROZEN_MODE=diagnose when the source
    project holds signoff-request.json (the job's mode choice has no 'signoff' entry yet).
    Like the legacy sign-off it removes the assigned 150 ps CPU hold SDC from the copy before
    release_timing (that margin is the fit's retained gate, reported separately, never hidden):
    the result states the fit's retained-gate hold slack and every release_timing failure."""
    from tool_paths import ROOT, find_tool
    source, output = args.source, args.output.resolve()
    fitted = json.loads((source / 'physical-result.json').read_text())
    require(fitted.get('fit_verified') and fitted.get('measurement_valid'), 'Verified existing fit required')
    require(not output.exists(), 'Fresh sign-off output required')
    request = json.loads((source / 'signoff-request.json').read_text())
    if 'tail' in request:
        tail_request(request['tail'])        # a bad tail request stops before any copy
    extra_sdc = extra_sdc_lines(request) if 'extra_sdc' in request else None
    protected = abort_probe_identity(source, request)
    # A ROM update (cue firmware or the save-state handler) re-uses a signed-off placement: only
    # the ROM contents change (quartus_cdb --update_mif), never the graph.
    rom_update = 'cue_firmware' in request or 'save_firmware' in request
    require(not ('cue_firmware' in request and 'save_firmware' in request), 'One ROM update per sign-off')
    require(not (rom_update and protected), 'One ROM update per sign-off')
    if rom_update:
        baseline = json.loads((source / 'release-timing.json').read_text())
        require(baseline.get('passed') is True, 'ROM update needs a passing baseline release timing')
        rom_base = request.get('base_sha256', {})
        require(rom_base and all(not Path(p).is_absolute() and '..' not in Path(p).parts
                                 for p in rom_base), 'ROM update needs a closed base identity')
        require(all(digest(source / p) == h for p, h in rom_base.items()), 'ROM update baseline drift')
    executor_guard()
    shutil.copytree(source, output)
    if rom_update:
        require(all(digest(source / p) == h for p, h in rom_base.items()), 'ROM update baseline changed during copy')
    if protected:
        require(all(digest(source / p) == h for p, h in protected.items()),
                'Entry probe baseline changed during copy')
    old = request.get('repoint_from', str(source))
    TEXT = {'.qsf', '.qpf', '.sdc', '.tcl', '.sv', '.v', '.vhd', '.json', '.txt', '.mif', '.hex', '.svh', '.vh'}
    for path in output.rglob('*'):
        if path.is_file() and 'db' not in path.relative_to(output).parts[:1] and \
                'incremental_db' not in path.relative_to(output).parts[:1] and path.suffix in TEXT:
            text = path.read_text(errors='surrogateescape')
            if old in text or str(source) in text:
                path.write_text(text.replace(old, str(output)).replace(str(source), str(output)), errors='surrogateescape')
    if extra_sdc:
        # timing-only: the placed and routed fit is analysed under added constraints, never refitted
        sdc = output / 'pocket.sdc'
        sdc.write_text(sdc.read_text().rstrip('\n') + '\n' + '\n'.join(extra_sdc) + '\n')
    cue_update = None
    if rom_update:
        from measure_pocket_seeds import inputs
        before_inputs = inputs(output)
        # Reject stale/foreign ROM contents before touching the fitter database.
        if 'cue_firmware' in request:
            from update_cue_fit import stage
            lanes = stage(output, ROOT / 'rtl/pocket', request['cue_firmware'])
            names, kind = set(request['cue_firmware']), 'cue'
        else:
            # the handler ROM as this checkout builds it (build_save_fw.py: both committed ROM hex
            # files; 'rom' names a diagnosis pair such as psx_save_fw_probe.hex, default the handler)
            from update_save_fit import stage
            from build_save_fw import rom40_from_byte_hex
            want = dict(request['save_firmware'])
            new_rom = output.parent / (output.name + '.save-rom40.hex')
            # a fit with the extension ROM (prepare_save_rom_ext.py): 'ext' = {hex, before_sha256,
            # after_sha256} names the new psx_save_rom_ext.hex; both ROMs are updated together
            new_ext = None
            if 'ext' in want:
                want['ext'] = dict(want['ext'])
                given_ext = Path(want['ext'].pop('hex', ''))
                require('rom40' in want and given_ext.is_absolute() and given_ext.is_file(),
                        'save_firmware ext needs rom40 and hex, an absolute path to an existing extension image')
                new_ext = output.parent / (output.name + '.save-rom-ext.hex')
                new_ext.write_bytes(given_ext.read_bytes())
            if 'rom40' in want:
                # the staged routine (staged_handler.S and its format steps): a finished 1024 x 40
                # image, e.g. the psx_save_rom40.hex a prepare step wrote; after_sha256 binds it
                given = Path(want.pop('rom40'))
                require(given.is_absolute() and given.is_file() and 'rom' not in want,
                        'save_firmware rom40 must be an absolute path to an existing image (and no rom name)')
                new_rom.write_bytes(given.read_bytes())
            else:
                name = want.pop('rom', 'psx_save_fw.hex')
                require(re.fullmatch(r'psx_save_fw(_[a-z0-9]+)?\.hex', name) is not None, 'Unknown save ROM name')
                first = ROOT / 'rtl/pocket' / name
                new_rom.write_text(rom40_from_byte_hex(first, first.with_name(first.stem + '2' + first.suffix)))
            lanes = stage(output, new_rom, want, new_ext) if new_ext else stage(output, new_rom, want)
            # with the extension: one of the two may stay as it is, not both
            names, kind = {Path(r['lane']).name for r in lanes
                           if len(lanes) == 1 or r['before_sha256'] != r['after_sha256']}, 'save'
            require(names, 'ROM update changes neither ROM')
        after_inputs = inputs(output)
        require([dict(r, sha256='') for r in before_inputs] ==
                [dict(r, sha256='') for r in after_inputs], 'ROM update changed the selected graph')
        changed = {Path(a['path']).name for a, b in zip(before_inputs, after_inputs)
                   if a['sha256'] != b['sha256']}
        require(changed == names, 'ROM update changed non-ROM inputs')
        (output / 'output_files/apf_top.rbf').unlink(missing_ok=True)
        executor_guard()
        with (output / 'update-mif.log').open('w') as stream:
            rc = subprocess.run([str(Path(find_tool('quartus_sh')).parent / 'quartus_cdb'),
                                 'apf_top', '--update_mif'], cwd=output,
                                stdout=stream, stderr=subprocess.STDOUT, timeout=900).returncode
        cue_update = dict(kind=kind, lanes=lanes, update_mif_exit_code=rc, refitted=False)
        write_json(output / f'{kind}-firmware-update.json', cue_update)
        require(rc == 0, f'Quartus {kind} firmware update failed; no assembly permitted')
        require(all(digest(output / r['lane']) == r['after_sha256'] and
                    digest(output / 'db' / r['mif']) == r['mif_after_sha256'] for r in lanes),
                f'{kind} ROM contents drifted during cdb update')
    probe_update = None
    if protected:
        from measure_pocket_seeds import inputs
        from build_save_abort_probe import build
        from update_save_fit import LANE, stage
        before_inputs = inputs(output)
        hold_guard(output, before_inputs)
        probe_dir = output.parent / (output.name + '.entry-probe')
        probe = build(probe_dir, early_finish=request.get('save_abort_finish_early', False),
                      kernel_gprs=request.get('save_abort_kernel_gprs', False))
        rom = probe_dir / 'psx_save_rom40.hex'
        lanes = stage(output, rom, request['save_abort_probe'])
        after_inputs = inputs(output)
        require([dict(r, sha256='') for r in before_inputs] == [dict(r, sha256='') for r in after_inputs],
                'Entry probe changed the selected graph')
        changed = {a['path'] for a, b in zip(before_inputs, after_inputs) if a['sha256'] != b['sha256']}
        expected_changes = (set() if request['save_abort_probe']['before_sha256'] ==
                            request['save_abort_probe']['after_sha256'] else {str(output / LANE)})
        require(changed == expected_changes, 'Entry probe changed non-ROM inputs or unexpected ROM identity')
        (output / 'output_files/apf_top.rbf').unlink(missing_ok=True)
        executor_guard()
        with (output / 'update-mif.log').open('w') as stream:
            rc = subprocess.run([str(Path(find_tool('quartus_sh')).parent / 'quartus_cdb'),
                                 'apf_top', '--update_mif'], cwd=output,
                                stdout=stream, stderr=subprocess.STDOUT, timeout=900).returncode
        require(rc == 0, 'Entry probe ROM update failed; no assembly permitted')
        require(all(digest(output / row['lane']) == row['after_sha256'] and
                    digest(output / 'db' / row['mif']) == row['mif_after_sha256'] for row in lanes),
                'Entry probe ROM contents drifted during cdb update')
        probe_update = dict(refitted=False, test_only=True, probe=probe, lanes=lanes,
                            baseline_timing_passed=json.loads((source / 'release-timing.json').read_text())['passed'])
        write_json(output / 'save-abort-update.json', probe_update)
    qsf = output / 'apf_top.qsf'
    hold = f'set_global_assignment -name SDC_FILE "{output}/cpu-hold-margin/pocket_cpu_hold.sdc"\n'
    q = qsf.read_text()
    # Diagnostic ROM updates keep the fitted margin assigned throughout.
    # The legacy sign-off path retains its existing separate-audit behavior.
    hold_removed = not protected and q.count(hold) == 1
    if protected:
        require(q.count(hold) == 1, 'Entry probe must retain the assigned 150 ps margin')
    else:
        qsf.write_text(q.replace(hold, ''))
    # A project that carries the video sync constraint (prepare_video_sync_constraints.py marker in
    # pocket.sdc): first the rule of before, on the same copy with the block taken out, reported
    # alongside; then the verdict run below with the block in place.
    sync_bound = old_waiver = None
    if (output / 'pocket.sdc').is_file() and 'POCKET_VIDEO_SYNC' in (output / 'pocket.sdc').read_text(errors='replace'):
        from prepare_video_sync_constraints import marker_bound, without_block, patterns as sync_patterns
        with_block = (output / 'pocket.sdc').read_text()
        sync_bound = marker_bound(with_block)
        (output / 'pocket.sdc').write_text(without_block(with_block))
        old_json = output / 'release-timing-old-waiver.json'
        with (output / 'release-timing-old-waiver.out').open('w') as stream:
            old_rc = subprocess.run([sys.executable, str(ROOT / 'scripts/release_timing.py'), str(output),
                                     '--output', str(old_json)],
                                    stdout=stream, stderr=subprocess.STDOUT, timeout=3600).returncode
        (output / 'pocket.sdc').write_text(with_block)
        old_waiver = old_waiver_summary(json.loads(old_json.read_text()) if old_json.is_file() else {}, old_rc)
    sta_log = output / 'release-timing.out'
    with sta_log.open('w') as stream:
        rt = subprocess.run([sys.executable, str(ROOT / 'scripts/release_timing.py'), str(output),
                             '--output', str(output / 'release-timing.json')],
                            stdout=stream, stderr=subprocess.STDOUT, timeout=3600).returncode
    text = sta_log.read_text(errors='replace')
    failures = [l for l in text.splitlines() if l.startswith('FAIL ')]
    setup_fail = [l for l in failures if ' setup ' in l]
    rows = [l.split() for l in (ROOT / 'build/release/timing-audit/release-timing.log').read_text(errors='replace').splitlines()
            if l.startswith('RELEASE_TIMING ')] if (ROOT / 'build/release/timing-audit/release-timing.log').exists() else []
    worst = {}
    for r in rows:
        if len(r) >= 8 and r[5] == r[7]:
            key = (r[1], r[2]); s = float(r[4])
            if key not in worst or s < worst[key][0]: worst[key] = (s, r[5])
    # every crossing's worst slack per check (all corners), so the archived result keeps the table
    # that release-timing.json holds (the latter is not archived and the workspace is reused)
    crossing = {}
    for r in rows:
        if len(r) >= 8 and r[5] != r[7]:
            key = f'{r[2]} {r[5]} -> {r[7]}'; s = float(r[4])
            if key not in crossing or s < crossing[key]['slack_ns']: crossing[key] = dict(slack_ns=s, corner=r[1])
    sync = None
    if sync_bound is not None:
        script = output / 'signoff-video-sync.tcl'
        script.write_text(video_sync_script(sync_patterns(), output))
        executor_guard()
        with (output / 'signoff-video-sync.log').open('w') as stream:
            subprocess.run([str(Path(find_tool('quartus_sh')).parent / 'quartus_sta'), '-t', str(script)],
                           cwd=output, stdout=stream, stderr=subprocess.STDOUT, timeout=1800)
        sync = parse_video_sync(output, (output / 'signoff-video-sync.log').read_text(errors='replace'), sync_bound)
        # the covered endpoints answer to the bound, not to the waiver: a miss there fails the build
        failures += sync['failures']
        setup_fail += [l for l in sync['failures'] if ' setup ' in l or ' video-sync ' in l]
    passed = rt == 0 and not (sync and sync['failures'])
    paths = None
    if failures:
        script = output / 'signoff-paths.tcl'
        script.write_text(setup_path_script(failures, output))
        executor_guard()
        with (output / 'signoff-paths.log').open('w') as stream:
            subprocess.run([str(Path(find_tool('quartus_sh')).parent / 'quartus_sta'), '-t', str(script)],
                           cwd=output, stdout=stream, stderr=subprocess.STDOUT, timeout=1800)
        paths = []
        for report in sorted(output.glob('paths-*.txt')):
            paths += [f'{report.stem}: ' + ' '.join(l.split(';')[1:4]).strip()
                      for l in report.read_text(errors='replace').splitlines() if re.match(r'^; -?\d', l)]
    # Optional: slack of named paths at every corner (request 'report_paths': a list of
    # {"name", "from", "to"} register patterns, each optionally with "npaths" and "detail"),
    # e.g. a crossing whose margin a review asked for, or the full path of a failing group.
    named = named_details = None
    if request.get('report_paths'):
        script = output / 'signoff-named-paths.tcl'
        script.write_text(named_path_script(request['report_paths'], output))
        executor_guard()
        with (output / 'signoff-named-paths.log').open('w') as stream:
            subprocess.run([str(Path(find_tool('quartus_sh')).parent / 'quartus_sta'), '-t', str(script)],
                           cwd=output, stdout=stream, stderr=subprocess.STDOUT, timeout=1800)
        named, named_details = parse_named_reports(
            request['report_paths'], output, (output / 'signoff-named-paths.log').read_text(errors='replace'))
    # Optional: clock-domain-crossing reports (request 'cdc_reports': true), see cdc_script.
    if request.get('cdc_reports') is True:
        script = output / 'signoff-cdc.tcl'
        script.write_text(cdc_script(output))
        executor_guard()
        with (output / 'signoff-cdc.log').open('w') as stream:
            subprocess.run([str(Path(find_tool('quartus_sh')).parent / 'quartus_sta'), '-t', str(script)],
                           cwd=output, stdout=stream, stderr=subprocess.STDOUT, timeout=1800)
    # Optional: the whole tight tail (request 'tail': {"setup_below_ns", "hold_below_ns",
    # "max_paths"}): every path of every clock pair below the limits at each corner, grouped by
    # register bank. Runs in this copy after the hold-SDC step above, so under the same
    # constraints as the release_timing verdict.
    tail_groups = tail_info = None
    if 'tail' in request:
        script = output / 'signoff-tail.tcl'
        script.write_text(tail_script(request['tail'], output))
        executor_guard()
        with (output / 'signoff-tail.log').open('w') as stream:
            subprocess.run([str(Path(find_tool('quartus_sh')).parent / 'quartus_sta'), '-t', str(script)],
                           cwd=output, stdout=stream, stderr=subprocess.STDOUT, timeout=3600)
        timing_file = output / 'release-timing.json'
        periods = json.loads(timing_file.read_text()).get('clocks', {}) if timing_file.is_file() else {}
        from release_timing import WAIVERS
        tail_groups, tail_info = parse_tail_reports(
            output, (output / 'signoff-tail.log').read_text(errors='replace'), request['tail'], periods,
            json.loads(WAIVERS.read_text())['waivers'])
        tail_info['cpu_hold_sdc_removed'] = hold_removed
        (output / 'tail-summary.txt').write_text(tail_summary(tail_groups, tail_info))
    trace_bundle = None
    if request.get('save_trace_diagnostic', False):
        executor_guard()
        trace_log = output / 'save-trace-bundle.log'
        with trace_log.open('w') as stream:
            trace_rc = subprocess.run([str(Path(find_tool('quartus_sh')).parent / 'quartus_sta'),
                                       '-t', str(ROOT / 'scripts/audit_save_trace_bundle.tcl'),
                                       str(output / 'save-trace-bundle')], cwd=output,
                                      stdout=stream, stderr=subprocess.STDOUT, timeout=1800).returncode
        trace_bundle = dict(exit_code=trace_rc, log_sha256=digest(trace_log),
                            passed=trace_rc == 0 and 'PASS_SAVE_TRACE_BUNDLE corners4 ' in trace_log.read_text())
        require(trace_bundle['passed'], 'Save trace frozen-header bundle timing is unverified; no assembly')
    asm_rc = None
    # A test-only core may be assembled despite a setup miss only when the request names the owner's
    # approval; the result then carries it (test_only_setup_failure_approved), and it is never a release.
    approved = request.get('test_only_assemble_despite_setup_failure')
    if (passed or (failures and not setup_fail) or (setup_fail and approved)) and not extra_sdc:   # an empty failure list with rt != 0 is an untrusted table: never
        executor_guard()
        with (output / 'asm.log').open('w') as stream:
            asm_rc = subprocess.run([str(Path(find_tool('quartus_sh')).parent / 'quartus_asm'), 'apf_top'],
                                    cwd=output, stdout=stream, stderr=subprocess.STDOUT, timeout=900).returncode
    rbf = output / 'output_files/apf_top.rbf'
    if cue_update is not None:
        # The current source closure changes, while physical counts still belong to
        # the unchanged base placement. Do not relabel the old fit as a fresh fit.
        write_json(output / 'fit-inputs.json', dict(
            top='apf_top', quartus_sources=inputs(output),
            project_sha256={s: digest(output / ('apf_top' + s)) for s in ('.qsf', '.qpf')},
            fixed_ports={}, physical_clock_ports=['clk_74a', 'clk_74b']))
    retained = fitted.get('reported_timing', {}).get('worst_hold_slack_ns')
    result = dict(mode='signoff', source=str(source), cue_firmware_update=cue_update, release_timing_exit_code=rt,
                  release_timing_passed=passed, release_timing_failures=failures,
                  setup_failures=setup_fail, hold_only_miss=bool(failures) and not setup_fail,
                  worst_same_clock={f'{c} {k}': {'slack_ns': s, 'clock': clk} for (c, k), (s, clk) in sorted(worst.items())},
                  worst_crossing=dict(sorted(crossing.items())),
                  retained150ps_initial_hold_ns=retained, cpu_hold_sdc_removed_for_release_timing=hold_removed,
                  setup_paths=paths, named_paths=named, named_path_details=named_details, test_only_setup_failure_approved=approved if setup_fail else None,
                  assembly_exit_code=asm_rc, rbf_sha256=digest(rbf) if rbf.is_file() else None,
                  jenkins=job_context(), selected=False, save_abort_update=probe_update,
                  save_trace_bundle=trace_bundle)
    if sync is not None:
        result.update(video_sync_constraint=sync, video_crossing_old_waiver=old_waiver)
    if extra_sdc:
        result.update(extra_sdc=extra_sdc, timing_only=True)
    if tail_info is not None:      # keys only when asked for: no 'tail' request, the result of before
        result.update(tail=tail_groups, tail_info=tail_info)
    write_json(output / 'frozen-fit-result.json', result)
    status = 'passed' if passed else ('hold-only miss' if result['hold_only_miss'] else 'FAILED (setup)')
    summary = (f"RESULT {output.name}: sign-off of an existing fit; release_timing {status} "
               f"({len(failures)} failing rows); retained150ps initial_hold {retained} ns; "
               f"asm rc={asm_rc}; rbf {'present' if rbf.is_file() else 'missing'}")
    print(summary, flush=True)
    for line in failures:
        print(line, flush=True)
    for line in paths or []:
        print('PATH ' + line, flush=True)
    for line in named or []:
        print('NAMED ' + line, flush=True)
    if sync is not None:
        for corner, c in sync['corners'].items():
            print(f"VIDEO_SYNC {corner}: {c['paths']} paths, bound {sync['bound_ns']} ns, worst setup slack "
                  f"{c.get('worst_setup_slack_ns')} at {c.get('worst_setup_to')}, max data delay "
                  f"{c.get('max_data_delay_ns')} at {c.get('max_data_delay_to')}, worst hold slack "
                  f"{c.get('worst_hold_slack_ns')}", flush=True)
        print(f"VIDEO_SYNC old waiver rule: worst {old_waiver['worst_slack_ns']} ns, would pass: "
              f"{old_waiver['would_pass']}", flush=True)
    if tail_info is not None:
        print((output / 'tail-summary.txt').read_text(), flush=True)
    if args.result_file:
        args.result_file.write_text(summary + '\n' + ''.join(l + '\n' for l in failures))
    if extra_sdc:
        return passed                  # timing only: nothing is assembled
    return asm_rc == 0 and rbf.is_file() and (not setup_fail or bool(approved))


def run(args):
    output = args.output.resolve()
    if args.mode == 'snapshot':
        snapshot(args.source, args.manifest, output, args.seed)
        return True
    require(os.environ.get('BUILD_URL', '').startswith('https://build.session-zero.dev/'),
            'Run physical tools and audits through the psx-fit Jenkins queue')
    executor_guard()
    provenance = job_context()
    if args.mode == 'diagnose' and (args.source / 'signoff-request.json').is_file():
        return signoff(args)
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
    enforced = hold_gate_enforced(output)
    hold_only = not initial_passed and hold_only_miss(result)
    release_passed = release_failures = None
    if hold_only and not enforced:
        try:
            release_passed, release_failures = release_check(output, output)
        except Exception as error:   # recorded, never a pass; the result file is still written
            release_passed, release_failures = False, [f'release check error: {error}']
    verdict, gate = fit_verdict(initial_passed, enforced, hold_only, release_passed)
    if verdict is not None:
        passed = verdict
    report.update(hold_gate='enforced' if enforced else 'report-only', hold_only_miss=hold_only,
                  release_timing_passed=release_passed, release_timing_failures=release_failures)
    report['passed'] = passed
    write_json(output / 'frozen-fit-result.json', report)
    capacity = (f"fits {result.get('required_LABs')} / 1848" if result['fit_verified'] else
                f"no fit; requires {result.get('required_LABs')} LABs")
    summary = (f"RESULT {output.name}: {capacity}, "
               f"{gate} {'PASS' if passed else 'FAIL'}, "
               f"initial_hold {timing.get('worst_hold_slack_ns')} ns")
    if gate == 'release_timing':
        summary += (f"; retained150ps MISS (report-only: no {HOLD_GATE_MARKER} in pocket.sdc); "
                    f"not assembled, sign-off assembles")
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
