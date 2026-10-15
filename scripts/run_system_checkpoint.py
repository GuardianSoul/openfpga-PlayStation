#!/usr/bin/env python3
"""Run/resume connected PSX RTL with serialized model, clocks and sector host."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time

from ghdl_verilog import normalize_generated_verilog
from prepare_resource_targets import digest
from prepare_system_verilator import prepare
from psx_disc_host import DiscHost
from reference_cpu_gte_program import write_program
from system_checkpoint_bench import write_bench
from tool_paths import ROOT, find_tool

MAX_CYCLES = (2**64 - 1) // 29_525_100 - 1


def build(directory, fast_reset, fast_boot, reuse, no_disc_lid_closed=False):
    models = ROOT / 'sim/reference/mister_memory_models.sv'
    driver = ROOT / 'sim/reference/psx_checkpoint_main.cpp'
    host = ROOT / 'sim/reference/psx_checkpoint_host.h'
    template = ROOT / 'sim/reference/tb_psx_system_fast.sv'
    generator = ROOT / 'scripts/system_checkpoint_bench.py'
    inputs = (models, driver, host, template, generator,
              ROOT / 'scripts/prepare_system_verilator.py', ROOT / 'scripts/ghdl_verilog.py')
    manifest_path = directory / 'build-manifest.json'
    executable = directory / 'obj/Vpsx_checkpoint'
    if reuse:
        result = json.loads(manifest_path.read_text())
        if result['fast_reset'] != fast_reset or result['fast_boot'] != fast_boot:
            raise ValueError('Recorded reset/boot selection differs')
        if result.get('no_disc_lid_closed', False) != no_disc_lid_closed:
            raise ValueError('Recorded no-disc lid selection differs')
        if result['preparation'].get('reset_header_constant_alias') != 1:
            raise ValueError('Legacy initialized reset-header alias; use a fresh build and boot, not an old checkpoint')
        if not all(digest(Path(path)) == sha for path, sha in result['protected_sha256'].items()):
            raise ValueError('Recorded checkpoint build changed; rebuild required')
        return result, executable
    ghdl, verilator = find_tool('ghdl'), find_tool('verilator')
    if not ghdl or not verilator:
        raise ValueError('GHDL and Verilator required')
    directory.mkdir(parents=True, exist_ok=True)
    prepared = prepare(directory / 'sources', fast_reset=fast_reset, fast_boot=fast_boot)
    bench = write_bench(directory)
    if no_disc_lid_closed:
        text = bench.read_text()
        marker = '.LIDopen(LIDopen)'
        if text.count(marker) != 1:
            raise ValueError('Generated CD lid connection changed')
        bench.write_text(text.replace(marker, ".LIDopen(1'b0)"))
    verilog = directory / 'system.v'
    with (directory / 'build.log').open('w') as log:
        for source in prepared['sources']:
            subprocess.run([ghdl, '-a', '--std=08', '-frelaxed', '--work=' + source['library'], source['path']],
                           cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=60, check=True)
        with verilog.open('w') as out:
            subprocess.run([ghdl, '--synth', '--std=08', '-frelaxed', '--vendor-library=ramip',
                            '--out=verilog', 'psx_mister_psx_top_resource'], cwd=directory,
                           stdout=out, stderr=log, timeout=120, check=True)
        prepared['verilog_normalization'] = normalize_generated_verilog(verilog)
        subprocess.run([verilator, '--cc', '--exe', '--build', '--savable', '--no-timing',
                        '-O3', '-j', '4', '-Wno-fatal', '--output-split', '20000',
                        '--top-module', 'psx_checkpoint', '--Mdir', str(directory / 'obj'),
                        str(verilog), str(models), str(bench), str(driver)], cwd=directory,
                       stdout=log, stderr=subprocess.STDOUT, timeout=300, check=True)
    protected = {str(path): digest(path) for path in (*inputs, bench, verilog, executable)}
    for source in prepared['sources']:
        for key in ('path', 'original', 'selected'):
            if key in source:
                protected[source[key]] = digest(Path(source[key]))
    result = {'preparation': prepared, 'fast_reset': fast_reset, 'fast_boot': fast_boot,
              'no_disc_lid_closed': no_disc_lid_closed,
              'protected_sha256': protected,
              'ghdl_version': subprocess.check_output([ghdl, '--version'], text=True),
              'verilator_version': subprocess.check_output([verilator, '--version'], text=True).strip()}
    manifest_path.write_text(json.dumps(result, indent=2) + '\n')
    return result, executable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bios', type=Path)
    parser.add_argument('--cue', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/sim/system-checkpoint')
    parser.add_argument('--build-dir', type=Path)
    parser.add_argument('--reuse', action='store_true')
    parser.add_argument('--full-reset', action='store_true')
    parser.add_argument('--fastboot', action='store_true')
    parser.add_argument('--no-disc-lid-closed', action='store_true',
                        help='Hold the core lid input closed with no mounted disc (hardware bring-up configuration)')
    parser.add_argument('--max-cycles', type=int, default=2_000_000)
    parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--checkpoint-at', type=int, default=0)
    parser.add_argument('--stop-at-checkpoint', action='store_true')
    parser.add_argument('--restore', type=Path)
    parser.add_argument('--capture-interval', type=int, default=0)
    parser.add_argument('--pad-file', type=Path)
    args = parser.parse_args()
    if not 0 < args.max_cycles <= MAX_CYCLES or args.timeout <= 0:
        parser.error('Execution bounds exceed the positive 64-bit simulation time range')
    if not 0 <= args.checkpoint_at <= args.max_cycles or not 0 <= args.capture_interval <= MAX_CYCLES:
        parser.error('Invalid checkpoint/capture cycle bound')
    if args.stop_at_checkpoint and not args.checkpoint_at:
        parser.error('Stopping at a checkpoint requires --checkpoint-at')
    if args.cue and not args.bios:
        parser.error('Disc execution requires a local BIOS')
    if args.no_disc_lid_closed and args.cue:
        parser.error('The closed-lid bring-up selection requires no disc')
    directory = args.output.resolve()
    build_dir = args.build_dir.resolve() if args.build_dir else directory / 'build'
    for folder in (directory, build_dir):
        if folder.is_relative_to(ROOT) and subprocess.run(
                ['git', 'check-ignore', '--quiet', str(folder / 'boot.hex8')], cwd=ROOT).returncode:
            parser.error('Simulation inputs, checkpoints and outputs must use a Git-ignored directory')
        folder.mkdir(parents=True, exist_ok=True)
    if any((directory / name).exists() for name in ('report.json', 'checkpoint.bin', 'run.log')):
        parser.error('Preserve completed captures; choose a fresh output directory')
    # A checkpoint binds the executable and all fixed local inputs before any
    # model data is deserialized. Mutable pad events have a separate trace.
    built, executable = build(build_dir, not (args.full_reset or args.bios is not None),
                              args.fastboot, args.reuse, args.no_disc_lid_closed)
    program = write_program(directory / 'diagnostic', memory_checks=True)
    if args.bios:
        bios = args.bios.read_bytes()
        if len(bios) != 524288:
            parser.error('BIOS must be exactly 512 KiB')
    else:
        bios = b''.join(int(line, 16).to_bytes(4, 'little') for line in program['program'].read_text().splitlines())
    boot = directory / 'boot.hex8'
    boot.write_text(''.join(f'{value:02x}\n' for value in bios))
    expected = directory / 'expected.hex'
    shutil.copy2(program['expected'], expected)
    fixed = {'boot_sha256': digest(boot), 'expected_sha256': digest(expected),
             'executable_sha256': digest(executable),
             'build_manifest_sha256': digest(build_dir / 'build-manifest.json')}
    disc = None
    if args.cue:
        with DiscHost(args.cue) as host:
            disc = host.prepare(directory)
        fixed.update(disc_image_sha256=digest(Path(disc['image'])),
                     disc_metadata_sha256=digest(Path(disc['metadata'])), cue_sha256=digest(args.cue))
    if args.restore:
        sidecar = json.loads(args.restore.with_suffix('.json').read_text())
        if sidecar['fixed_inputs'] != fixed or sidecar['checkpoint_sha256'] != digest(args.restore):
            parser.error('Checkpoint build/input identity differs or checkpoint is damaged')
        resume_cycle = sidecar['segment']['end_cycle']
        if args.max_cycles <= resume_cycle or (args.checkpoint_at and args.checkpoint_at <= resume_cycle):
            parser.error('Resume and checkpoint bounds must advance beyond the saved cycle')
    protected = dict(built['protected_sha256'])
    protected.update({str(boot): digest(boot), str(expected): digest(expected)})
    protected[str(build_dir / 'build-manifest.json')] = fixed['build_manifest_sha256']
    for path in (args.bios, args.cue, args.restore):
        if path:
            protected[str(path.resolve())] = digest(path)
    if disc:
        for key in ('image', 'metadata'):
            protected[str(disc[key])] = digest(Path(disc[key]))
    command = [str(executable), f'+diagnostic={int(args.bios is None)}',
               f'+max_cycles={args.max_cycles}', f'+checkpoint_at={args.checkpoint_at}',
               f'+capture_interval={args.capture_interval}', f'+stop_at_checkpoint={int(args.stop_at_checkpoint)}']
    if args.restore:
        command.append('+restore=' + str(args.restore.resolve()))
    if args.pad_file:
        command.append('+pad_file=' + str(args.pad_file.resolve()))
    if disc:
        command.extend([f'+disc_file={disc["image"]}', f'+disc_metadata={disc["metadata"]}',
                        f'+disc_sectors={disc["sectors"]}', f'+disc_index={disc["index_sector"]}'])
    print('Running serializable connected PSX model.', flush=True)
    start = time.monotonic()
    timed_out = False
    with (directory / 'run.log').open('w') as log:
        try:
            code = subprocess.run(command, cwd=directory, stdout=log, stderr=subprocess.STDOUT,
                                  timeout=args.timeout).returncode
        except subprocess.TimeoutExpired:
            timed_out, code = True, None
    identity = all(digest(Path(path)) == sha for path, sha in protected.items())
    segment_path = directory / 'segment.json'
    segment = json.loads(segment_path.read_text()) if segment_path.exists() else {}
    checkpoint = directory / 'checkpoint.bin'
    checkpoint_segment_path = directory / 'checkpoint-segment.json'
    checkpoint_segment = None
    if checkpoint.exists() and checkpoint_segment_path.exists():
        try:
            checkpoint_segment = json.loads(checkpoint_segment_path.read_text())
        except json.JSONDecodeError:
            pass
    if identity:
        for state in (checkpoint, directory / 'final-state.bin'):
            if state.exists():
                # The driver closes the model stream before writing its marker.
                # Keep that completed save even if later execution times out.
                saved_segment = checkpoint_segment if state == checkpoint else segment if code == 0 else None
                if saved_segment is None:
                    continue
                state.with_suffix('.json').write_text(json.dumps(
                    {'fixed_inputs': fixed, 'checkpoint_sha256': digest(state), 'segment': saved_segment}, indent=2) + '\n')
    diagnostic_passed = not args.bios and code == 0 and segment.get('checks') == 2648 and segment.get('diagnostic_done') == 1
    completed_bound = code == 0 and segment.get('end_cycle', -1) >= args.max_cycles
    completed_checkpoint = (code == 0 and args.stop_at_checkpoint and checkpoint_segment is not None and
                            segment.get('end_cycle', -1) == checkpoint_segment['end_cycle'])
    report = {'source_and_fixed_input_identity_verified': identity, 'fixed_inputs': fixed,
              'protected_sha256': protected, 'segment': segment, 'exit_code': code,
              'wall_timeout': timed_out, 'elapsed_seconds': time.monotonic() - start,
              'restored_from': str(args.restore.resolve()) if args.restore else None,
              'diagnostic_passed': diagnostic_passed, 'bounded_execution_completed': completed_bound,
              'requested_checkpoint_stop_completed': completed_checkpoint,
              'game_verified': False, 'audio_verified': False, 'fmv_verified': False, 'hardware_verified': False}
    (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('segment', 'exit_code', 'elapsed_seconds',
                                                 'source_and_fixed_input_identity_verified')}, indent=2))
    if timed_out or code or not identity or not (diagnostic_passed or completed_bound or completed_checkpoint):
        raise SystemExit('Checkpoint run failed; see its run.log')


if __name__ == '__main__':
    main()
