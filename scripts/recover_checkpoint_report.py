#!/usr/bin/env python3
"""Record a completed capture whose Python report failed after saving the model.

Require an independently successful continuation of its exact final checkpoint.
Write a separate recovery record; never fabricate a subprocess exit status or
replace a checkpoint sidecar, build manifest, or original report.
"""
import argparse
import json
from pathlib import Path

from prepare_resource_targets import digest


def recover(run, continuation):
    run, continuation = Path(run).resolve(), Path(continuation).resolve()
    destination = run / 'recovered-report.json'
    if destination.exists() or (run / 'report.json').exists():
        raise ValueError('Preserve existing reports')
    resumed_path = continuation / 'report.json'
    resumed = json.loads(resumed_path.read_text())
    if not (resumed['source_and_fixed_input_identity_verified'] and
            resumed['exit_code'] == 0 and not resumed['wall_timeout'] and
            (resumed['bounded_execution_completed'] or resumed['diagnostic_passed'])):
        raise ValueError('Continuation must have completed with verified identity')
    state = run / 'final-state.bin'
    sidecar_path = state.with_suffix('.json')
    sidecar = json.loads(sidecar_path.read_text())
    segment_path = run / 'segment.json'
    segment = json.loads(segment_path.read_text())
    if (Path(resumed['restored_from']).resolve() != state or
            sidecar['checkpoint_sha256'] != digest(state) or
            sidecar['fixed_inputs'] != resumed['fixed_inputs'] or
            sidecar['segment'] != segment or
            resumed['segment']['start_cycle'] != segment['end_cycle'] or
            resumed['segment']['end_cycle'] <= segment['end_cycle']):
        raise ValueError('Final checkpoint, segment, inputs or continuation differ')
    protected = dict(resumed['protected_sha256'])
    fixed = sidecar['fixed_inputs']
    for name, key in (('boot.hex8', 'boot_sha256'),
                      ('expected.hex', 'expected_sha256'),
                      ('trackinfo.hex32', 'disc_metadata_sha256')):
        if key in fixed:
            path = run / name
            if digest(path) != fixed[key]:
                raise ValueError('Original fixed input changed: ' + name)
            protected[str(path)] = fixed[key]
    if not all(digest(Path(path)) == sha for path, sha in protected.items()):
        raise ValueError('Protected continuation source or input changed')
    log = run / 'run.log'
    marker = (f"STOP cycles={segment['end_cycle']} checks={segment['checks']} "
              f"sectors={segment['sectors']}")
    if log.read_text().splitlines()[-1] != marker:
        raise ValueError('Original driver completion marker is missing')
    for path in (state, sidecar_path, segment_path, log, resumed_path):
        protected[str(path)] = digest(path)
    result = {
        'source_and_fixed_input_identity_verified': True,
        'fixed_inputs': fixed, 'protected_sha256': protected, 'segment': segment,
        'recovery': {
            'reason': 'Original Python report serialization failed after final save',
            'validated_continuation': str(resumed_path),
            'final_checkpoint_successfully_deserialized': True,
            'driver_completion_marker_verified': True,
            'original_subprocess_exit_code': None,
            'original_elapsed_seconds': None,
            'original_report_replaced': False,
        },
        'game_verified': False, 'audio_verified': False,
        'fmv_verified': False, 'hardware_verified': False,
    }
    destination.write_text(json.dumps(result, indent=2) + '\n')
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--continuation', type=Path, required=True)
    args = parser.parse_args()
    print(recover(args.run, args.continuation))


if __name__ == '__main__':
    main()
