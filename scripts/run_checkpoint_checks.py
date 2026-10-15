#!/usr/bin/env python3
"""Verify fresh-process continuation against uninterrupted connected execution."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess

from prepare_resource_targets import digest
from tool_paths import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path)
    parser.add_argument('--reuse', action='store_true')
    parser.add_argument('--full-reset', action='store_true')
    parser.add_argument('--candidate', action='store_true',
                        help='Use the existing finite-memory Pocket candidate, preserving the reference build')
    parser.add_argument('--no-disc-lid-closed', action='store_true')
    args = parser.parse_args()
    parent='pocket-checkpoint-checks' if args.candidate else 'checkpoint-checks'
    directory = ROOT / 'build/sim' / parent / 'runs' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory.mkdir(parents=True)
    print(directory,flush=True)
    build_dir = args.build_dir.resolve() if args.build_dir else directory / 'build'
    runner = ROOT / 'scripts' / ('run_pocket_checkpoint.py' if args.candidate else 'run_system_checkpoint.py')
    interval = 100000 if args.full_reset else 10000
    common = ['python3', str(runner), '--build-dir', str(build_dir), '--capture-interval', str(interval)]
    if args.full_reset:
        common.append('--full-reset')
    if args.no_disc_lid_closed:
        common.append('--no-disc-lid-closed')
    bound = '12000000' if args.candidate and args.full_reset else '6000000' if args.full_reset else '2000000'
    continuing_cut = 1000000 if args.full_reset else 10000
    cuts = (50, continuing_cut, 4650123 if args.full_reset else 750123)

    def execute(folder, options, reuse=True):
        command = common + ['--output', str(folder)] + (['--reuse'] if reuse else []) + options
        with (directory / (folder.name + '.log')).open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=900, check=True)
        report = json.loads((folder / 'report.json').read_text())
        if not report['source_and_fixed_input_identity_verified']:
            raise SystemExit('Checkpoint fixture identity changed')
        return report

    baseline = directory / 'baseline'
    reference = execute(baseline, ['--max-cycles', bound, '--checkpoint-at', str(continuing_cut)], args.reuse)
    if not reference['diagnostic_passed']:
        raise SystemExit('Uninterrupted diagnostic failed')
    if args.candidate:
        # Exercise an executing CPU checkpoint near completion, independent
        # of the candidate's finite-memory reset/execution latency.
        # Leave more than one entire capture interval before diagnostic stop;
        # an irregular cut must still reach the baseline's final RAM/VRAM dump.
        cuts=(50,continuing_cut,max(continuing_cut+1,reference['segment']['end_cycle']-interval-70123))
    if json.loads((baseline / 'checkpoint.json').read_text())['segment']['end_cycle'] != continuing_cut:
        raise SystemExit('Continuing execution changed the saved checkpoint cycle')
    cases = []
    last_vram = sorted(baseline.glob('vram-*.hex32'))[-1].name
    for cut in cuts:
        prefix, resumed = directory / f'prefix-{cut}', directory / f'resumed-{cut}'
        # A large bound is parsed and saved without requiring a multi-hour
        # execution merely to exercise the former signed-32-bit CLI limit.
        partial = execute(prefix, ['--max-cycles', '3000000000', '--checkpoint-at', str(cut), '--stop-at-checkpoint'])
        restore_source = baseline / 'checkpoint.bin' if cut == continuing_cut else prefix / 'checkpoint.bin'
        restored = execute(resumed, ['--max-cycles', bound, '--restore', str(restore_source)])
        if not restored['diagnostic_passed'] or restored['segment']['start_cycle'] != cut:
            raise SystemExit('Restored diagnostic failed')
        state_equal = digest(baseline / 'final-state.bin') == digest(resumed / 'final-state.bin')
        outputs = {}
        for name in ('audio.pcm', 'execution.csv'):
            outputs[name] = ((prefix / name).read_bytes() + (resumed / name).read_bytes() ==
                             (baseline / name).read_bytes())
        # Include the actual byte-addressed RAM/SPU and VRAM captures. Audio
        # metadata has a per-segment start offset and is inspected separately.
        for name in ('main-ram.hex32', 'spu-ram.hex32', last_vram):
            outputs[name] = digest(baseline / name) == digest(resumed / name)
        snapshots = sorted(resumed.glob('snapshot-*.csv'))
        inspected = False
        if snapshots:
            stamp = int(snapshots[-1].stem.split('-')[1])
            subprocess.run(['python3', str(ROOT / 'scripts/inspect_system_capture.py'), str(resumed),
                            '--snapshot', str(stamp)], stdout=subprocess.DEVNULL, check=True)
            media = json.loads((resumed / f'media-inspection-{stamp:012d}.json').read_text())
            inspected = media['audio_start_frame'] == partial['segment']['audio_frames']
        if not state_equal or not all(outputs.values()) or not inspected:
            raise SystemExit('Checkpoint replay differs from uninterrupted execution')
        cases.append({'cut_cycle': cut, 'saved_checks': partial['segment']['checks'],
                      'final_state_bytes': (resumed / 'final-state.bin').stat().st_size,
                      'final_state_sha256': digest(resumed / 'final-state.bin'),
                      'complete_serialized_state_equal': state_equal, 'outputs_equal': outputs,
                      'resumed_media_inspection_passed': inspected})

    damaged = directory / 'damaged-checkpoint.bin'
    source = directory / f'prefix-{continuing_cut}/checkpoint.bin'
    shutil.copy2(source, damaged)
    shutil.copy2(source.with_suffix('.json'), damaged.with_suffix('.json'))
    with damaged.open('r+b') as file:
        file.seek(100); value = file.read(1); file.seek(100); file.write(bytes([value[0] ^ 1]))
    rejected_folder = directory / 'damaged-rejected'
    rejection = subprocess.run(common + ['--reuse', '--output', str(rejected_folder), '--restore', str(damaged)],
                               capture_output=True, text=True, timeout=60)
    rejected = rejection.returncode != 0 and 'checkpoint is damaged' in rejection.stderr and not (rejected_folder / 'run.log').exists()
    if not rejected:
        raise SystemExit('Damaged checkpoint was not rejected before execution')
    timeout_recovery = None
    if not args.full_reset:
        interrupted = directory / 'interrupted-after-save'
        interruption = subprocess.run(common + ['--reuse', '--output', str(interrupted), '--max-cycles',
                                               '3000000000', '--checkpoint-at', '50', '--timeout', '1'],
                                      capture_output=True, text=True, timeout=60)
        timed_report = json.loads((interrupted / 'report.json').read_text())
        if interruption.returncode == 0 or not timed_report['wall_timeout'] or not (interrupted / 'checkpoint.json').exists():
            raise SystemExit('Completed checkpoint was not retained after timeout')
        recovery = directory / 'timeout-recovered'
        recovered = execute(recovery, ['--max-cycles', bound, '--restore', str(interrupted / 'checkpoint.bin')])
        timeout_recovery = recovered['diagnostic_passed'] and digest(recovery / 'final-state.bin') == digest(baseline / 'final-state.bin')
        if not timeout_recovery:
            raise SystemExit('Timeout checkpoint recovery differs from uninterrupted execution')
    report = {'passed': True, 'full_reset': args.full_reset, 'candidate':args.candidate,
              'no_disc_lid_closed':args.no_disc_lid_closed,'run_directory': str(directory), 'cases': cases,
              'independent_diagnostic_results': 2648, 'damaged_checkpoint_rejected_before_execution': rejected,
              'completed_checkpoint_retained_after_timeout_and_replayed': timeout_recovery,
              'former_32bit_bound_accepted': True, 'actual_32bit_counter_crossing_verified': False,
              'game_checkpoint_verified': False, 'game_verified': False, 'hardware_verified': False}
    (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
