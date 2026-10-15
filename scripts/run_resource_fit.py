#!/usr/bin/env python3
"""Run Quartus map/fit/STA for PSX component area targets; no Pocket bitstream."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import tempfile

if __package__:
    from .prepare_resource_targets import COMPILE_PROFILES, TARGET_NAMES, apply_compile_profile, apply_reset_only, digest, prepare
    from .resource_report import parse_summary
    from .tool_paths import ROOT, find_tool
else:
    from prepare_resource_targets import COMPILE_PROFILES, TARGET_NAMES, apply_compile_profile, apply_reset_only, digest, prepare
    from resource_report import parse_summary
    from tool_paths import ROOT, find_tool


def validated_counts(text, top, device):
    """Reject failed fits, other projects/devices and incomplete summaries."""
    expected = {'Fitter Status': r'Successful\b[^\n]*',
                'Revision Name': re.escape(top),
                'Top-level Entity Name': re.escape(top),
                'Device': re.escape(device)}
    for label, value in expected.items():
        if not re.search(r'^' + re.escape(label) + r'\s*:\s*' + value + r'\s*$', text, re.M):
            raise ValueError(f'Unexpected or missing {label} in component fit summary')
    return parse_summary(text)


def timing_metrics(text):
    """Only classify reported slacks; incomplete constraints cannot prove closure."""
    entries=[{'type':kind,'slack_ns':float(value)} for kind,value in
             re.findall(r'^Type\s*:\s*(.*?)\nSlack\s*:\s*(-?\d+(?:\.\d+)?)',text,re.M)]
    return {'worst_setup_slack_ns':min((e['slack_ns'] for e in entries if ' Setup ' in e['type']),default=None),
            'worst_hold_slack_ns':min((e['slack_ns'] for e in entries if ' Hold ' in e['type']),default=None),
            'timing_violations_reported':any(e['slack_ns']<0 for e in entries) if entries else None,
            'negative_slacks':[e for e in entries if e['slack_ns']<0],
            'board_timing_verified':False}


def fit_target(binary_dir, target, device, timeout):
    directory = Path(target['directory'])
    log = directory / 'quartus.log'
    result = {'top': target['top'], 'directory': str(directory), 'steps': [],
              'counts': {}, 'errors': [], 'fit_verified': False,
              'board_timing_verified': False, 'source_manifest': target['quartus_sources'],
              'project_sha256': target['project_sha256'], 'fixed_ports': target['fixed_ports'],
              'physical_clock_ports': target['physical_clock_ports']}
    for key in ('included_components','components_connected','physical_pins','limitations','compile_profile','compile_settings','reset_only_specialization','gte_adapter','pocket_controls','cd_arithmetic','no_turbo_cache','gpu_line_share','gpu_divider_pool','gpu_timing_prune','spu_context_ram','gpu_coordinates','gpu_borrowed'):
        if key in target:
            result[key]=target[key]
    with log.open('w') as output:
        for tool in ('quartus_map', 'quartus_fit', 'quartus_sta'):
            command = [str(binary_dir / tool), target['top']]
            output.write(' '.join(command) + '\n')
            output.flush()
            try:
                completed = subprocess.run(command, cwd=directory, stdout=output,
                                           stderr=subprocess.STDOUT, timeout=timeout)
                result['steps'].append({'tool': tool, 'exit_code': completed.returncode})
                if completed.returncode:
                    result['errors'].append(f'{tool} failed ({completed.returncode}); see {log}')
                    break
                output.flush()
                if tool == 'quartus_map' and 'clock port is fed by virtual pin' in log.read_text():
                    result['errors'].append(f'Clock input was virtualized; invalid component target. See {log}')
                    break
            except (OSError, subprocess.TimeoutExpired) as error:
                result['steps'].append({'tool': tool, 'exit_code': None})
                result['errors'].append(f'{tool}: {error}; see {log}')
                break
    # Each run has a newly created directory. Never import counts from a previous fit.
    analysis = directory / 'output_files' / (target['top'] + '.map.summary')
    if analysis.is_file():
        result['synthesis_summary']={'path':str(analysis),'sha256':digest(analysis),'text':analysis.read_text()}
    result['tool_error_messages']=re.findall(r'^Error[^\n]*',log.read_text(errors='replace'),re.M)
    summary = directory / 'output_files' / (target['top'] + '.fit.summary')
    changed_sources=[]
    for source in target['quartus_sources']:
        try: unchanged=digest(Path(source['path']))==source['sha256']
        except OSError: unchanged=False
        if not unchanged: changed_sources.append(source['path'])
    result['source_identity_verified']=not changed_sources
    if changed_sources: result['errors'].append('Sources changed during resource run: '+', '.join(changed_sources))
    changed_projects=[]
    for suffix,expected in target['project_sha256'].items():
        path=directory/(target['top']+suffix)
        try: unchanged=digest(path)==expected
        except OSError: unchanged=False
        if not unchanged: changed_projects.append(str(path))
    result['project_identity_verified']=not changed_projects
    if changed_projects: result['errors'].append('Project constraints changed during resource run: '+', '.join(changed_projects))
    fit_succeeded = any(step['tool'] == 'quartus_fit' and step['exit_code'] == 0
                        for step in result['steps']) and not changed_sources and not changed_projects
    if fit_succeeded:
        try:
            result['counts'] = validated_counts(summary.read_text(), target['top'], device)
            result['fit_summary'] = {'path': str(summary), 'sha256': digest(summary)}
            result['fit_verified'] = True
        except (OSError, ValueError) as error:
            result['errors'].append(str(error))
    elif summary.is_file():
        result['failed_fit_summary']={'path':str(summary),'sha256':digest(summary),'text':summary.read_text()}
    timing = directory / 'output_files' / (target['top'] + '.sta.summary')
    if timing.is_file():
        result['timing_summary'] = {'path': str(timing), 'sha256': digest(timing),
                                    'text': timing.read_text()}
        result['reported_timing']=timing_metrics(timing.read_text())
    elif any(step['tool'] == 'quartus_sta' and step['exit_code'] == 0 for step in result['steps']):
        result['errors'].append(f'Missing timing summary: {timing}')
    # A successful STA exit does not assert timing closure or cover virtual interfaces.
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin', type=Path, help='Directory containing quartus_sh/map/fit/sta')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/resources')
    parser.add_argument('--timeout', type=int, default=1800, help='Seconds allowed per tool step')
    parser.add_argument('--targets',nargs='+',choices=TARGET_NAMES,help='Components to measure; cluster must be selected alone')
    parser.add_argument('--profile',choices=COMPILE_PROFILES,default='baseline',help='Compiler settings; no RTL changes')
    parser.add_argument('--reset-only',action='store_true',help='Candidate reset-mode specialization; psx_top alone')
    parser.add_argument('--gte-adapter',action='store_true',help='Experimental Laxer GTE CE/read-port adaptation; gte or psx_top alone')
    parser.add_argument('--pocket-controls',action='store_true',help='One digital pad, two cards; psx_top or joypad alone')
    parser.add_argument('--cd-arithmetic',action='store_true',help='Exact bounded CD arithmetic adaptation; psx_top or cd_top alone')
    parser.add_argument('--no-turbo-cache',action='store_true',help='Remove optional turbo data cache with controls off; psx_top or cpu alone')
    parser.add_argument('--gpu-line-share',action='store_true',help='Experimental shared scanline attribute multiplier; gpu or psx_top alone')
    parser.add_argument('--gpu-divider-pool',action='store_true',help='Experimental shared GPU divider; gpu or psx_top alone')
    parser.add_argument('--gpu-timing-prune',action='store_true',help='Omit disabled polygon timing bookkeeping; GPU or system alone')
    parser.add_argument('--spu-context-ram',action='store_true',help='Experimental falling-edge SPU context RAM; SPU or reset-only system alone')
    parser.add_argument('--gpu-coordinates',action='store_true',help='Register negated GPU coordinates in existing boundary setup; GPU or system alone')
    parser.add_argument('--gpu-borrowed-inputs',action='store_true',help='Experimental producer-held divider operands; requires GPU divider pool')
    args = parser.parse_args()
    if args.gpu_borrowed_inputs and (args.targets not in (['gpu'],['psx_top']) or not args.gpu_divider_pool):
        parser.error('--gpu-borrowed-inputs requires GPU or system alone with --gpu-divider-pool')
    if args.gpu_coordinates and args.targets not in (['gpu'], ['psx_top']):
        parser.error('--gpu-coordinates requires GPU or system alone')
    if args.spu_context_ram and (args.targets not in (['spu'], ['psx_top']) or
                                 (args.targets == ['psx_top'] and not args.reset_only)):
        parser.error('--spu-context-ram requires SPU alone or the reset-only system')
    if args.gpu_timing_prune and args.targets not in (['gpu'],['psx_top']):
        parser.error('--gpu-timing-prune requires GPU or system alone')
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    if args.gpu_divider_pool and args.targets not in (['gpu'],['psx_top']):
        parser.error('--gpu-divider-pool requires GPU or system alone')
    if args.reset_only and args.targets!=['psx_top']:
        parser.error('--reset-only requires --targets psx_top')
    if args.gte_adapter and args.targets not in (['gte'],['psx_top']):
        parser.error('--gte-adapter requires --targets gte or psx_top')
    if args.pocket_controls and args.targets not in (['joypad'],['psx_top']):
        parser.error('--pocket-controls requires --targets joypad or psx_top')
    if args.cd_arithmetic and args.targets not in (['cd_top'],['psx_top']):
        parser.error('--cd-arithmetic requires --targets cd_top or psx_top')
    if args.no_turbo_cache and args.targets not in (['cpu'],['psx_top']):
        parser.error('--no-turbo-cache requires --targets cpu or psx_top')
    if args.gpu_line_share and args.targets not in (['gpu'],['psx_top']):
        parser.error('--gpu-line-share requires --targets gpu or psx_top')
    if args.targets and 'cluster' in args.targets and args.targets!=['cluster']:
        parser.error('Select cluster alone; it already includes all five components')
    if args.targets and 'platform' in args.targets and args.targets!=['platform']:
        parser.error('Select platform alone; PSX integration is not included')
    if args.targets and 'laxer_gte' in args.targets and args.targets!=['laxer_gte']:
        parser.error('Select the alternative GTE alone')
    located = find_tool('quartus_sh')
    binary_dir = args.bin.resolve() if args.bin else Path(located).parent if located else None
    names = ('quartus_sh', 'quartus_map', 'quartus_fit', 'quartus_sta')
    if binary_dir is None or any(not (binary_dir / name).is_file() for name in names):
        parser.error('Quartus Lite with Cyclone V support is required; supply --bin /path/to/quartus/bin.')
    version = subprocess.check_output([str(binary_dir / 'quartus_sh'), '--version'],
                                      text=True, timeout=30).strip()
    output = args.output.resolve()
    runs = output / 'runs'
    runs.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    directory = Path(tempfile.mkdtemp(prefix=started.strftime('%Y%m%dT%H%M%SZ-'), dir=runs))
    manifest = prepare(directory,args.targets)
    if args.pocket_controls:
        from prepare_pocket_controls import apply_pocket_controls
        manifest=apply_pocket_controls(manifest)
    if args.reset_only: manifest=apply_reset_only(manifest)
    if args.gte_adapter:
        from prepare_gte_adapter import apply_gte_adapter
        manifest=apply_gte_adapter(manifest)
    if args.cd_arithmetic:
        from prepare_cd_arithmetic import apply_cd_arithmetic
        manifest=apply_cd_arithmetic(manifest)
    if args.no_turbo_cache:
        from prepare_no_turbo_cache import apply_no_turbo_cache
        manifest=apply_no_turbo_cache(manifest)
    if args.gpu_line_share:
        from prepare_gpu_line_share import apply_gpu_line_share
        manifest=apply_gpu_line_share(manifest)
    if args.gpu_divider_pool:
        from prepare_gpu_divider_pool import apply_gpu_divider_pool
        manifest=apply_gpu_divider_pool(manifest)
    if args.gpu_timing_prune:
        from prepare_gpu_timing import apply_gpu_timing_prune
        manifest=apply_gpu_timing_prune(manifest)
    if args.spu_context_ram:
        from prepare_spu_context_ram import apply_spu_context_ram
        manifest=apply_spu_context_ram(manifest)
    if args.gpu_coordinates:
        from prepare_gpu_coordinates import apply_gpu_coordinates
        manifest=apply_gpu_coordinates(manifest)
    if args.gpu_borrowed_inputs:
        from prepare_gpu_borrowed import apply_gpu_borrowed
        manifest=apply_gpu_borrowed(manifest)
    manifest = apply_compile_profile(manifest,args.profile)
    report = {'tool_version': version, 'device': manifest['device'],
              'started_utc': started.isoformat(), 'run_directory': str(directory),
              'upstream_pin': manifest['upstream_pin'], 'target_games': manifest['target_games'],
              'upstream_project':manifest.get('upstream_project','PSX_MiSTer'),
              'compile_profile':manifest['compile_profile'],'compile_settings':manifest['compile_settings'],
              'reset_only_specialization':manifest.get('reset_only_specialization',False),
              'spu_context_ram':manifest.get('spu_context_ram',False),
              'gpu_coordinates':manifest.get('gpu_coordinates',False),
              'gpu_borrowed':manifest.get('gpu_borrowed',False),
              'gte_adapter':manifest.get('gte_adapter',False),
              'pocket_controls':manifest.get('pocket_controls',False),
              'cd_arithmetic':manifest.get('cd_arithmetic',False),
              'no_turbo_cache':manifest.get('no_turbo_cache',False),
              'gpu_line_share':manifest.get('gpu_line_share',False),
              'gpu_divider_pool':manifest.get('gpu_divider_pool',False),
              'gpu_timing_prune':manifest.get('gpu_timing_prune',False),
              'excluded_components': manifest['excluded_components'],
              'full_core': False, 'hardware_deployable': False, 'board_timing_verified': False,
              'targets': {}}
    failed = False
    report_path = output / 'quartus-report.json'
    for name, target in manifest['targets'].items():
        print(f'Running {name}: {target["directory"]}', flush=True)
        result = fit_target(binary_dir, target, manifest['device'], args.timeout)
        report['targets'][name] = result
        failed |= bool(result['errors']) or not result['fit_verified']
        report['component_resource_runs_completed'] = not failed and len(report['targets']) == len(manifest['targets'])
        contents = json.dumps(report, indent=2) + '\n'
        (directory / 'quartus-report.json').write_text(contents)
        report_path.write_text(contents)
        print(name, result['counts'], 'reported timing',result.get('reported_timing'),
              'errors', result['errors'], flush=True)
    print(f'Resource report: {report_path}', flush=True)
    if failed:
        raise SystemExit('A component resource run failed; see the report and per-target logs.')
    print('Resource fits completed. Complete Pocket PSX execution, fit and board timing remain unverified.')


if __name__=='__main__': main()
