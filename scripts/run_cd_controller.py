#!/usr/bin/env python3
"""Check the converted pinned CD controller against authored Mode 2 sectors."""
import json
from pathlib import Path
import re
import subprocess
import sys

from prepare_resource_targets import digest
from prepare_system_verilator import prepare
from psx_disc_host import DiscHost, bcd, multitrack_metadata_words
from system_disc_bench import cd_response_trace
from tool_paths import ROOT, find_tool


def synthetic_sector(lba):
    minute, remaining = divmod(lba + 150, 4500)
    second, frame = divmod(remaining, 75)
    header = bytes([0] + [255] * 10 + [0, bcd(minute), bcd(second), bcd(frame), 2])
    subheader = bytes([1, 0, 8, 0]) * 2
    payload = bytes((i * 29 + lba * 101) & 255 for i in range(2048))
    # EDC/ECC are zero: this fixture checks the host/controller FIFO path,
    # not checksum validation or a retail dump.
    return header + subheader + payload + bytes(280)


# --apf replaces the reference host with the Pocket SD loader
# (rtl/pocket/psx_cd_stream.sv) behind the pinned APF command handler.
APF_HOST = [ROOT / 'sim/pocket/psx_disc_host_apf.sv', ROOT / 'rtl/pocket/psx_cd_stream.sv',
            ROOT / 'references/MIRLO64/target/pocket/core_bridge_cmd.v',
            ROOT / 'references/MIRLO64/platform/pocket/common.v', ROOT / 'sim/pocket/mf_datatable_model.sv']


# --multi: the track table psx_cd_stream writes for a three-file disc (data
# track of 12 sectors, audio files of 160 and 155 sectors with their pregaps);
# the bench checks GetTN and GetTD against it (reference host only).
MULTI_TRACKS = [12, 160, 155]
# GetTD answers: track 0 = disc end, tracks 1-3 = INDEX 01 (MM, SS), BCD.
MULTI_GETTD = {0: (0x00, 0x06), 1: (0x00, 0x02), 2: (0x00, 0x04), 3: (0x00, 0x06)}
MULTI_CHECKS = '''
 command(8'h13);wait_irq(3);response(value);response(value);if(value!=1) $fatal(1,"Multi first track wrong");checks++;
 response(value);if(value!=3) $fatal(1,"Multi track count %02x",value);checks++;ack_irq();
 GETTD_CHECKS'''


