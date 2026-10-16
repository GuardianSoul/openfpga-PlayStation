#!/usr/bin/env python3
"""Conditional CD timing diagnosis in a protected copy of a completed fit.

This does not prepare or qualify a release. It replaces only the captured
source-only CD payload bounds in the analysis copy; local paths regain their
ordinary clock relationships. The Native 19-track/two-buffer/single fixed
slot profile is the only supported mapped geometry. All original sources,
project bytes and compiled binary catalogs must remain unchanged.
"""
import argparse
import csv
import json
from pathlib import Path
import re
import shutil
import subprocess

from measure_pocket_seeds import inputs
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


REVIEWED_CD = {
    '8e7d306115d672b396c5bece33a256b78220c25bd756638de4db9dfef8b9aa20',
    'eaaa6978025b0f9a8afb84f6f407b37ccb626648804870710fa2c7779281a5dd',
    # 30b7d5b: only the LIBCRYPT_SLOT=0 specialization is supported below.
    '10717e58e9c4e3d36dcfe36964d15920a899814a5ebcd09246f4ef498b0d909a',
}
HELD = ('*|disc_stream|m_valid *|disc_stream|m_end[*] *|disc_stream|m_minutes[*] '
        '*|disc_stream|m_seconds[*] *|disc_stream|m_ntracks[*] *|disc_stream|m_slot[*] '
        '*|disc_stream|resp_buf* *|disc_stream|resp_kind[*] *|disc_stream|req_lba[*] '
        '*|disc_stream|sel_slot[*]')
MAX = f'set_max_delay 13 -from [get_registers {{{HELD}}}]\n'
MINS = tuple(f'set_min_delay {bound} -from [get_registers {{{HELD}}}]\n' for bound in ('0', '-13'))


def replace_bounds(text, scope):
    selected = [line for line in MINS if text.count(line) == 1]
    if text.count(MAX) != 1 or len(selected) != 1:
        raise ValueError('Expected exact known CD source-only bounds')
    result = text.replace(MAX, '', 1).replace(selected[0], '', 1)
    if any(line.startswith(('set_max_delay', 'set_min_delay')) and 'disc_stream|' in line
           for line in result.splitlines()):
        raise ValueError('Unknown additional CD payload bound')
    if any(c in str(scope) for c in ('"', '$', '[', ']', '\n', '\\')):
        raise ValueError('Unsupported diagnostic scope path')
    return result + f'\nsource "{scope}"\n'


def profile(top, cd_sha):
    if cd_sha not in REVIEWED_CD:
        raise ValueError('CD source has not been reviewed for this diagnostic')
    instances = re.findall(r'psx_cd_stream\s*#\((.*?)\)\s+disc_stream\s*\((.*?)\);', top, re.S)
    if len(instances) != 1:
        raise ValueError('Expected one connected CD stream')
    params, ports = instances[0]
    if (params.count('.MAX_TRACKS(19)') != 1 or
            re.findall(r'\.NBUF\((.*?)\)', params) not in ([], ['2']) or
            re.findall(r'\.LIBCRYPT_SLOT\((.*?)\)', params) not in ([], ["16'h0"], ["16'h0000"], ["16'd0"]) or
            ports.count(".disc_select_strobe(1'b0)") != 1 or
            ports.count('.host_clk(clk_74a)') != 1 or ports.count('.clk(clk1x)') != 1):
        raise ValueError('Unsupported CD diagnostic clock/buffer/track/selection profile')


