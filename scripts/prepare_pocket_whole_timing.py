#!/usr/bin/env python3
"""Compose the measured whole-core timing repairs before a fresh Native fit.

This applies existing reviewed helpers, not timing-report waivers. Start with
a fresh early Native top; keep area/GPU choices in that caller's base recipe.
External pin/board and game qualification remain separate requirements.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from measure_pocket_seeds import inputs
from prepare_apf_pin_timing import prepare_project as apf_pins
from prepare_host_pause_cdc import prepare_project as host_pause
from prepare_native_sdram_io import prepare_project as native_pins
from prepare_pocket_config_cdc import assignments, scoped_hold_text
from prepare_pocket_audio_stream import refresh_audio_scope
from prepare_pocket_cpu_hold import prepare_project as cpu_hold
from prepare_pocket_status_cdc import prepare_project as status
from prepare_pocket_reset_release import prepare_project as reset_release
from prepare_resource_targets import digest
from prepare_video_reports_cdc import prepare_project as reports
from tool_paths import ROOT


def strict_cd_hold(text):
    """Do not inherit 053eca1's same-clock hold relaxation by source alone.

    Restore only the exact known legacy -13ns line to its stronger 0ns bound.
    A future qualified endpoint-scoped CDC constraint needs its own review;
    this is not a proof that the old bundled-data implementation closes.
    """
    changes = 0
    output = []
    for line in text.splitlines(keepends=True):
        if re.match(r'^\s*set_min_delay\b', line) and 'disc_stream|' in line and ' -to ' not in line:
            sha = hashlib.sha256(line.encode()).hexdigest()
            if sha == '384c29a43d0ae0d0beab551490e397379853d3ab4def0dbf86c76096103bddc7':
                line = line.replace('set_min_delay -13 ', 'set_min_delay 0 ', 1)
                changes += 1
            elif sha != 'bf1ee20eb63e42eae72c1faa9b662c9526bd0d1c6aecbcd908ba7c71faaf9d86':
                raise ValueError('Unknown source-only CD hold scope; require an endpoint-scoped CDC review')
        output.append(line)
    return ''.join(output), changes


def prepare_project(directory, expected_top_sha256, *, cpu_hold_margin_ns=.1):
    directory = Path(directory).resolve()
    if (not directory.is_relative_to(ROOT / 'build') or
            subprocess.run(['git', 'check-ignore', '--quiet', str(directory / 'whole-timing/manifest.json')],
                           cwd=ROOT).returncode):
        raise ValueError('Use a fresh ignored build project')
    if any((directory / name).exists() for name in ('db', 'incremental_db', 'output_files', 'whole-timing')):
        raise ValueError('Whole timing preparation must precede map/fit; use a fresh project')
    qsf = directory / 'apf_top.qsf'
    core = directory / 'core_top.sv'
    if digest(core) != expected_top_sha256:
        raise ValueError('Copied canonical top identity differs')
    assigned = assignments(directory, qsf.read_text())
    def source(name, kind):
        found = [p for k, p in assigned if k == kind and p.name == name]
        if len(found) != 1:
            raise ValueError('Expected one assigned ' + name)
        return found[0]
    if source('core_top.sv', 'SYSTEMVERILOG_FILE') != core:
        raise ValueError('Canonical top is not assigned')
    sdc = (directory / 'pocket.sdc').read_text()
    refreshed_sdc = refresh_audio_scope(sdc)
    audio_scope_refreshed = refreshed_sdc != sdc
    refreshed_sdc, cd_hold_strengthened = strict_cd_hold(refreshed_sdc)
    _, removed = scoped_hold_text(sdc)
    if removed or '--static-host-cuts' in sdc:
        raise ValueError('Regenerate without static-host or clock-family timing cuts')
    # These source/scope pairs must already come from the same current early
    # preparation. Reject stale copied guards instead of hiding their errors.
    video_scope = source('video.sdc', 'SDC_FILE')
    request_scope = source('pocket_video_request_scope.sdc', 'SDC_FILE')
    if (video_scope.read_text() != (ROOT / 'scripts/pocket_video_config_scope.tcl').read_text() or
            request_scope.read_text() != (ROOT / 'scripts/pocket_video_request_scope.tcl').read_text()):
        raise ValueError('Regenerate with current video/request scope helpers')
    audio = source('psx_audio_bridge.sv', 'SYSTEMVERILOG_FILE')
    if digest(audio) != 'c2d8ce98b0badc49b9d4d4933d8a8f353dfa45f66615f8a08be890ade88ba3bc':
        raise ValueError('Whole timing audio Gray proof requires the reviewed bridge source')
    video = source('gpu_videoout_async.vhd', 'VHDL_FILE')
    timer = source('timer.vhd', 'VHDL_FILE')
    controller = source('psx_sdram_ctrl.sv', 'SYSTEMVERILOG_FILE')
    framework = source('apf_top.v', 'VERILOG_FILE')
    # Record the complete starting composition; helpers reject unsupported RTL.
    before = inputs(directory)
    protected = {str(p): digest(p) for p in (video, timer, controller, framework)}
    overlays = {}
    (directory / 'pocket.sdc').write_text(refreshed_sdc)
    overlays['audio_scope_refreshed'] = audio_scope_refreshed
    overlays['CD_source_only_hold_bounds_strengthened'] = cd_hold_strengthened
    overlays['status'] = status(directory, digest(core))
    overlays['SDRAM_pins'] = native_pins(directory, digest(controller),
                                       implementation=True, input_delay_taps=24)
    overlays['ordered_reports_and_timer'] = reports(directory, digest(video), digest(timer))
    overlays['host_pause_and_PAL'] = host_pause(directory, digest(core))
    overlays['APF_pins'] = apf_pins(directory, digest(core), digest(framework),
                                  video_setup_ns=1, video_hold_ns=1,
                                  audio_setup_ns=1, audio_hold_ns=1,
                                  falling_audio_pads=True)
    overlays['reset_release'] = reset_release(directory, digest(core))
    overlays['CPU_hold_reserve'] = cpu_hold(directory, digest(core), margin_ns=cpu_hold_margin_ns)
    if any(digest(Path(p)) != sha for p, sha in protected.items()):
        raise ValueError('An original HDL source changed during whole timing preparation')
    work = directory / 'whole-timing'
    work.mkdir()
    result = dict(before=before, after=inputs(directory), overlays=overlays,
                  preparer_sha256=digest(Path(__file__)),
                  prepare_only=True, map_fit_run=False, hardware_verified=False,
                  game_verified=False, production_selected=False,
                  waiver_script_used=False, additional_CPU_hold_ns=cpu_hold_margin_ns,
                  board_and_receiver_assumptions_qualified=False,
                  required_next_steps=['Freeze every assigned/include/ROM/recursive SDC input before map',
                                       'Fresh full fits and all-corner audits across several seeds',
                                       'PSRAM/bridge electrical and pin-contract qualification',
                                       'Integrated FFVII and exact-candidate Pocket validation'])
    (work / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--expected-top-sha256', required=True)
    parser.add_argument('--cpu-hold-ns', type=float, default=.1,
                        help='Additional physical hold reserve; retains device-derived uncertainty (default .1 ns)')
    args = parser.parse_args()
    result = prepare_project(args.project, args.expected_top_sha256, cpu_hold_margin_ns=args.cpu_hold_ns)
    print(json.dumps(dict(project=str(args.project), prepared=len(result['after']),
                          fit_run=False, hardware_verified=False)))