def main(apf='--apf' in sys.argv[1:], multi='--multi' in sys.argv[1:]):
    if apf and multi: raise SystemExit('--multi uses the reference host')
    ghdl = find_tool('ghdl')
    verilator = find_tool('verilator') or str(ROOT / 'build/tools/oss-cad-suite/bin/verilator')
    if not ghdl or not Path(verilator).is_file():
        raise SystemExit('GHDL synthesis and Verilator are required')
    directory = ROOT / ('build/sim/cd-controller-apf' if apf else 'build/sim/cd-controller-multi' if multi else 'build/sim/cd-controller-checked')
    directory.mkdir(parents=True, exist_ok=True)
    image = directory / 'synthetic.bin'
    image.write_bytes(b''.join(synthetic_sector(lba) for lba in range(12)))
    cue = directory / 'synthetic.cue'
    cue.write_text('FILE "synthetic.bin" BINARY\nTRACK 01 MODE2/2352\nINDEX 01 00:00:00\n')
    with DiscHost(cue) as host:
        disc = host.prepare(directory)
    if multi:
        words = multitrack_metadata_words(MULTI_TRACKS, region=2)
        disc['metadata'].write_text(''.join(f'{w:08x}\n' for w in words))
    manifest = prepare(directory / 'sources')
    # Synthesize only the CD entity. Its converted RAM dependencies are opaque
    # and use the same separately validated portable RAM models as the system.
    verilog = directory / 'cd.v'
    template = ROOT / 'sim/reference/tb_cd_controller.sv'
    bench = directory / 'tb_cd_controller.sv'
    models = ROOT / 'sim/reference/mister_memory_models.sv'
    host_rtl = APF_HOST if apf else [ROOT / 'sim/reference/psx_disc_host.sv']
    executable = directory / 'obj/Vtb_cd_controller'
    with (directory / 'build.log').open('w') as log:
        for source in manifest['sources']:
            subprocess.run([ghdl, '-a', '--std=08', '-frelaxed', f'--work={source["library"]}', source['path']],
                           cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=60, check=True)
        with verilog.open('w') as output:
            subprocess.run([ghdl, '--synth', '--std=08', '-frelaxed', '--vendor-library=ramip',
                            '--out=verilog', 'cd_top'], cwd=directory, stdout=output, stderr=log, timeout=120, check=True)
        from ghdl_verilog import normalize_generated_verilog
        normalization = normalize_generated_verilog(verilog)
        converted = verilog.read_text()
        matches = re.findall(r'^module (cd_top(?:_Barch[^\s]*)?)\s*\n', converted, re.M)
        if len(matches) != 1:
            raise SystemExit('Expected exactly one CD controller entity')
        header = converted[converted.index('module ' + matches[0]):].split(');', 1)[0]
        ports = {name.lower(): name for name in re.findall(r'\b(?:input|output)\s+(?:\[\d+:\d+\]\s+)?(\w+)', header)}
        text = template.read_text()
        # Top-level GHDL ports retain VHDL spelling; nested entities can be
        # lowercased. Bind the same exact port set under either form.
        instance = re.search(r'CD_ENTITY cd\([^\n]+\);', text).group()
        bound = re.sub(r'\.(\w+)\(', lambda m: '.' + ports[m[1].lower()] + '(', instance)
        text = text.replace(instance, bound).replace('CD_ENTITY', matches[0])
        marker='value=bus_dataread;bus_read=0;'
        if text.count(marker)!=1:raise ValueError('CD response task changed')
        text=text.replace(marker,'value=bus_dataread;if(address==1) $fwrite(expected_responses,"%02x\\n",value);bus_read=0;')
        observer='''integer response_trace,expected_responses;
initial begin response_trace=$fopen("responses-observed.csv","w");expected_responses=$fopen("responses-expected.hex8","w");end
final begin $fclose(response_trace);$fclose(expected_responses);end
'''+cd_response_trace('clk1x','bus_read','bus_addr','bus_dataread','cycles','response_trace')
        text=text.replace('endmodule',observer+'endmodule')
        if multi:
            # Replace the single-track GetTN check with three-track GetTN/GetTD.
            single='''command(8'h13);wait_irq(3);response(value);response(value);if(value!=1) $fatal(1,"First track wrong");checks++;
 response(value);if(value!=1) $fatal(1,"Track count wrong");checks++;ack_irq();
'''
            if text.count(single)!=1: raise ValueError('GetTN check changed')
            gettd=''.join(f""" write_reg(0,0);write_reg(2,8'h{track:02x});command(8'h14);wait_irq(3);response(value);
 response(value);if(value!=8'h{mm:02x}) $fatal(1,"GetTD {track} minutes %02x",value);checks++;
 response(value);if(value!=8'h{ss:02x}) $fatal(1,"GetTD {track} seconds %02x",value);checks++;ack_irq();
""" for track,(mm,ss) in MULTI_GETTD.items())
            text=text.replace(single,MULTI_CHECKS.replace('GETTD_CHECKS',gettd).lstrip(' ')+' ')
            text=text.replace('checks!=2054','checks!=2062')
        bench.write_text(text)
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', '4', '-Wno-fatal',
                        '--top-module', 'tb_cd_controller', '--Mdir', str(directory / 'obj'),
                        str(verilog), str(models), *map(str, host_rtl), str(bench)],
                       cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=300, check=True)
    protected = {str(path): digest(path) for path in
                 (image, cue, disc['metadata'], verilog, template, bench, models, *host_rtl, executable)}
    result = subprocess.run([str(executable), f'+disc_file={image}', f'+disc_metadata={disc["metadata"]}',
                             '+disc_index=0', '+disc_sectors=12'], cwd=directory,
                            capture_output=True, text=True, timeout=90)
    output = result.stdout + result.stderr
    (directory / 'run.log').write_text(output)
    print(output, end='', flush=True)
    identity = all(digest(Path(path)) == sha for path, sha in protected.items())
    identity &= all(digest(Path(s['path'])) == s['sha256'] and
                    digest(Path(s['original'])) == s['original_sha256'] and
                    ('selected' not in s or digest(Path(s['selected'])) == s['selected_sha256'])
                    for s in manifest['sources'])
    matched = re.search(r'PASS CD controller commands=\s*%d checks=\s*%d sectors=\s*(\d+) cycles=\s*(\d+)'
                        % ((11, 2062) if multi else (7, 2054)), output)
    expected_responses=[int(line,16) for line in (directory/'responses-expected.hex8').read_text().splitlines()]
    observed_responses=[int(line.split(',')[-1],16) for line in (directory/'responses-observed.csv').read_text().splitlines()]
    observer_passed=len(expected_responses)==(32 if multi else 20) and expected_responses==observed_responses
    passed = result.returncode == 0 and identity and bool(matched) and observer_passed
    report = {'preparation': manifest, 'verilog_normalization': normalization,
              'protected_sha256': protected, 'source_and_input_identity_verified': identity,
              'ghdl_version': subprocess.check_output([ghdl, '--version'], text=True),
              'verilator_version': subprocess.check_output([verilator, '--version'], text=True).strip(),
              'passed': passed, 'exit_code': result.returncode, 'stdout': output,
              'commands': ['Init', 'GetTN', *(['GetTD']*4 if multi else []), 'GetID', 'SetMode', 'SetLoc', 'ReadN', 'Pause'],
              'checks': 2062 if multi else 2054, 'track_table': 'multitrack_metadata_words(%s)' % MULTI_TRACKS if multi else 'single track', 'sectors_delivered': int(matched[1]) if matched else None,
              'cycles': int(matched[2]) if matched else None, 'instant_seek': True,
              'sector_host': 'APF data slot loader psx_cd_stream' if apf else 'reference simulation host',
              'response_observer_verified':observer_passed,'response_observer_comparisons':len(expected_responses),
              'checksum_validation_verified': False, 'dma_verified': False, 'xa_audio_verified': False,
              'game_verified': False, 'hardware_verified': False}
    (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    if not passed:
        raise SystemExit(f'CD controller checks failed; see {directory}/run.log')


if __name__ == '__main__':
    main()