def catalog(directory):
    return {str(p.relative_to(directory)): digest(p) for p in directory.rglob('*')
            if p.is_file() and p.suffix in {'.cdb', '.ddb', '.tdb'}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('completed_project', type=Path)
    parser.add_argument('analysis_copy', type=Path)
    parser.add_argument('--fault', choices=('hidden-local', 'relaxed-local', 'hidden-pair'))
    args = parser.parse_args()
    source, copy = args.completed_project.resolve(), args.analysis_copy.resolve()
    if (copy.exists() or not copy.is_relative_to(ROOT / 'build') or
            subprocess.run(['git', 'check-ignore', '--quiet', str(copy / 'diagnostic-result.json')],
                           cwd=ROOT).returncode):
        raise ValueError('Use a fresh ignored build directory')
    result = json.loads((source / 'result.json').read_text())
    if not (result['fit_verified'] and result['source_identity_verified'] and result['project_identity_verified']
            and len(result['steps']) == 3 and all(s['exit_code'] == 0 for s in result['steps'])):
        raise ValueError('Completed source-verified map/fit/STA required')
    target = json.loads((source / 'timing-inputs.json').read_text())
    before = inputs(source)
    if before != target['quartus_sources'] or any(digest(source / ('apf_top' + ext)) != sha
                                                 for ext, sha in target['project_sha256'].items()):
        raise ValueError('Captured compilation identity differs')
    assigned = assignments(source, (source / 'apf_top.qsf').read_text())
    def one(name):
        found = [p for _, p in assigned if p.name == name]
        if len(found) != 1:
            raise ValueError('Expected one assigned ' + name)
        return found[0]
    profile(one('core_top.sv').read_text(), digest(one('psx_cd_stream.sv')))
    binaries = catalog(source)
    if len(binaries) < 100:
        raise ValueError('Missing complete compiled binary catalogs')
    shutil.copytree(source, copy)
    for name in ('result.json', 'timing-inputs.json'):
        (copy / name).rename(copy / ('parent-' + name))
    scope = copy / 'diagnostic-CD-scope.tcl'
    shutil.copyfile(ROOT / 'scripts/pocket_cd_bundle_diagnostic.tcl', scope)
    pocket = copy / 'pocket.sdc'
    pocket.write_text(replace_bounds(pocket.read_text(), scope))
    copied_inputs = inputs(copy)
    audit = ROOT / 'scripts/audit_pocket_cd_bundle_diagnostic.tcl'
    tools_before = {str(p): digest(p) for p in (Path(__file__), audit,
                    ROOT / 'scripts/pocket_cd_bundle_diagnostic.tcl')}
    command = [str(Path(find_tool('quartus_sh')).parent / 'quartus_sta'), '-t', str(audit),
               str(copy / 'CD-diagnostic'), *(['--fault-' + args.fault] if args.fault else [])]
    log = copy / 'diagnostic.log'
    with log.open('w') as f:
        process = subprocess.run(command, cwd=copy, stdout=f, stderr=subprocess.STDOUT, timeout=300)
    if inputs(source) != before or inputs(copy) != copied_inputs or catalog(source) != binaries or catalog(copy) != binaries:
        raise ValueError('Diagnosis changed original inputs or compiled binary catalogs')
    if any(digest(Path(p)) != sha for p, sha in tools_before.items()):
        raise ValueError('Diagnostic scripts changed while running')
    markers = {'hidden-local': 'CD endpoint timing coverage incomplete',
               'relaxed-local': 'Relaxed local CD relationship',
               'hidden-pair': 'CD physical pair timing coverage incomplete'}
    report = dict(command=command, exit_code=process.returncode,
                  conditional_CD_diagnostic_passed=process.returncode == 0,
                  fault=args.fault, original_and_copy_inputs_unchanged=True,
                  fault_rejected_for_expected_reason=(process.returncode != 0 and
                      markers[args.fault] in log.read_text()) if args.fault else None,
                  compiled_binary_catalogs_identical=True, binary_count=len(binaries),
                  diagnostic_script_identities=tools_before,
                  original_project=str(source), original_inputs=before,
                  analysis_inputs=copied_inputs, log_sha256=digest(log),
                  auditor_sha256=digest(audit), scope_sha256=digest(scope),
                  implementation_frozen_with_new_constraints=False,
                  source_only_bounds_replaced_in_analysis_copy=True,
                  whole_core_timing_closed=False, board_timing_verified=False, hardware_verified=False)
    table = copy / 'CD-diagnostic/paths.tsv'
    if process.returncode == 0:
        rows = list(csv.DictReader(table.open(), delimiter='\t'))
        if {r['corner'] for r in rows} != {'0', '1', '2', '3'} or any(float(r['slack_ns']) <= 0 for r in rows):
            raise ValueError('Incomplete/nonpositive CD diagnostic table')
        report['path_rows'] = len(rows)
        report['minima_ns'] = {group: {kind: min(float(r['slack_ns']) for r in rows
                                               if r['class'] == group and r['kind'] == kind)
                                       for kind in ('setup', 'hold')} for group in ('local', 'crossing', 'sync')}
        report['maximum_crossing_data_ns'] = max(float(r['data_ns']) for r in rows if r['class'] == 'crossing')
    (copy / 'diagnostic-result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('exit_code', 'conditional_CD_diagnostic_passed', 'fault',
                                            'compiled_binary_catalogs_identical', 'whole_core_timing_closed')}))
    if process.returncode:
        raise SystemExit(process.returncode)


if __name__ == '__main__':
    main()
