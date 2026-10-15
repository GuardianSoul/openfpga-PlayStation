#!/usr/bin/env python3
"""Burst memory probe with on-screen diagnostics (PSXDiagProbe).

The burst probe shows one combined status box. On hardware it showed red, and
its detail lives behind APF debug reads that a Pocket user cannot make. This
variant keeps the probe logic and draws each status and the result words on
screen (psx_diag_pattern.sv), so a photograph identifies the failure.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile

from build_pocket import source
from build_pocket_burst_probe import prepare as prepare_burst
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool

WORDS = ('{14\'b0,loaded_words}', '{14\'b0,checked_words}', 'write_crc ^ 32\'hffffffff',
    'read_crc ^ 32\'hffffffff', '{14\'b0,ps_checked_sync}', '{9\'b0,ps_failed_addr_sync}',
    'ps_failed_expected_sync', 'ps_failed_actual_sync')


def prepare(directory):
    manifest = prepare_burst(directory)
    core = directory/'core_top.sv'; text = core.read_text()
    old = '''    psx_test_pattern picture (.clk(video_rgb_clock), .reset(!video_locked),
        .passed(all_passed), .failed(any_failed || mem_protocol_error), .checking(any_checking),
        .progress(bios_checking ? checked_words : loaded_words),
        .rgb(video_rgb), .de(video_de), .hs(video_hs), .vs(video_vs));
'''
    words = ', '.join(reversed(WORDS))
    new = f'''    // Segments: BIOS readback, PSRAM test, SDRAM controller, PSRAM controller.
    psx_diag_pattern picture (.clk(video_rgb_clock), .reset(!video_locked),
        .status_failed({{ps_wait_error, mem_protocol_error, ps_flags_sync[2], bios_failed}}),
        .status_passed({{ps_initialized, mem_initialized && !mem_protocol_error, ps_flags_sync[1], bios_passed}}),
        .words({{{words}}}),
        .rgb(video_rgb), .de(video_de), .hs(video_hs), .vs(video_vs));
'''
    if text.count(old) != 1: raise ValueError('Burst probe picture instance changed')
    core.write_text(text.replace(old, new))
    pattern = source(ROOT/'rtl/pocket/psx_diag_pattern.sv')
    sources = [source(core) if Path(s['path']) == core else s for s in manifest['sources']] + [pattern]
    qsf = directory/'apf_top.qsf'
    qsf.write_text(qsf.read_text()+f'set_global_assignment -name SYSTEMVERILOG_FILE "{pattern["path"]}" -library work\n')
    sdc = directory/'pocket.sdc'
    sdc.write_text(sdc.read_text()+'''
# Display-only samples of held results and status flags.
set_false_path -to [get_registers {*|picture|flags_meta[*] *|picture|words_meta[*]}]
''')
    manifest.update(kind='bios-psram-diag-probe', sources=sources,
        project_hashes={n: digest(directory/n) for n in ('apf_top.qsf', 'apf_top.qpf', 'pocket.sdc', 'apf/build_id.mif')})
    (directory/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest


def package(project, out):
    import re, shutil, zipfile
    fit = (project/'output_files/apf_top.fit.summary').read_text()
    if 'Fitter Status : Successful' not in fit or 'Total virtual pins : 0' not in fit:
        raise SystemExit('Successful physical fit required')
    timing = (project/'output_files/apf_top.sta.summary').read_text()
    slacks = {k: float(v) for k, v in re.findall(r'Type\s*:\s*(.*?)\nSlack\s*:\s*(-?[\d.]+)', timing)}
    if not slacks or min(slacks.values()) < 0: raise SystemExit('Reported timing violations remain')
    if out.exists(): shutil.rmtree(out)
    sd = out/'SD'; core = sd/'Cores/GuardianSoul.PSXDiagProbe'; core.mkdir(parents=True)
    original = (project/'output_files/apf_top.rbf').read_bytes()
    table = bytes(int(f'{b:08b}'[::-1], 2) for b in range(256))
    (core/'bitstream.rbf_r').write_bytes(original.translate(table))
    def write(name, value): (core/name).write_text(json.dumps(value, indent=2)+'\n')
    write('core.json', {'core': {'magic': 'APF_VER_1', 'metadata': {
        'platform_ids': ['psxdiagprobe'], 'shortname': 'PSXDiagProbe',
        'description': 'BIOS SDRAM and PSRAM probe with on-screen results',
        'author': 'GuardianSoul', 'url': 'https://github.com/GuardianSoul/openfpga-PlayStation', 'version': '0.1.0',
        'date_release': '2026-10-14'},
        'framework': {'target_product': 'Analogue Pocket', 'version_required': '1.1', 'sleep_supported': False,
            'dock': {'supported': True, 'analog_output': False}, 'hardware': {'link_port': False, 'cartridge_adapter': -1}},
        'cores': [{'name': 'default', 'id': 0, 'filename': 'bitstream.rbf_r'}]}})
    # Same slot as the burst probe it derives from.
    write('data.json', {'data': {'magic': 'APF_VER_1', 'data_slots': [{
        'name': 'BIOS', 'id': '0x20', 'required': True, 'parameters': '0x008', 'deferload': False,
        'filename': 'bios.bin', 'extensions': ['bin'], 'size_exact': 524288, 'address': '0x00000000'}]}})
    write('video.json', {'video': {'magic': 'APF_VER_1', 'scaler_modes': [{
        'width': 640, 'height': 480, 'aspect_w': 4, 'aspect_h': 3, 'rotation': 0, 'mirror': 0}]}})
    for key, field in [('input', 'controllers'), ('interact', 'variables'), ('variants', 'variant_list')]:
        body = {'magic': 'APF_VER_1', field: []}
        if key == 'interact': body['messages'] = []
        write(key+'.json', {key: body})
    write('audio.json', {'audio': {'magic': 'APF_VER_1'}})
    (core/'info.txt').write_text('Memory probe with on-screen results. Photograph the screen.\n')
    assets = sd/'Assets/psxdiagprobe/common'; assets.mkdir(parents=True)
    (assets/'PLACE-BIOS-HERE.txt').write_text('Place your own 524288-byte PSX BIOS dump here as bios.bin.\n')
    (sd/'Platforms').mkdir()
    (sd/'Platforms/psxdiagprobe.json').write_text(json.dumps({'platform': {
        'category': 'Utility', 'name': 'PSX Diagnostic Memory Probe', 'year': 2026, 'manufacturer': 'GuardianSoul'}}, indent=2)+'\n')
    notices = core/'licenses'; notices.mkdir()
    for src in (ROOT/'THIRD_PARTY_NOTICES.md', ROOT/'references/MIRLO64/LICENSE', ROOT/'licenses/LiteX-BSD-2-Clause.txt'):
        if src.is_file(): shutil.copyfile(src, notices/(src.parent.name+'-'+src.name if src.name == 'LICENSE' else src.name))
    (out/'build-report.json').write_text(json.dumps(dict(project=str(project), slacks=slacks,
        rbf_sha256=digest(project/'output_files/apf_top.rbf'), hardware_verified=False), indent=2)+'\n')
    with zipfile.ZipFile(out/'PSXDiagProbe.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(sd.rglob('*')):
            if path.is_file(): archive.write(path, path.relative_to(sd))
        archive.write(ROOT/'docs/pocket-diag-probe.md', 'PSXDiagProbe-INSTALL.md')
    return out/'PSXDiagProbe.zip'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'build/pocket/diag-probe')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    runs = args.output.resolve()/'runs'; runs.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=runs))
    manifest = prepare(directory); print(directory, flush=True)
    if args.prepare_only: return
    binary = Path(find_tool('quartus_sh')).parent
    with (directory/'quartus.log').open('w') as log:
        for tool in ('quartus_map', 'quartus_fit', 'quartus_sta', 'quartus_asm'):
            result = subprocess.run([str(binary/tool), 'apf_top'], cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
            print(tool, result.returncode, flush=True)
            if result.returncode: raise SystemExit(result.returncode)
    for s in manifest['sources']:
        if digest(Path(s['path'])) != s['sha256']: raise ValueError('Compiled source changed: '+s['path'])
    print(package(directory, args.output.resolve()/'package'))


if __name__ == '__main__': main()
